package ai.jarvis.app.car.holo

import android.content.Context
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.util.zip.GZIPInputStream
import java.util.zip.GZIPOutputStream
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.hypot
import kotlin.math.max

/**
 * The house's map kept by the piece (T-1074): where the phone holds no pack, the ground is drawn
 * from tiles of [TILE_DEG] (a pack's cell, about a kilometre), each the house's
 * `/api/maps/buildings` answer for that box, asked for once and kept here as a gzip file. So the
 * detail is wherever the car goes without a state's pack on the phone (144 MB for a small one),
 * and a place driven before is drawn with the house away.
 *
 * The folder never passes [capBytes]: a write that takes it over deletes the tiles used longest
 * ago, down to nine tenths of it. Nothing is written with under [RESERVE_BYTES] free. A tile
 * older than [MAX_AGE_MS] is still read (`old`), and the caller asks the house for it again.
 *
 * Blocking; [DriveFeed] calls it on its reader thread.
 *
 * Not covered: a tile is not checked against the house's pack, so a map the house rebuilt shows
 * here when the tile has aged out; two processes writing one folder; the size on disk is the
 * files' lengths, not their blocks.
 */
class MapTiles(
    private val dir: File,
    private val capBytes: Long = CAP_BYTES,
    private val now: () -> Long = System::currentTimeMillis,
) {
    /** One tile: the cell whose south-west corner is ([x], [y]) hundredths of a degree. */
    data class Tile(val x: Int, val y: Int) {
        /** West, south, east, north. */
        fun box(): DoubleArray = doubleArrayOf(x * TILE_DEG, y * TILE_DEG, (x + 1) * TILE_DEG, (y + 1) * TILE_DEG)
    }

    /** A kept answer; [old] when it is time to ask the house again. */
    class Held(val text: String, val old: Boolean)

    private fun file(t: Tile) = File(dir, "${t.x}_${t.y}$EXT")

    fun has(t: Tile): Boolean = file(t).isFile

    /** The tile's answer, counted as used now; `null` when it is not held or does not read. */
    fun read(t: Tile): Held? {
        val f = file(t)
        if (!f.isFile) return null
        return runCatching {
            DataInputStream(f.inputStream().buffered()).use { s ->
                val at = s.readLong()
                val text = GZIPInputStream(s).readBytes().toString(Charsets.UTF_8)
                f.setLastModified(now())
                Held(text, now() - at > MAX_AGE_MS)
            }
        }.getOrElse {
            f.delete() // cut short by a full disk or a kill: ask the house again
            null
        }
    }

    /** Keeps [text] as the tile's answer, through a part file, and makes room past the cap. */
    fun write(t: Tile, text: String) {
        runCatching {
            dir.mkdirs()
            if (dir.usableSpace < RESERVE_BYTES) return
            // Two feeds share this folder: a part of its own per write, and only a part gone
            // stale is a write that died (no trim would count it); a fresh one is a live writer's.
            dir.listFiles { f -> f.name.endsWith(PART) }
                ?.filter { now() - it.lastModified() > STALE_PART_MS }?.forEach { it.delete() }
            val part = File.createTempFile("${t.x}_${t.y}_", PART, dir)
            val raw = part.outputStream().buffered()
            DataOutputStream(raw).apply { writeLong(now()) }.flush()
            GZIPOutputStream(raw).use { it.write(text.toByteArray(Charsets.UTF_8)) }
            val f = file(t)
            if (!part.renameTo(f)) part.delete() else f.setLastModified(now())
            trim()
        }
    }

    // ponytail: lists the folder on every write (a few a minute, thousands of files); keep a
    // running total if writes ever come faster than that.
    private fun trim() {
        val files = dir.listFiles { f -> f.name.endsWith(EXT) } ?: return
        var total = files.sumOf { it.length() }
        if (total <= capBytes) return
        for (f in files.sortedBy { it.lastModified() }) {
            if (total <= capBytes / 10 * 9) break
            total -= f.length()
            f.delete()
        }
    }

    companion object {
        /** A pack's cell (`tests/contracts/map-pack.json`), and within the house's cap for one ask. */
        const val TILE_DEG = 0.01
        private const val PER_DEG = 100

        /** What the tiles may take: a few thousand square kilometres of town, far more of country. */
        const val CAP_BYTES = 1L shl 30

        /** The phone's last half gigabyte is not the map's to take. */
        private const val RESERVE_BYTES = 512L shl 20

        /** A month: new buildings and signs arrive in the map about that often. */
        const val MAX_AGE_MS = 30L * 24 * 3600 * 1000

        private const val EXT = ".tile"
        private const val PART = ".part"

        /** A write takes seconds; a part this old was left by one that died. */
        private const val STALE_PART_MS = 60_000L

        /** In the cache folder: Android may clear it when the phone is short, and it is not backed up. */
        fun of(context: Context) = MapTiles(File(context.cacheDir, "map-tiles"))

        fun at(lon: Double, lat: Double) = Tile(floor(lon * PER_DEG).toInt(), floor(lat * PER_DEG).toInt())

        /** Every tile a west, south, east, north box touches, west to east then south to north. */
        fun covering(box: DoubleArray): List<Tile> {
            val a = at(box[0], box[1])
            val b = at(box[2], box[3])
            return (a.y..b.y).flatMap { y -> (a.x..b.x).map { x -> Tile(x, y) } }
        }

        /**
         * The tiles within [reachM] of a line, in the order the line meets them, at most [limit]:
         * what a drive along it will draw from.
         */
        fun along(line: List<LatLon>, reachM: Double, limit: Int): List<Tile> {
            val out = LinkedHashSet<Tile>()
            fun round(p: LatLon) {
                val dLat = reachM / GeoPlane.M_PER_DEG
                val dLon = reachM / (GeoPlane.M_PER_DEG * max(cos(Math.toRadians(p.lat)), MIN_COS))
                out += covering(doubleArrayOf(p.lon - dLon, p.lat - dLat, p.lon + dLon, p.lat + dLat))
            }
            for ((a, b) in line.zipWithNext()) {
                if (out.size >= limit) break
                val plane = GeoPlane(a.lat, a.lon)
                val steps = (hypot(plane.east(b.lon), plane.north(b.lat)) / STEP_M).toInt().coerceIn(1, MAX_STEPS)
                for (i in 0 until steps) round(LatLon(a.lat + (b.lat - a.lat) * i / steps, a.lon + (b.lon - a.lon) * i / steps))
            }
            line.lastOrNull()?.let(::round)
            return out.take(limit)
        }

        /** Half a tile: no tile between two samples is stepped over. */
        private const val STEP_M = 400.0

        /** One segment of a route is never thousands of kilometres; a bad one must not spin here. */
        private const val MAX_STEPS = 2_000

        /** Past 84 degrees a degree of longitude is too short to divide by. */
        private const val MIN_COS = 0.1
    }
}
