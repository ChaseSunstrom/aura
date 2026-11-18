package ai.aura.app.car.holo

import ai.aura.app.assist.ModelStore
import ai.aura.app.config.JarvisConfig
import ai.aura.app.config.ServerUrl
import ai.aura.app.tailnet.JarvisHttp
import android.content.Context
import android.os.StatFs
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.TimeUnit

/**
 * Keeps the phone's copies of the house's map packs (T-1017): the ready ones, each through a
 * `<id>.<built>.pack.part` and a rename, so [MapPacks] never opens a pack that is half there. A part a
 * stopped copy left is carried on with a range ask; a pack the house rebuilt (its `built` or
 * `bytes` differs) is replaced; one the house no longer lists is deleted; one that does not fit
 * in the free space less [RESERVE_BYTES] is left and said in the log.
 *
 * Nothing is installed that was not checked: the size is the house's `bytes`, the file's sha256
 * is the house's `sha256` (T-1024), and it must open as a pack of the format with the house's
 * `built`. What does not check out is thrown away and the pack in use stays. A row with no
 * usable `sha256` (an older house) is not copied, as [ModelStore] refuses a model with no digest.
 * A house that does not answer changes nothing: no deletes.
 *
 * Not covered: a download is not resumed within one run (the next run does it); two runs at
 * once on one folder (WorkManager's unique work keeps to one); a pack already in place is kept
 * on its size and `built`, not hashed again (a rebuild changes `built`); the hash comes from
 * the house that serves the bytes, so it catches a cut or a flipped block, not a lying house.
 */
class MapPackSync(
    private val dir: File,
    private val house: House,
    private val freeBytes: () -> Long,
    private val cancelled: () -> Boolean,
) {
    /** One row of the house's list; only a `ready` one has a pack to copy. */
    class Region(val id: String, val state: String, val bytes: Long, val built: Long, val sha256: String = "", val auto: Boolean = false)

    /** The body of a pack call: [partial] when the house gave only what was asked from, else all of it. */
    class Download(val partial: Boolean, val body: InputStream)

    interface House {
        /** The house's regions, or `null` when it did not answer: nothing is then dropped. */
        fun regions(): List<Region>?

        /** The pack of [id] from byte [from] on, or all of it. Throws [IOException] when there is none. */
        fun pack(id: String, from: Long): Download
    }

    data class Result(val copied: Int = 0, val kept: Int = 0, val dropped: Int = 0, val skipped: Int = 0, val failed: Int = 0)

    private class Stopped : IOException("stopped")

    fun run(): Result {
        val rows = house.regions() ?: return Result()
        val named = rows.filter { validId(it.id) }
        // T-1074: a region the house fetched on its own (a trip, a drive through) is copied only
        // while those fit in [AUTO_BYTES], newest first; the rest are drawn by the tile
        // ([MapTiles]) and their copies here are dropped, so the phone does not grow a state at a
        // time. Home and what the owner asked for by name are always copied.
        var autos = 0L
        val over = named.filter { it.auto }.sortedByDescending { it.built }.filter { r ->
            autos += r.bytes.coerceAtLeast(0L)
            autos > AUTO_BYTES
        }.map { it.id }.toSet()
        val listed = named.filter { it.id !in over }
        // Nothing is dropped on a list the phone cannot trust whole: an empty one (a house that
        // lost its packs, a proxy's blank page) or one with a row it cannot name would otherwise
        // delete packs the house still has.
        val dropped = if (named.isNotEmpty() && named.size == rows.size) drop(listed.map { it.id }.toSet()) else 0
        var copied = 0
        var kept = 0
        var skipped = 0
        var failed = 0
        for (r in listed) {
            if (cancelled()) break
            // A row the phone cannot check a copy against is not a pack to take.
            if (r.state != READY || r.bytes <= 0 || r.built <= 0 || !SHA256.matches(r.sha256)) continue
            try {
                when (one(r)) {
                    Step.COPIED -> copied++
                    Step.KEPT -> kept++
                    Step.SKIPPED -> skipped++
                }
            } catch (e: Stopped) {
                break
            } catch (e: Exception) {
                // One region's failure (a dead link, a bad pack) is not the others'.
                failed++
                Log.w(TAG, "pack ${r.id} not copied: ${e.message}")
            }
        }
        val out = Result(copied, kept, dropped, skipped, failed)
        Log.i(TAG, "packs: $out")
        return out
    }

    private enum class Step { COPIED, KEPT, SKIPPED }

    private fun one(r: Region): Step {
        val local = File(dir, "${r.id}.pack")
        // The build is in the name: a part of an older build is not a prefix of this one, and
        // resuming it would splice two packs (the post-copy check would then throw the whole away).
        val part = File(dir, "${r.id}.${r.built}.pack.part")
        if (local.isFile && local.length() == r.bytes && builtOf(local) == r.built) return Step.KEPT
        dir.mkdirs()
        for (f in dir.listFiles().orEmpty()) {
            if (f != part && f.isFile && f.name.endsWith(".part") && FILE.matchEntire(f.name)?.groupValues?.get(1) == r.id) f.delete()
        }
        // A part that cannot be a prefix of this pack is not resumed.
        if (part.exists() && (!part.isFile || part.length() >= r.bytes)) part.delete()
        val have = if (part.isFile) part.length() else 0L
        val need = r.bytes - have
        if (freeBytes() - RESERVE_BYTES < need) {
            Log.w(TAG, "pack ${r.id} left: not enough space (needs $need bytes beyond a $RESERVE_BYTES reserve)")
            return Step.SKIPPED
        }
        return copy(r, part, local, have)
    }

    private fun copy(r: Region, part: File, local: File, have: Long): Step {
        val dl = house.pack(r.id, have)
        dl.body.use { body ->
            val resume = dl.partial && have > 0
            var written = if (resume) have else 0L
            FileOutputStream(part, resume).use { out ->
                val buf = ByteArray(BUFFER)
                while (true) {
                    if (cancelled()) throw Stopped()
                    // One byte past the size says "more than listed" without reading all of it.
                    val n = body.read(buf, 0, minOf(buf.size.toLong(), r.bytes - written + 1).toInt())
                    if (n < 0) break
                    written += n
                    if (written > r.bytes) {
                        out.close()
                        part.delete()
                        throw IOException("the house sent more than the ${r.bytes} it listed")
                    }
                    out.write(buf, 0, n)
                }
                // On disk before the rename: a power cut after it must not leave a pack named
                // for bytes that were never written.
                out.fd.sync()
            }
            if (written != r.bytes) throw IOException("the copy ended at $written of ${r.bytes} bytes")
        }
        // The whole file, after a resume too: a part spliced from two sends is caught here.
        val problem = ModelStore.digestProblem(r.id, r.sha256, ModelStore.sha256(part))
        if (problem != null) {
            part.delete()
            throw IOException("the copy's hash is not the one the house listed: $problem")
        }
        if (builtOf(part) != r.built) {
            part.delete()
            throw IOException("the copy is not the build the house listed")
        }
        if (!part.renameTo(local)) throw IOException("the copy could not be put in place")
        return Step.COPIED
    }

    /** When the pack in [file] was built, or `null` when it is not a pack this reads. */
    private fun builtOf(file: File): Long? = MapPack.open(file)?.let { p -> p.built.also { p.close() } }

    /** Deletes `<id>.pack` and `<id>.pack.part` of an id the house does not list; no other file. */
    private fun drop(listed: Set<String>): Int {
        var n = 0
        for (f in dir.listFiles().orEmpty()) {
            val id = FILE.matchEntire(f.name)?.groupValues?.get(1) ?: continue
            if (id !in listed && f.isFile && f.delete()) n++
        }
        return n
    }

    companion object {
        /** What the phone keeps free whatever it copies: a map is not worth a full phone. */
        const val RESERVE_BYTES = 500L * 1024 * 1024

        /** What the regions the house fetched on its own may take here: a trip's state or two, not every one driven through. */
        const val AUTO_BYTES = 1L shl 30

        private const val READY = "ready"
        private const val BUFFER = 64 * 1024
        private const val TAG = "MapPackSync"
        private val ID = Regex("^[a-z0-9][a-z0-9-]{0,63}$")
        /** `tests/contracts/map-pack.json`'s `sha256.pattern`, in either case: hex is hex. */
        private val SHA256 = Regex("^[0-9a-fA-F]{64}$")
        // Group 1 is the id: `<id>.pack`, `<id>.<built>.pack.part`, or an older build's `<id>.pack.part`.
        private val FILE = Regex("^([a-z0-9][a-z0-9-]{0,63})(?:\\.pack|(?:\\.[0-9]{1,19})?\\.pack\\.part)$")

        /** `tests/contracts/map-pack.json`'s `id`: the only text that becomes a file name here. */
        fun validId(id: String) = ID.matches(id)
    }
}

/**
 * The house's pack calls (`GET /api/maps/regions`, `GET /api/maps/packs/{id}`, the paths of
 * `tests/contracts/map-pack.json`), bearer-authenticated like [HouseMaps]. Blocking: the
 * worker's thread calls it.
 *
 * Not covered: a body is not size-checked here ([MapPackSync] caps what it takes).
 */
class HousePacks(
    base: String,
    private val token: String,
    private val http: OkHttpClient = DEFAULT,
) : MapPackSync.House {
    private val base = ServerUrl.normalize(base)

    override fun regions(): List<MapPackSync.Region>? = try {
        http.newCall(request("/api/maps/regions").get().build()).execute().use { r ->
            if (!r.isSuccessful) return null
            val source = r.body!!.source()
            if (source.request(MAX_LIST + 1)) return null
            val arr = JSONObject(source.buffer.readUtf8()).optJSONArray("regions") ?: return null
            // A row that does not read makes the whole list unread: a half-read list would drop packs the house has.
            (0 until arr.length()).map { i ->
                val o = arr.optJSONObject(i) ?: return null
                val id = o.opt("id") as? String ?: return null
                MapPackSync.Region(id, o.optString("state", ""), o.optLong("bytes", 0L), o.optLong("built", 0L), o.optString("sha256", ""), o.optBoolean("auto", false))
            }
        }
    } catch (e: Exception) {
        Log.w(TAG, "the house's regions did not come: ${e.message}")
        null
    }

    override fun pack(id: String, from: Long): MapPackSync.Download {
        if (!MapPackSync.validId(id)) throw IOException("not a region id")
        val ask = request("/api/maps/packs/$id").apply { if (from > 0) header("Range", "bytes=$from-") }.get().build()
        val r = http.newCall(ask).execute()
        when {
            r.code == 200 -> return MapPackSync.Download(false, r.body!!.byteStream())
            r.code == 206 && from > 0 && r.header("Content-Range")?.startsWith("bytes $from-") == true ->
                return MapPackSync.Download(true, r.body!!.byteStream())
            r.code == 416 && from > 0 -> {
                r.close()
                // A part longer than what the house has now: the house's pack is another one.
                return pack(id, 0)
            }
            else -> {
                r.close()
                throw IOException("the house answered ${r.code} for a pack")
            }
        }
    }

    private fun request(path: String) = Request.Builder().url(base + path).header("Authorization", "Bearer $token")

    companion object {
        private const val TAG = "HousePacks"
        private const val MAX_LIST = 2L * 1024 * 1024

        /** No call timeout: a state's pack is gigabytes, and the read timeout is what catches a dead link. */
        private val DEFAULT: OkHttpClient by lazy {
            JarvisHttp.builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .followRedirects(false)
                .followSslRedirects(false)
                .build()
        }

        fun of(config: JarvisConfig): HousePacks? =
            if (config.isConfigured) HousePacks(config.serverUrl, config.token) else null
    }
}

/**
 * The copy of the house's packs when nobody opens the app: on an unmetered network, every few
 * hours. Always succeeds: a house that is away is tried again at the next period, which is soon
 * enough for a map.
 */
class MapPackWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    override fun doWork(): Result {
        runCatching {
            val app = applicationContext
            val house = HousePacks.of(JarvisConfig(app)) ?: return Result.success()
            val dir = MapPacks.dir(app)
            val free = { StatFs(app.noBackupFilesDir.path).availableBytes }
            MapPackSync(dir, house, free, { isStopped }).run()
        }.onFailure { Log.w(TAG, "the packs were not synced", it) }
        return Result.success()
    }

    companion object {
        internal const val NAME = "jarvis-map-packs"
        private const val TAG = "MapPackWorker"

        /** A pack is a hundred megabytes and more: never over a metered link. */
        fun constraints(): Constraints = Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build()

        /** Idempotent (UPDATE keeps the schedule and moves it to this class, T-1531) and never throws, like UpdateWorker.schedule: it runs in JarvisApp.onCreate. */
        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<MapPackWorker>(6, TimeUnit.HOURS).setConstraints(constraints()).build()
            runCatching {
                WorkManager.getInstance(context).enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.UPDATE, request)
            }.onFailure { Log.w(TAG, "the map pack copy is not scheduled", it) }
        }
    }
}
