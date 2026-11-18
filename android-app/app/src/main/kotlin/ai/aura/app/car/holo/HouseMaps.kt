package ai.aura.app.car.holo

import ai.aura.app.config.ServerUrl
import ai.aura.app.tailnet.JarvisHttp
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Dispatcher
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * The house's maps (jarvis-core's `/api/maps` routes, `aura/integrations/maps`) for the drive
 * (T-0613): the route, the map-match and the building footprints.
 *
 * Every call is asynchronous; each answer is parsed off the main thread and handed to
 * `done(value, refused)` on the main thread, unless [close] came first. `refused` is the
 * house's own "there is none" (a 404 whose `detail.code` is `no_route`, `no_match`, or
 * `valhalla_refused` for anything but a route, [refused]); a `null` value without it is the house, Valhalla or the network away,
 * including a house too old to have the endpoint.
 *
 * Not covered: retries (the caller asks again on its own clock); a body over [MAX_BYTES]
 * is treated as the house away rather than read into memory.
 */
class HouseMaps(base: String, private val token: String) {
    private val base = ServerUrl.normalize(base)
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var closed = false

    private val http = JarvisHttp.builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        // A route waits on Valhalla; past this the driver is better served by the old one.
        .callTimeout(15, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    /** The same pool and settings, for a question whose answer is stale in seconds. */
    private val quick = http.newBuilder().callTimeout(MATCH_TIMEOUT_S, TimeUnit.SECONDS).build()

    /**
     * Tiles go two at a time on a dispatcher of their own: nine for a first look at a place, on
     * the shared one, were five calls ahead of every map-match (the 2026-11-11 drive, where
     * footprints starved the link).
     */
    private val tiles = http.newBuilder().dispatcher(Dispatcher().apply { maxRequests = TILES_AT_ONCE }).build()

    /**
     * [nothing], when given, is called instead of [done] when Valhalla refused the route
     * ([nothing] in the companion): the house answered and is well, and has no route to give.
     */
    fun route(from: LatLon, to: LatLon, done: (HouseRoute?, Boolean) -> Unit, nothing: (() -> Unit)? = null) {
        val body = JSONObject().put("from", pair(from)).put("to", pair(to)).put("costing", "auto")
        post("/api/maps/route", body, MapsJson::route, done, nothing)
    }

    /** [points] oldest first, 1 to 100 of them (the house's cap). */
    fun match(points: List<LatLon>, done: (Matched?, Boolean) -> Unit) {
        val arr = JSONArray()
        for (p in points) arr.put(pair(p))
        val json = JSONObject().put("points", arr).toString().toRequestBody("application/json".toMediaType())
        // Its own, shorter wait: one match is out at a time (DriveFeed), so an answer lost on
        // the way held the car's place on the road still for the whole 15 s.
        send(request("/api/maps/match").post(json).build(), MapsJson::match, done, quick)
    }

    /** At most 0.1 degrees across (the house's cap). */
    fun buildings(west: Double, south: Double, east: Double, north: Double, done: (NearMap?, Boolean) -> Unit) {
        val bbox = String.format(Locale.ROOT, "%.6f,%.6f,%.6f,%.6f", west, south, east, north)
        send(request("/api/maps/buildings?bbox=$bbox").get().build(), MapsJson::near, done)
    }

    /**
     * The roadworks and closures under way in the box (T-1108): at most 0.1 degrees across.
     * Never kept on the phone: what was live an hour ago is not what is.
     */
    fun live(west: Double, south: Double, east: Double, north: Double, done: (List<HoloDetail>?, Boolean) -> Unit) {
        val bbox = String.format(Locale.ROOT, "%.6f,%.6f,%.6f,%.6f", west, south, east, north)
        send(request("/api/maps/live?bbox=$bbox").get().build(), MapsJson::live, done, quick)
    }

    /** One [MapTiles] tile's answer: what it draws, and its [text] to keep, `null` when it must not be kept. */
    class Tile(val near: NearMap, val text: String?)

    /**
     * The buildings answer for a tile's box (T-1074). An answer the house marks `covered: false`
     * (no pack holds the place yet; the house has asked for its region) has no [Tile.text]: kept,
     * it would draw bare ground there after the region arrived.
     */
    fun tile(west: Double, south: Double, east: Double, north: Double, done: (Tile?, Boolean) -> Unit) {
        val bbox = String.format(Locale.ROOT, "%.6f,%.6f,%.6f,%.6f", west, south, east, north)
        val parse = { o: JSONObject -> Tile(MapsJson.near(o), if (o.optBoolean("covered", true)) o.toString() else null) }
        send(request("/api/maps/buildings?bbox=$bbox").get().build(), parse, done, tiles)
    }

    /** Nothing in flight is delivered after this; the screen calls it when it stops. */
    fun close() {
        closed = true
        http.dispatcher.cancelAll()
        tiles.dispatcher.cancelAll()
    }

    private fun <T> post(
        path: String, body: JSONObject, parse: (JSONObject) -> T?, done: (T?, Boolean) -> Unit,
        nothing: (() -> Unit)? = null,
    ) {
        val json = body.toString().toRequestBody("application/json".toMediaType())
        send(request(path).post(json).build(), parse, done, nothing = nothing)
    }

    private fun request(path: String) = Request.Builder().url(base + path).header("Authorization", "Bearer $token")

    private fun <T> send(request: Request, parse: (JSONObject) -> T?, done: (T?, Boolean) -> Unit, with: OkHttpClient = http, nothing: (() -> Unit)? = null) {
        if (closed) return
        val call = runCatching { with.newCall(request) }.getOrElse {
            // A server URL OkHttp cannot read: the house is as good as away.
            deliver(done, null, false)
            return
        }
        val started = System.nanoTime()
        val path = request.url.encodedPath
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (!call.isCanceled()) Log.w(TAG, "maps ${request.url.encodedPath} failed", e)
                deliver(done, null, false)
            }

            override fun onResponse(call: Call, response: Response) {
                val (value, refused, unsure) = response.use { r -> read(r, parse, path) }
                // T-0980: an answer the phone could not use was silent, so a drive with no
                // buildings could not say whether the house answered. Every buildings answer
                // and every unusable one is logged, by its status and time, never its place.
                if (value == null || path.endsWith("/buildings")) {
                    val what = if (value != null) "" else if (refused) ", none there" else ", nothing usable"
                    Log.i(TAG, "maps $path: ${response.code}$what in ${(System.nanoTime() - started) / 1_000_000} ms")
                }
                if (unsure && nothing != null) main.post { if (!closed) nothing() } else deliver(done, value, refused)
            }
        })
    }

    private fun <T> read(r: Response, parse: (JSONObject) -> T?, path: String): Triple<T?, Boolean, Boolean> {
        val text = runCatching {
            val source = r.body!!.source()
            if (source.request(MAX_BYTES + 1)) null else source.buffer.readUtf8()
        }.getOrNull() ?: return Triple(null, false, false)
        val o = runCatching { JSONObject(text) }.getOrNull() ?: return Triple(null, false, false)
        if (r.isSuccessful) return Triple(runCatching { parse(o) }.getOrNull(), false, false)
        val code = o.optJSONObject("detail")?.optString("code").orEmpty()
        return Triple(null, refused(r.code, code, path), nothing(r.code, code, path))
    }

    private fun <T> deliver(done: (T?, Boolean) -> Unit, value: T?, refused: Boolean) {
        main.post { if (!closed) done(value, refused) }
    }

    private fun pair(p: LatLon) = JSONArray().put(p.lat).put(p.lon)

    companion object {
        /**
         * The house's maps for this phone's pairing, or null before it is paired. Every kind
         * answers them: jarvis-core itself, the console's relay and the adapter's (the adapter
         * pairing had none, and the car's ground drew no buildings; the owner, 2026-11-10).
         */
        fun of(config: ai.aura.app.config.JarvisConfig): HouseMaps? =
            if (config.isConfigured) HouseMaps(config.serverUrl, config.token) else null

        private const val TAG = "HouseMaps"

        /** A match older than this says where the car was, not where it is. */
        private const val MATCH_TIMEOUT_S = 5L

        private const val TILES_AT_ONCE = 2

        /** 5000 footprints (the house's cap) are about 1.5 MB; well past it is not the house. */
        private const val MAX_BYTES = 8L * 1024 * 1024

        private val NONE = setOf("no_route", "valhalla_refused", "no_match")

        /**
         * Whether an error answer is the house saying "there is none". Valhalla refusing a
         * route is not (T-1029): it refuses where the house keeps no routing tiles (a region
         * over MAPS_ROUTE_MAX_GB), and the phone's packs may hold those roads, so the caller
         * is told the house had nothing and tries them.
         */
        internal fun refused(status: Int, code: String, path: String): Boolean =
            status == 404 && code in NONE && !nothing(status, code, path)

        /**
         * Valhalla refused a route: the house is there and has none to give, for a reason it
         * does not tell apart (no tiles for the region, or no way between the two places).
         */
        internal fun nothing(status: Int, code: String, path: String): Boolean =
            status == 404 && code == "valhalla_refused" && path.endsWith("/route")
    }
}
