package ai.jarvis.app.car.holo

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.roundToInt

/**
 * What a hologram drive is fed while it is shown: the phone's GPS into [position] (which
 * fills the frames between fixes), a map-match of the last few fixes every [MATCH_EVERY],
 * and the house's buildings within [NEAR_M], asked again each [REFETCH_M] the car moves.
 * One per drawn drive: the templated screen's ([CarNavScreen]) and the wireless car's
 * ground ([GroundView]). Main thread only.
 *
 * Does not cover the route (whoever has one hands it to the scene) or the car's own GPS:
 * a phone whose GPS stops leaves the drive dead-reckoning, then still.
 */
class DriveFeed(
    private val context: Context,
    val position: CarPosition,
    /** Changes the scene between frames, never during one. */
    private val scene: ((HoloScene) -> Unit) -> Unit,
    /** After each fix [position] took: the fix, and where the car is drawn now. */
    private val onFix: (fix: Location, here: LatLon, now: Long) -> Unit = { _, _, _ -> },
    /** The clock [position] is fed and read on: the drawing's own. */
    private val clock: () -> Long = SystemClock::uptimeMillis,
    /** The map packs the phone copied (T-1017): what the ground draws from when the house's buildings call fails. */
    private val packs: MapPacks = MapPacks.of(context),
    /** The house's answers kept by the tile (T-1074): what the ground draws from where no pack covers. */
    private val tiles: MapTiles = MapTiles.of(context),
) {
    /** The house's maps from [start] to [stop]; null on a Hermes-only pairing (the packs are then all that is asked). */
    var maps: HouseMaps? = null
        private set
    private var fixes = 0
    private var matching = false
    private var buildingsAt: LatLon? = null
    @Volatile private var buildingsAsk = 0

    private val main = Handler(Looper.getMainLooper())
    /**
     * Reads the packs and writes the tiles, one at a time and off the main thread.
     * One thread for the feed's whole life, gone after half a minute idle. [stop] leaves it
     * alone: shutting it down threw away the queued writes of tiles the house had already
     * answered (T-1151), and a new one made by the next [start] would run beside the old one
     * draining, two threads on [parsed] and two writers in the tile folder.
     */
    private val packReader: ExecutorService = ThreadPoolExecutor(
        0, 1, READER_IDLE_S, TimeUnit.SECONDS, LinkedBlockingQueue(),
    ) { r -> Thread(r, "jarvis-map-pack").apply { isDaemon = true } }
    /** Routes over the packs' roads, one at a time; [stop] ends it and a route that finishes after is dropped. */
    private var router: ExecutorService? = null
    @Volatile private var routeAsk = 0
    /** Set by [stop], cleared by [start]: a route asked for in between is not started, and answers nothing. */
    @Volatile private var stopped = false
    /** When the last ask found the house away; parked, nothing else would ask again. */
    private var buildingsFailedAt: Long? = null
    /** Asks in a row that found the house away, for [asksBuildings]'s backoff; an answer resets it. */
    private var buildingsFails = 0
    private val gps = LocationListener { onLocation(it) }

    /**
     * The gyroscope's turn into [position] (T-1101), about the up the gravity sensor gives.
     * Its timestamps are the sensor's own (nanoseconds); only their difference is used.
     */
    private val turn = object : SensorEventListener {
        private val up = FloatArray(3)
        private var lastNs = 0L

        override fun onSensorChanged(e: SensorEvent) {
            if (e.sensor.type == Sensor.TYPE_GRAVITY) {
                e.values.copyInto(up, 0, 0, 3)
                return
            }
            val dt = if (lastNs == 0L) 0f else (e.timestamp - lastNs) / 1e9f
            lastNs = e.timestamp
            val deg = gyroTurnDeg(e.values[0], e.values[1], e.values[2], up[0], up[1], up[2], dt)
            if (deg != 0f) position.turned(deg, clock())
        }

        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
    }

    /** A phone with no gyroscope or no gravity sensor keeps GPS bearing alone, as before. */
    private fun listenForTurns() {
        val sm = context.getSystemService(SensorManager::class.java) ?: return
        val gyro = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE) ?: return
        val gravity = sm.getDefaultSensor(Sensor.TYPE_GRAVITY) ?: return
        sm.registerListener(turn, gravity, SensorManager.SENSOR_DELAY_UI)
        sm.registerListener(turn, gyro, SensorManager.SENSOR_DELAY_GAME)
    }

    /** Listens to GPS and asks [maps]; false when there is no GPS provider or no location grant. */
    fun start(maps: HouseMaps?): Boolean {
        this.maps = maps
        stopped = false
        packs.reopen() // stop() closed them for good, so a reader mid-open could not re-fill them
        if (context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) return false
        val lm = context.getSystemService(LocationManager::class.java) ?: return false
        return try {
            lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, GPS_MS, 0f, gps, Looper.getMainLooper())
            listenForTurns()
            true
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "no GPS provider", e)
            false
        } catch (e: SecurityException) {
            Log.w(TAG, "location revoked", e)
            false
        }
    }

    fun stop() {
        context.getSystemService(LocationManager::class.java)?.removeUpdates(gps)
        context.getSystemService(SensorManager::class.java)?.unregisterListener(turn)
        maps?.close()
        maps = null
        // Answers to these are never delivered after close: the next start asks again.
        buildingsAsk++
        // The reader keeps what is queued: its writes finish, and the asks counted on above
        // and `stopped` keep its reads and draws off the screen.
        stopped = true
        routeAsk++
        router?.shutdownNow()
        router = null
        packs.close()
        matching = false
        buildingsAt = null
        buildingsFailedAt = null
        buildingsFails = 0
        liveAt = null
        liveFails = 0
        // An answer in flight is never delivered after close: the next drive must be able to fetch.
        fetchingAhead = false
        aheadRetryAt = 0L
        asking.clear()
    }

    private fun onLocation(loc: Location) {
        val now = clock()
        val here = fix(
            loc.latitude,
            loc.longitude,
            if (loc.hasSpeed()) loc.speed else 0f,
            if (loc.hasBearing()) loc.bearing else null,
            if (loc.hasAccuracy()) loc.accuracy else 0f,
            now,
        ) ?: return
        onFix(loc, here, now)
    }

    /** One fix at [now]: where the car is drawn now, or null when [position] refused it. */
    fun fix(lat: Double, lon: Double, speedMps: Float, bearingDeg: Float?, accuracyM: Float, now: Long): LatLon? {
        if (!position.fix(lat, lon, speedMps, bearingDeg, accuracyM, now)) return null
        if (++fixes % MATCH_EVERY == 0) match()
        val here = position.at(now)?.let { LatLon(it.lat, it.lon) } ?: return null
        buildingsNear(here)
        liveNear(here, now)
        pump()
        return here
    }

    /** One match out at a time; a slow house skips matches rather than queueing them. */
    private fun match() {
        val m = maps ?: return
        if (matching) return
        val trace = position.trace()
        if (trace.isEmpty()) return
        matching = true
        m.match(trace) { got, _ ->
            matching = false
            if (got == null) return@match
            position.matched(trace.last(), got.at, clock())
            if (got.street.isNotBlank()) scene { it.street = got.street }
        }
    }

    private var liveAt: LatLon? = null
    private var liveAskedAt = 0L
    private var liveFails = 0

    /**
     * The house's live work zones round [at] (T-1108), asked when [asksLive] says. An answer
     * replaces what was drawn, an empty one clears it; after [LIVE_DROP] asks in a row with no
     * answer the zones are cleared too, since nothing says they are still there.
     */
    private fun liveNear(at: LatLon, now: Long) {
        val m = maps ?: return
        val moved = liveAt?.let { last -> GeoPlane(last.lat, last.lon).let { p -> hypot(p.east(at.lon), p.north(at.lat)) } }
        if (!asksLive(moved, now - liveAskedAt)) return
        liveAt = at
        liveAskedAt = now
        val b = nearBox(at)
        m.live(b[0], b[1], b[2], b[3]) { got, _ ->
            if (stopped) return@live
            if (got != null) {
                liveFails = 0
                scene { it.setLive(got) }
            } else if (++liveFails == LIVE_DROP) {
                scene { it.setLive(emptyList()) }
            }
        }
    }

    /** Asked once per [REFETCH_M] moved ([asksBuildings]), so a house without them is not asked each fix. */
    private fun buildingsNear(at: LatLon) {
        val m = maps
        val moved = buildingsAt?.let { last -> GeoPlane(last.lat, last.lon).let { p -> hypot(p.east(at.lon), p.north(at.lat)) } }
        if (!asksBuildings(moved, buildingsFailedAt, buildingsFails, clock())) return
        buildingsAt = at
        buildingsFailedAt = null
        // Fast driving on a slow house has two out; the older answer must not replace the newer.
        val ask = ++buildingsAsk
        val box = nearBox(at)
        // The packs, and the house only where no pack covers the box. The house was once asked
        // as well and had the last word, for the same data: a kilometre of footprints every
        // 250 m over mobile data starved everything else on the link for a whole drive
        // (2026-11-11: matches timed out, her speech ran dry, the socket dropped five times).
        // T-1074: and from the house by the tile, each asked for once and kept, so a place
        // driven before costs the link nothing and is drawn with the house away.
        // Nearest first: the house is asked for the car's own tile before the far ones, and
        // where the merge is cut at its cap it is the far tiles that lose (T-1103's review).
        fromPacks(ask, box, at) { fromTiles(ask, nearestFirst(MapTiles.covering(box), at), at, m) }
    }

    /** On the reader's thread, after whatever is queued there. */
    private fun onReader(work: () -> Unit) {
        runCatching { packReader.execute { runCatching(work).onFailure { Log.w(TAG, "a map read failed", it) } } }
    }

    /** The tiles read this drive, newest used last; the reader's thread only. */
    private val parsed = object : LinkedHashMap<MapTiles.Tile, NearMap>(PARSED_MAX * 2, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<MapTiles.Tile, NearMap>?) = size > PARSED_MAX
    }
    /** The ask whose tiles last found the house away: nine tiles failing are one failure to back off from. */
    private var failedAsk = 0
    /**
     * The tiles asked of the house and not answered yet (main thread). On a slow link at speed
     * each new ask would otherwise ask again for every tile still queued behind the last one.
     */
    private val asking = HashSet<MapTiles.Tile>()
    /** The newest ask the tiles serve: an answer to an older ask's call is drawn for this one. */
    @Volatile private var tilesFor: Triple<Int, List<MapTiles.Tile>, LatLon>? = null

    /** [want] from memory and the phone's tile files, drawn at once; then the house for each one missing or old. */
    private fun fromTiles(ask: Int, want: List<MapTiles.Tile>, at: LatLon, m: HouseMaps?) = onReader {
        if (ask != buildingsAsk) return@onReader
        tilesFor = Triple(ask, want, at)
        val missing = ArrayList<MapTiles.Tile>()
        for (t in want) {
            if (parsed.containsKey(t)) continue
            val held = tiles.read(t)
            val near = held?.let { h -> runCatching { MapsJson.near(JSONObject(h.text)) }.getOrNull() }
            if (near != null) parsed[t] = near
            if (near == null || held.old) missing += t
        }
        draw(ask, want, at, evenEmpty = false)
        if (m == null) return@onReader
        main.post {
            if (ask != buildingsAsk || stopped) return@post
            for (t in missing) if (asking.add(t)) fromHouse(ask, t, m)
        }
    }

    private fun fromHouse(ask: Int, t: MapTiles.Tile, m: HouseMaps) {
        val b = t.box()
        m.tile(b[0], b[1], b[2], b[3]) { got, refused ->
            asking.remove(t)
            if (got == null) {
                if (!refused && ask == buildingsAsk && failedAsk != ask) {
                    failedAsk = ask
                    buildingsFails++
                    buildingsFailedAt = clock()
                }
                return@tile
            }
            buildingsFails = 0
            onReader {
                // A place the house has no map of yet is drawn empty and asked about again:
                // kept, here or on disk, it would stay bare after its region arrived.
                if (got.text != null) {
                    tiles.write(t, got.text)
                    parsed[t] = got.near
                } else {
                    parsed.remove(t)
                }
                tilesFor?.let { (newest, want, at) -> if (t in want) draw(newest, want, at, evenEmpty = true) }
            }
        }
    }

    /** The reader's thread: what is held of [want] into the scene, if this is still the newest ask. */
    private fun draw(ask: Int, want: List<MapTiles.Tile>, at: LatLon, evenEmpty: Boolean) {
        if (ask != buildingsAsk) return
        val have = want.mapNotNull { parsed[it] }
        if (have.isEmpty() && !evenEmpty) return
        val merged = NearMap.merge(have, MapPack.LIMIT)
        main.post { if (ask == buildingsAsk && !stopped) took(merged, at) }
    }

    /** The tiles of the route still to fetch, in the order it is driven. Main thread. */
    private val ahead = ArrayDeque<MapTiles.Tile>()
    private var fetchingAhead = false
    private var aheadRetryAt = 0L
    /** Times in a row the tile at the head found no answer: one that never reads must not hold the rest back. */
    private var aheadFails = 0

    /**
     * A route was planned (T-1074): its tiles are fetched now, one at a time and in the order
     * they are driven, so the detail is on the phone before the car is there and a stretch with
     * no signal is still drawn. `null` or an empty line forgets what was still to fetch.
     *
     * Not covered: a tile the house has no map of yet is skipped (the drive asks again when it
     * is there); past [AHEAD_MAX] tiles the rest of the route is fetched as it is driven.
     */
    fun ahead(line: List<LatLon>?) {
        ahead.clear()
        if (line != null) ahead += MapTiles.along(line, NEAR_M, AHEAD_MAX)
        aheadRetryAt = 0L
        aheadFails = 0
        pump()
    }

    private fun pump() {
        val m = maps ?: return
        if (fetchingAhead || stopped || clock() < aheadRetryAt) return
        val t = ahead.removeFirstOrNull() ?: return
        fetchingAhead = true
        val b = t.box()
        onReader {
            // A pack that does not read must not leave the fetch waiting on this for the whole drive.
            val held = runCatching { tiles.has(t) || packs.covers(b[0], b[1], b[2], b[3]) }.getOrDefault(false)
            main.post {
                if (stopped) return@post
                if (held) {
                    fetchingAhead = false
                    pump()
                    return@post
                }
                m.tile(b[0], b[1], b[2], b[3]) { got, _ ->
                    fetchingAhead = false
                    if (got == null) {
                        // The house away: this tile again, but not on every fix, and not for
                        // ever (an answer too large or unreadable fails the same way each time).
                        if (++aheadFails < AHEAD_TRIES) ahead.addFirst(t) else aheadFails = 0
                        aheadRetryAt = clock() + RETRY_MS
                        return@tile
                    }
                    aheadFails = 0
                    got.text?.let { text -> onReader { tiles.write(t, text) } }
                    pump()
                }
            }
        }
    }

    /**
     * The packs the phone holds round [at], read on their own thread (a box is a few
     * milliseconds of SQLite, too long for a frame) and delivered here if this is still the
     * newest ask; [uncovered] runs here instead when no pack holds the box.
     */
    private fun fromPacks(ask: Int, box: DoubleArray, at: LatLon, uncovered: () -> Unit) {
        runCatching {
            packReader.execute {
                if (ask != buildingsAsk) return@execute
                val got = runCatching { packs.near(box[0], box[1], box[2], box[3]) }.getOrNull()
                main.post {
                    if (ask != buildingsAsk || stopped) return@post
                    if (got != null) took(got, at) else uncovered()
                }
            }
        } // a reader that stop() ended: nothing to draw for
    }

    /**
     * The house gave no route: the roads of the packs the phone holds may (T-1026, [PackRouter]).
     * Searched on a thread of its own (up to [PackRouter.BUDGET_MS], which would stall the ground's
     * pack reads on theirs), [done] on the main one with the route or `null`; never called after
     * [stop]. [kept] is the route being followed: a [to] too far for one pack read is reached
     * through its line (T-1047).
     */
    fun routeFromPacks(from: LatLon, to: LatLon, kept: HouseRoute? = null, done: (HouseRoute?) -> Unit) {
        if (stopped) return // a stopped drive wants no answer, and no thread made for one
        val ask = routeAsk
        val worker = router ?: Executors.newSingleThreadExecutor { r -> Thread(r, "jarvis-pack-route").apply { isDaemon = true } }
            .also { router = it }
        runCatching {
            worker.execute {
                val got = runCatching { PackRouter.route(from, to, { w, s, e, n -> packs.roads(w, s, e, n) }, kept = kept, covered = { p -> packs.covers(p.lon, p.lat, p.lon, p.lat) }) }.getOrNull()
                main.post { if (ask == routeAsk) done(got) }
            }
        } // a worker that stop() ended: nothing to route for
    }

    /**
     * The house's footprints and roads round [at], into the scene, and how many and how near
     * into the log (T-0980): never where the car is.
     */
    internal fun took(near: NearMap, at: LatLon) {
        val p = GeoPlane(at.lat, at.lon)
        val nearest = near.buildings.minOfOrNull { b -> b.ring.minOf { hypot(p.east(it.lon), p.north(it.lat)) } }
        Log.i(TAG, "${near.buildings.size} buildings (nearest ${nearest?.let { "${it.roundToInt()} m" } ?: "none"}), ${near.roads.size} roads")
        position.roads(near.roads)
        scene {
            it.setBuildings(near.buildings)
            it.setRoads(near.roads)
            near.details?.let { d -> it.setDetails(d) }
            // An answer with no heights (an older house, a tile kept from before them) does not flatten the place.
            if (near.ground.isNotEmpty()) it.setGround(near.ground)
        }
    }

    companion object {
        /** What GPS gives anyway; [CarPosition] fills the frames between. */
        private const val GPS_MS = 1_000L

        /** [tiles] by how far each one's middle is from [at]. */
        internal fun nearestFirst(tiles: List<MapTiles.Tile>, at: LatLon): List<MapTiles.Tile> {
            val p = GeoPlane(at.lat, at.lon)
            return tiles.sortedBy { t ->
                val b = t.box()
                hypot(p.east((b[0] + b[2]) / 2), p.north((b[1] + b[3]) / 2))
            }
        }

        /** West, south, east, north of what is drawn round [at]: [NEAR_M] each way. */
        internal fun nearBox(at: LatLon): DoubleArray {
            val dLat = NEAR_M / GeoPlane.M_PER_DEG
            val dLon = NEAR_M / (GeoPlane.M_PER_DEG * cos(Math.toRadians(at.lat)))
            return doubleArrayOf(at.lon - dLon, at.lat - dLat, at.lon + dLon, at.lat + dLat)
        }

        /** About five seconds of driving between matches. */
        const val MATCH_EVERY = 5

        /** The drive shows [HoloCamera.FOG_END_M] ahead; this covers it with a refetch's room to move. */
        private const val NEAR_M = 1_800.0

        private const val REFETCH_M = 200f

        /** Tiles parsed and held in memory: the ones round the car (nine or so), and those just left. */
        private const val PARSED_MAX = 40
        /** The reader's thread ends after this long with nothing to read or write. */
        private const val READER_IDLE_S = 30L

        /** A route's tiles fetched ahead: about 150 km of road with its sides. */
        private const val AHEAD_MAX = 600
        private const val AHEAD_TRIES = 3

        /** A house that was away is asked again [RETRY_FIRST_MS] after, doubling to [RETRY_MS], moved or not. */
        private const val RETRY_FIRST_MS = 5_000L
        private const val RETRY_MS = 30_000L

        /**
         * Whether the buildings are asked for again: the first fix, [REFETCH_M] moved, or a while
         * after the house was away, 5 s after the first of [fails] and doubling to 30 s. A first
         * ask that failed (the tunnel not up yet at the start of a drive) left a parked car with no
         * buildings for good, then for 30 s (T-0977).
         */
        internal fun asksBuildings(movedM: Float?, failedAt: Long?, fails: Int, now: Long): Boolean {
            if (movedM == null || movedM >= REFETCH_M) return true
            val wait = (RETRY_FIRST_MS shl (fails - 1).coerceIn(0, 3)).coerceAtMost(RETRY_MS)
            return failedAt != null && now - failedAt >= wait
        }

        /**
         * Whether the live zones are asked for again: the first fix, every [LIVE_MS] parked or
         * not, and [LIVE_MOVED_M] on (half of what one ask covers) but never inside
         * [LIVE_MIN_MS], so a motorway is one small ask every quarter minute at most.
         */
        internal fun asksLive(movedM: Float?, sinceMs: Long): Boolean =
            movedM == null || sinceMs >= LIVE_MS || (movedM >= LIVE_MOVED_M && sinceMs >= LIVE_MIN_MS)

        private const val LIVE_MS = 180_000L
        private const val LIVE_MIN_MS = 15_000L
        private const val LIVE_MOVED_M = 500f
        private const val LIVE_DROP = 3

        private const val TAG = "DriveFeed"
    }
}
