package ai.jarvis.app.car.holo

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import kotlin.math.hypot
import kotlin.math.roundToInt

/** The house's `/api/maps/route` answer (jarvis-core `integrations/maps.route`). */
class HouseRoute(
    val shape: List<LatLon>,
    val distanceM: Float,
    val durationS: Float,
    /** The street the route starts on. */
    val street: String,
    /** The next maneuver; with no turn before the end it is the arrival. */
    val turn: Turn?,
) {
    /** [modifier] is the turn's side for a reader that words it itself (the console's `sayStep`): left, right, uturn, straight, or "" where the maker gave none. */
    class Turn(val instruction: String, val street: String, val at: LatLon?, val modifier: String = "")

    fun holo() = HoloRoute(shape, street, turn?.at, turn?.street?.ifBlank { null })
}

/** Where the house put the newest fix on the road (`/api/maps/match`). */
class Matched(val at: LatLon, val street: String)

/**
 * The house's `/api/maps/buildings` answer: the footprints, the roads and paths through them,
 * and what stands beside them (T-1060). [details] is `null` when the answer does not speak of
 * them at all (a house from before T-1066), which must not clear what a pack already gave.
 */
class NearMap(
    val buildings: List<HoloBuilding>,
    val roads: List<HoloRoad>,
    val details: List<HoloDetail>? = null,
    /** The ground's heights (T-1105); empty from a house or a pack that has none. */
    val ground: List<HoloGroundCell> = emptyList(),
) {
    companion object {
        /**
         * Several answers as one, at most [limit] of each kind: two packs that meet, or two tiles
         * a road runs through, hold the same feature, told apart by its geometry alone.
         */
        fun merge(parts: List<NearMap>, limit: Int): NearMap {
            val buildings = LinkedHashMap<List<LatLon>, HoloBuilding>()
            val roads = LinkedHashMap<List<LatLon>, HoloRoad>()
            val details = LinkedHashMap<Pair<String, List<LatLon>>, HoloDetail>()
            val ground = LinkedHashMap<Pair<Int, Int>, HoloGroundCell>()
            for (m in parts) {
                for (b in m.buildings) buildings.putIfAbsent(b.ring, b)
                for (r in m.roads) roads.putIfAbsent(r.line, r)
                for (d in m.details.orEmpty()) details.putIfAbsent(d.kind to d.pts, d)
                for (g in m.ground) ground.putIfAbsent(g.cx to g.cy, g)
            }
            return NearMap(buildings.values.take(limit), roads.values.take(limit), details.values.take(limit), ground.values.toList())
        }
    }
}

/**
 * The house's map answers, read defensively: whatever does not parse is `null` (or left
 * out), never a half-drawn route. Does not check that a route goes where it was asked to.
 */
object MapsJson {
    private val HEX = Regex("^#[0-9a-fA-F]{6}$")

    /** A roof shape or a building kind is a word; a longer one is cut. */
    private const val MAX_WORD = 24

    /** The longest name a building's sign holds (tests/contracts/map-pack.json `sign.max`). */
    const val MAX_SIGN = 32

    /** A roof taller than this is a typo in the map (T-1185). */
    private const val MAX_ROOF_M = 60.0

    /** More lanes than any road has: a larger number is a typo in the map. */
    const val MAX_LANES = 12
    const val MAX_REF = 40
    const val MAX_LAYER = 5

    /** T-1183: the longest of what a guide sign says (tests/contracts/map-pack.json `roadTags`; MapPackTest reads it). */
    const val MAX_DESTINATION = 120
    const val MAX_JUNCTION_REF = 12
    const val MAX_TRAFFIC_SIGN = 60

    /** T-1235: the longest list of a road's lane turns, and the most lanes in it (map-pack.json `turnLanes`; MapPackTest reads it). */
    const val MAX_TURN_LANES = 80
    const val MAX_TURN_LANES_LANES = 8
    private val TURN_LANES = Regex("[a-z_;|]+")

    /** The most ground cells read from one answer: a box 0.1 degrees across is 121. */
    const val MAX_GROUND_CELLS = 400

    /** The Dead Sea's shore and Everest, rounded out: a height outside them is a bad number, not a place. */
    const val MIN_GROUND_M = -500
    const val MAX_GROUND_M = 9000

    /** A park's or a lake's ring with more points than this is left out, like a pack's. */
    private const val MAX_DETAIL_POINTS = 2_000

    fun route(o: JSONObject): HouseRoute? {
        val arr = o.optJSONArray("shape") ?: return null
        if (arr.length() < 2) return null
        val shape = ArrayList<LatLon>(arr.length())
        for (i in 0 until arr.length()) shape += point(arr.optJSONArray(i), lonFirst = false) ?: return null
        val distance = o.optDouble("distance_m")
        val duration = o.optDouble("duration_s")
        if (!(distance >= 0.0) || !(duration >= 0.0)) return null
        val turn = o.optJSONObject("next_turn")?.let { t ->
            HouseRoute.Turn(
                instruction = str(t, "instruction").trim().removeSuffix("."),
                street = str(t, "street"),
                at = point(t.optJSONArray("at"), lonFirst = false),
            )
        }
        return HouseRoute(shape, distance.toFloat(), duration.toFloat(), str(o, "current_street"), turn)
    }

    /** [r] as the house sent it, so [route] reads it back: the phone's own copy of a route (T-1016). */
    fun encode(r: HouseRoute): JSONObject = JSONObject().apply {
        put("shape", JSONArray().also { a -> r.shape.forEach { a.put(pair(it)) } })
        put("distance_m", r.distanceM.toDouble())
        put("duration_s", r.durationS.toDouble())
        put("current_street", r.street)
        r.turn?.let { t ->
            put("next_turn", JSONObject().apply {
                put("instruction", t.instruction)
                put("street", t.street)
                t.at?.let { put("at", pair(it)) }
            })
        }
    }

    /** `[lat, lon]`, the order of the house's route answer. */
    fun pair(p: LatLon): JSONArray = JSONArray().put(p.lat).put(p.lon)

    /** The `[lat, lon]` pair of [pair], or `null` when it is not a valid one. */
    fun latLon(a: JSONArray?): LatLon? = point(a, lonFirst = false)

    fun match(o: JSONObject): Matched? {
        val lat = o.optDouble("lat")
        val lon = o.optDouble("lon")
        if (!valid(lat, lon)) return null
        return Matched(LatLon(lat, lon), str(o, "street"))
    }

    // Android's org.json reads a JSON null as the string "null": an unnamed road would be drawn so.
    private fun str(o: JSONObject, key: String): String = if (o.isNull(key)) "" else o.optString(key)

    /** The buildings answer whole; a house from before T-0980 sends no roads, and its buildings still draw. */
    fun near(o: JSONObject): NearMap = NearMap(buildings(o), roads(o), if (o.has("details")) details(o) else null, ground(o))

    /** The answer's `ground` (T-1105): `[{cell: [cx, cy], n, h}]`; one that is not a whole grid is left out. */
    fun ground(o: JSONObject): List<HoloGroundCell> {
        val arr = o.optJSONArray("ground") ?: return emptyList()
        val out = ArrayList<HoloGroundCell>()
        for (i in 0 until minOf(arr.length(), MAX_GROUND_CELLS)) {
            val g = arr.optJSONObject(i) ?: continue
            val cell = g.optJSONArray("cell") ?: continue
            if (cell.length() != 2 || cell.opt(0) !is Int || cell.opt(1) !is Int) continue
            groundCell(cell.getInt(0), cell.getInt(1), g.optInt("n"), g.optJSONArray("h"))?.let { out += it }
        }
        return out
    }

    /** One cell from its numbers, or `null` unless [h] is [n] by [n] whole numbers on Earth. */
    fun groundCell(cx: Int, cy: Int, n: Int, h: JSONArray?): HoloGroundCell? {
        if (h == null || n !in 2..HoloGroundCell.MAX_N || h.length() != n * n) return null
        if (cx !in -18000 until 18000 || cy !in -9000 until 9000) return null
        val out = ShortArray(n * n)
        for (k in out.indices) {
            val v = h.opt(k) as? Int ?: return null
            out[k] = v.coerceIn(MIN_GROUND_M, MAX_GROUND_M).toShort()
        }
        return HoloGroundCell(cx, cy, n, out)
    }

    /** `#rrggbb` as opaque ARGB, or 0 for anything else: a colour is the house's text, not trusted. */
    internal fun colour(text: String): Int =
        if (HEX.matches(text)) (0xFF000000L or text.substring(1).toLong(16)).toInt() else 0

    /** A building's look from the keys the house's answer and a pack's `extra` share (T-1066). */
    internal fun look(o: JSONObject): HoloLook {
        val min = o.optDouble("min_height_m", 0.0)
        val look = HoloLook(
            colour(str(o, "colour")), colour(str(o, "roof_colour")), str(o, "roof_shape").take(MAX_WORD),
            if (min.isFinite() && min > 0.0) min.toFloat() else 0f, str(o, "kind").take(MAX_WORD), o.optBoolean("part", false),
            // A name is the house's text: one short line of what can be printed, or no sign. A
            // format character (one that reverses the reading order) is not printed either.
            str(o, "sign").trim().takeIf { it.length <= MAX_SIGN && it.none { c -> c.isISOControl() || Character.getType(c) == Character.FORMAT.toInt() } }.orEmpty(),
            str(o, "trade").take(MAX_WORD),
            // T-1185: the pack keeps the roof's height, its slope's direction and its ridge's side.
            roofM = o.optDouble("roof_height_m", 0.0).let { if (it.isFinite() && it > 0.0 && it < MAX_ROOF_M) it.toFloat() else 0f },
            roofDir = o.optDouble("roof_dir", -1.0).let { if (it.isFinite() && it >= 0.0 && it < 360.0) it.toFloat() else -1f },
            roofOrient = str(o, "roof_orient").takeIf { it == "along" || it == "across" }.orEmpty(),
        )
        val plain = look.colour == 0 && look.roofColour == 0 && look.roofShape.isEmpty() && look.minM == 0f &&
            look.kind.isEmpty() && !look.part && look.sign.isEmpty() && look.roofM == 0f && look.roofDir < 0f &&
            look.roofOrient.isEmpty()
        return if (plain) HoloLook.NONE else look
    }

    /** Trees, lamps, parking and the rest from `/api/maps/buildings`; each `geom` is `[lon, lat]` points. */
    fun details(o: JSONObject): List<HoloDetail> {
        val arr = o.optJSONArray("details") ?: return emptyList()
        val out = ArrayList<HoloDetail>(arr.length())
        for (i in 0 until arr.length()) {
            val d = arr.optJSONObject(i) ?: continue
            val geom = d.optJSONArray("geom") ?: continue
            if (geom.length() > MAX_DETAIL_POINTS) continue
            val pts = ArrayList<LatLon>(geom.length())
            for (j in 0 until geom.length()) pts += point(geom.optJSONArray(j), lonFirst = true) ?: break
            if (pts.size == geom.length()) HoloDetail.of(str(d, "kind"), pts, str(d, "text"))?.let { out += it }
        }
        return out
    }

    /**
     * The zones of `/api/maps/live` (T-1108) as the scene takes them: "roadworks" or
     * [HoloWorld.CLOSURE] along `[lon, lat]` points. Empty when the house has no feed, cannot
     * read it, or nothing is under way here; a zone that does not read is left out.
     */
    fun live(o: JSONObject): List<HoloDetail> {
        val arr = o.optJSONArray("zones") ?: return emptyList()
        val out = ArrayList<HoloDetail>()
        for (i in 0 until minOf(arr.length(), MAX_LIVE)) {
            val z = arr.optJSONObject(i) ?: continue
            val kind = str(z, "kind")
            val line = z.optJSONArray("line") ?: continue
            if ((kind != "roadworks" && kind != HoloWorld.CLOSURE) || line.length() !in 2..MAX_LIVE_POINTS) continue
            val pts = ArrayList<LatLon>(line.length())
            for (j in 0 until line.length()) pts += point(line.optJSONArray(j), lonFirst = true) ?: break
            if (pts.size == line.length()) out += HoloDetail(kind, pts)
        }
        return out
    }

    /** A kilometre round the car holds a handful; the house's own caps are 5000 zones of 2000 points. */
    private const val MAX_LIVE = 200
    private const val MAX_LIVE_POINTS = 2000

    /** Roads and paths from `/api/maps/buildings` (T-0980); each line is GeoJSON, `[lon, lat]`. */
    fun roads(o: JSONObject): List<HoloRoad> {
        val arr = o.optJSONArray("roads") ?: return emptyList()
        val out = ArrayList<HoloRoad>(arr.length())
        for (i in 0 until arr.length()) {
            val r = arr.optJSONObject(i) ?: continue
            val line = r.optJSONArray("line") ?: continue
            if (line.length() < 2) continue
            val pts = ArrayList<LatLon>(line.length())
            for (j in 0 until line.length()) pts += point(line.optJSONArray(j), lonFirst = true) ?: break
            if (pts.size == line.length()) out += HoloRoad(
                pts, str(r, "kind"), r.optInt("lanes", 0).coerceIn(0, MAX_LANES),
                // Where the house says it; 0 (either way) where it does not.
                r.optInt("oneway", 0).let { if (it == 1 || it == -1) it else 0 },
                str(r, "name"),
                refOf(r), r.opt("bridge") == true, r.opt("tunnel") == true, layerOf(r),
                wordsOf(r, "destination", MAX_DESTINATION), wordsOf(r, "destination:ref", MAX_REF),
                wordsOf(r, "junction:ref", MAX_JUNCTION_REF), wordsOf(r, "traffic_sign", MAX_TRAFFIC_SIGN),
                turnLanes = turnLanesOf(r, "turn:lanes"),
                turnLanesForward = turnLanesOf(r, "turn:lanes:forward"),
                turnLanesBackward = turnLanesOf(r, "turn:lanes:backward"),
            )
        }
        return out
    }

    /** T-1167: a route number only if the house sent a non-blank string; cut at [MAX_REF]. */
    fun refOf(o: JSONObject): String? = (o.opt("ref") as? String)?.trim()?.take(MAX_REF)?.takeIf { it.isNotEmpty() }

    /**
     * T-1183: a guide sign's words from a mapper's free text: a string, one trimmed line cut at [most], and
     * null if it is blank or holds a control or format character (a direction override reorders what is read).
     */
    fun wordsOf(o: JSONObject, key: String, most: Int): String? =
        (o.opt(key) as? String)?.trim()?.take(most)?.takeIf {
            it.isNotEmpty() && it.none { c -> c.isISOControl() || Character.getType(c) == Character.FORMAT.toInt() }
        }

    /**
     * T-1235: a road's lane turns (`left|through;right`) whole or null: a trimmed string of lowercase words, '_', ';' and '|'
     * within [MAX_TURN_LANES] characters and [MAX_TURN_LANES_LANES] lanes. Never cut: a cut list puts an arrow in the wrong lane.
     */
    fun turnLanesOf(o: JSONObject, key: String): String? =
        (o.opt(key) as? String)?.trim()?.takeIf {
            it.length <= MAX_TURN_LANES && TURN_LANES.matches(it) && it.count { c -> c == '|' } < MAX_TURN_LANES_LANES
        }

    /** T-1167: a level only if the house sent a whole number; 0 otherwise, clamped to ±[MAX_LAYER]. */
    fun layerOf(o: JSONObject): Int = (o.opt("layer") as? Int)?.coerceIn(-MAX_LAYER, MAX_LAYER) ?: 0

    /** Footprints from `/api/maps/buildings`; its rings are GeoJSON, `[lon, lat]`. */
    fun buildings(o: JSONObject): List<HoloBuilding> {
        val arr = o.optJSONArray("buildings") ?: return emptyList()
        val out = ArrayList<HoloBuilding>(arr.length())
        for (i in 0 until arr.length()) {
            val b = arr.optJSONObject(i) ?: continue
            val ring = b.optJSONArray("ring") ?: continue
            if (ring.length() < 3) continue
            val pts = ArrayList<LatLon>(ring.length())
            for (j in 0 until ring.length()) pts += point(ring.optJSONArray(j), lonFirst = true) ?: break
            if (pts.size != ring.length()) continue
            val h = if (b.isNull("height_m")) Double.NaN else b.optDouble("height_m")
            out += HoloBuilding(pts, if (h.isFinite() && h > 0.0) h.toFloat() else null, look(b))
        }
        return out
    }

    /** One `[x, y]` pair as a point, `null` when it is not a valid one; the map pack reads its geometry with it too. */
    internal fun point(a: JSONArray?, lonFirst: Boolean): LatLon? {
        if (a == null || a.length() != 2) return null
        val x = a.optDouble(0)
        val y = a.optDouble(1)
        val (lat, lon) = if (lonFirst) y to x else x to y
        return if (valid(lat, lon)) LatLon(lat, lon) else null
    }

    private fun valid(lat: Double, lon: Double) =
        lat.isFinite() && lon.isFinite() && lat in -90.0..90.0 && lon in -180.0..180.0
}

/**
 * Which route the drive follows, and when to ask the house for a new one (T-0613).
 *
 * A route is asked for when a destination is set, when there is none yet (every
 * [MIN_ASK_MS] until one comes), when the car has been more than [OFF_ROUTE_M] from it
 * for [OFF_ROUTE_MS] (a wrong turn, not a GPS wobble), once the car is [PASSED_M] past
 * the turn (the house answers one maneuver at a time, so the next one needs a new route
 * from here), and when the driver says "reroute". One question is out at a time; an answer
 * to an older one is dropped. A failure keeps the old route. Within [ARRIVED_M] of the end
 * the destination is done and the ribbon goes.
 *
 * [show] gets each new ribbon (or `null`) on the caller's thread. Not thread-safe: the
 * screen calls it on the main thread only.
 *
 * The route is kept on the phone (T-1016): with a [store] it is written when a route comes
 * (guidance starts or changes), never on a fix, and removed on arrival, cancel or a new
 * destination, so a guide made after an app or car-session restart [restore]s it and goes on
 * with the house away. Without it a house that is down at the wrong moment, or a head unit
 * that reconnects, loses the drive's route. A failed question never touches it.
 *
 * Not covered: via points (the house routes from here to there only); the nearest segment
 * is taken over the whole route, so a route that doubles back on itself can project the
 * car onto the wrong leg; no traffic; a route of its own, with the house away, only from
 * the roads of a map pack the phone holds ([PackRouter], T-1026: the screen asks it, this class
 * only sees the answer; a far destination joins this route's own line again, T-1047) and none outside them (it keeps asking); progress is worked out again from the next fix,
 * so a restored guide shows none until there is one.
 */
class RouteGuide(
    private val store: Store? = null,
    /** Wall-clock milliseconds: a stored route outlives the process, so the uptime clock will not do. */
    private val clock: () -> Long = System::currentTimeMillis,
    private val show: (HoloRoute?) -> Unit,
) {
    /** One string slot that outlives the process. Reads and writes never throw. */
    interface Store {
        fun read(): String?

        /** `null` removes it. */
        fun write(json: String?)
    }

    data class Request(val id: Int, val from: LatLon, val to: LatLon)

    /** [turnM] is `null` when the house did not say where the next maneuver is. */
    class Progress(val turnM: Float?, val remainingM: Float, val remainingS: Float)

    enum class Failure { NO_ROUTE, UNAVAILABLE }

    var destination: LatLon? = null; private set
    var label = ""; private set
    var route: HouseRoute? = null; private set
    var failure: Failure? = null; private set
    val routing: Boolean get() = pending != null

    private var pending: Request? = null
    private var pendingMs = 0L
    private var nextId = 1
    private var askedMs = NEVER
    private var offSinceMs = -1L

    /** A restored route is a guess until the first fix says the drive is not already over. */
    private var provisional = false
    private var lastAt: LatLon? = null
    private var closed = false

    // The route in a plane anchored at its first point.
    private var plane: GeoPlane? = null
    private var e = FloatArray(0)
    private var n = FloatArray(0)
    private var s = FloatArray(0)
    private var count = 0
    private var turnS = -1f
    private var projS = 0f
    private var projOff = 0f

    /** A new destination: the old route goes, and the house is asked at once when the car is somewhere. */
    fun go(to: LatLon, label: String, from: LatLon?, now: Long): Request? {
        clear()
        destination = to
        this.label = label
        askedMs = NEVER
        return if (from == null) null else ask(from, to, now)
    }

    fun cancel() {
        clear()
        destination = null
        label = ""
    }

    /** The driver asked: a new route from here now, whatever is out. */
    fun reroute(from: LatLon, now: Long): Request? {
        val to = destination ?: return null
        return ask(from, to, now)
    }

    /** Each fix (matched where it could be): the question to send the house, if one is due. */
    fun onPosition(at: LatLon, now: Long): Request? {
        val to = destination ?: return null
        lastAt = at
        if (pending != null && now - pendingMs < PENDING_MAX_MS) return null
        if (route == null) return if (now - askedMs >= MIN_ASK_MS) ask(at, to, now) else null
        val p = plane ?: return null
        val ae = p.east(at.lon)
        val an = p.north(at.lat)
        // A drive that ended 40 m from the pin never came within ARRIVED_M: its kept route would
        // be followed, and the house asked for a way to a place already reached, next session.
        if (provisional) {
            provisional = false
            if (hypot(ae - e[count - 1], an - n[count - 1]) <= NEAR_END_M) {
                cancel()
                return null
            }
        }
        if (hypot(ae - e[count - 1], an - n[count - 1]) <= ARRIVED_M) {
            cancel()
            return null
        }
        project(ae, an)
        if (projOff > OFF_ROUTE_M) {
            if (offSinceMs < 0) offSinceMs = now
            return if (now - offSinceMs >= OFF_ROUTE_MS && now - askedMs >= MIN_ASK_MS) ask(at, to, now) else null
        }
        offSinceMs = -1L
        val passed = turnS >= 0f && projS > turnS + PASSED_M
        return if (passed && now - askedMs >= MIN_ASK_MS) ask(at, to, now) else null
    }

    /** The screen stopped: a drive that ends near its end is over, and must not come back next session. */
    fun stopped() {
        val at = lastAt ?: return
        val p = plane ?: return
        if (route == null) return
        if (hypot(p.east(at.lon) - e[count - 1], p.north(at.lat) - n[count - 1]) <= NEAR_END_M) cancel()
    }

    /** The screen is gone: an answer still on its way must not write the store of a guide nobody reads. */
    fun close() {
        closed = true
    }

    /**
     * Take back the route the last guide kept; `false` (and the entry removed) when it is
     * older than [STORED_MAX_MS], saved in the future (the clock was set back) or does not
     * read. Does nothing when a destination or a question is already there. Call it once,
     * before the first fix; the route is dropped at that fix if it is near its end.
     */
    fun restore(): Boolean {
        if (destination != null || pending != null) return false
        val json = runCatching { store?.read() }.getOrNull() ?: return false
        val kept = runCatching {
            val o = JSONObject(json)
            val saved = o.getLong("saved_ms")
            val to = MapsJson.latLon(o.getJSONArray("destination"))
            val r = MapsJson.route(o.getJSONObject("route"))
            val age = clock() - saved
            if (to == null || r == null || age < 0 || age > STORED_MAX_MS) null else Triple(to, o.optString("label"), r)
        }.getOrNull()
        if (kept == null) {
            keep(null)
            return false
        }
        val (to, name, r) = kept
        destination = to
        label = name
        adopt(r)
        provisional = true
        return true
    }

    private fun keep(json: JSONObject?) {
        runCatching { store?.write(json?.toString()) }
    }

    /** The house's answer to [id]; `false` when it answers a question no longer asked. */
    fun onRoute(id: Int, answer: HouseRoute): Boolean {
        if (pending?.id != id) return false
        pending = null
        val to = destination
        provisional = false
        // A route that will not encode (an infinite length) is still guided, only not kept: the
        // route kept for this destination stays, a worse guide than a new one but not none.
        // After close() the next session may already own the entry; a late answer must not overwrite it.
        if (to != null && !closed) runCatching {
            keep(JSONObject().put("saved_ms", clock()).put("destination", MapsJson.pair(to))
                .put("label", label).put("route", MapsJson.encode(answer)))
        }
        adopt(answer)
        return true
    }

    private fun adopt(answer: HouseRoute) {
        failure = null
        offSinceMs = -1L
        val p = GeoPlane(answer.shape[0].lat, answer.shape[0].lon)
        val size = answer.shape.size
        if (e.size < size) {
            e = FloatArray(size); n = FloatArray(size); s = FloatArray(size)
        }
        for (i in 0 until size) {
            e[i] = p.east(answer.shape[i].lon)
            n[i] = p.north(answer.shape[i].lat)
            s[i] = if (i == 0) 0f else s[i - 1] + hypot(e[i] - e[i - 1], n[i] - n[i - 1])
        }
        count = size
        plane = p
        turnS = -1f
        answer.turn?.at?.let { at ->
            val te = p.east(at.lon)
            val tn = p.north(at.lat)
            var best = Float.MAX_VALUE
            for (i in 0 until size) {
                val d = hypot(e[i] - te, n[i] - tn)
                if (d < best) { best = d; turnS = s[i] }
            }
        }
        route = answer
        show(answer.holo())
    }

    /** [id] failed: [noRoute] is the house's 404, anything else is the house or Valhalla away. */
    fun onFailed(id: Int, noRoute: Boolean) {
        if (pending?.id != id) return
        pending = null
        failure = if (noRoute) Failure.NO_ROUTE else Failure.UNAVAILABLE
    }

    /** How far to the turn and the end, from where [at] projects onto the route. */
    fun progress(at: LatLon): Progress? {
        val r = route ?: return null
        val p = plane ?: return null
        project(p.east(at.lon), p.north(at.lat))
        val total = s[count - 1]
        // The house's length, not the plane's: over a long route the flat plane drifts.
        val share = if (total > 0f) ((total - projS) / total).coerceIn(0f, 1f) else 0f
        val turnM = if (turnS >= 0f) (turnS - projS).coerceAtLeast(0f) else null
        return Progress(turnM, r.distanceM * share, r.durationS * share)
    }

    private fun ask(from: LatLon, to: LatLon, now: Long): Request {
        askedMs = now
        pendingMs = now
        return Request(nextId++, from, to).also { pending = it }
    }

    // The one place a route is let go on purpose (go, cancel, arrival): a failed question never gets here.
    private fun clear() {
        keep(null)
        provisional = false
        pending = null
        failure = null
        offSinceMs = -1L
        count = 0
        plane = null
        turnS = -1f
        if (route != null) {
            route = null
            show(null)
        }
    }

    /** The nearest point on the route to ([pe], [pn]): its distance along it and off it. */
    private fun project(pe: Float, pn: Float) {
        var best = Float.MAX_VALUE
        var bestS = 0f
        for (i in 0 until count - 1) {
            val dx = e[i + 1] - e[i]
            val dy = n[i + 1] - n[i]
            val len2 = dx * dx + dy * dy
            val t = if (len2 > 0f) (((pe - e[i]) * dx + (pn - n[i]) * dy) / len2).coerceIn(0f, 1f) else 0f
            val d = hypot(e[i] + t * dx - pe, n[i] + t * dy - pn)
            if (d < best) {
                best = d
                bestS = s[i] + t * (s[i + 1] - s[i])
            }
        }
        projS = bestS
        projOff = best
    }

    companion object {
        /** Further than a lane or two from the route is off it... */
        const val OFF_ROUTE_M = 40f

        /** ...for this long: a GPS wobble or an urban canyon is shorter. */
        const val OFF_ROUTE_MS = 5_000L

        /** The least time between two questions the guide asks on its own. */
        const val MIN_ASK_MS = 10_000L

        /** Past the turn by this much, it is behind the car. */
        const val PASSED_M = 15f

        /** This close to the end, the car is there. */
        const val ARRIVED_M = 30f

        /** The house's own timeout is shorter; a question older than this was lost. */
        const val PENDING_MAX_MS = 30_000L

        /** A kept route older than this is a past drive's, not this one's. */
        const val STORED_MAX_MS = 12 * 60 * 60 * 1000L

        /** A restored route's first fix, or the last one at stop, this near its end: the drive was over. */
        const val NEAR_END_M = 300f

        private const val NEVER = Long.MIN_VALUE / 2

        private const val KEY = "guided_route"

        /** Its own file, so a route a few hundred KB long is not rewritten with every other setting change. */
        const val PREFS_NAME = "car_route"

        /**
         * The route's slot, in [PREFS_NAME]. No backup rule to add: `data_extraction_rules.xml`
         * excludes the whole `sharedpref` domain, so this file leaves the device by no route.
         */
        fun storeIn(context: android.content.Context): Store = object : Store {
            private val prefs get() = context.applicationContext.getSharedPreferences(PREFS_NAME, android.content.Context.MODE_PRIVATE)
            override fun read(): String? = runCatching { prefs.getString(KEY, null) }.getOrNull()
            override fun write(json: String?) {
                runCatching { prefs.edit().apply { if (json == null) remove(KEY) else putString(KEY, json) }.apply() }
            }
        }

        /** "90 m", "4.2 km", "12 km": metres to the nearest ten, kilometres to one place under ten. */
        fun metres(m: Float): String {
            val tens = (m / 10f).roundToInt() * 10
            return when {
                tens < 1000 -> "$tens m"
                m < 9_950f -> String.format(Locale.ROOT, "%.1f km", m / 1000f)
                else -> "${(m / 1000f).roundToInt()} km"
            }
        }

        /** "12 min", "1 h 5 min": never "0 min" for a trip not yet over. */
        fun duration(seconds: Float): String {
            val min = (seconds / 60f).roundToInt().coerceAtLeast(1)
            val h = min / 60
            val m = min % 60
            return when {
                h == 0 -> "$m min"
                m == 0 -> "$h h"
                else -> "$h h $m min"
            }
        }
    }
}
