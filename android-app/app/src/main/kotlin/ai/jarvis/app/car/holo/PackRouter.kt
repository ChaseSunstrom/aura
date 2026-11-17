package ai.jarvis.app.car.holo

import java.util.PriorityQueue
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * A route over the roads of the map packs the phone holds (T-1026), for a drive whose house does
 * not answer: the same [HouseRoute] the guide and the scene already take. A graph is built per
 * ask from the pack roads in a box round the two ends, then A* by time (a road's length over its
 * posted or usual speed). Roads join at shared vertices (equal to about 0.1 m), a one-way
 * street is driven one way, and each end is put on the nearest drivable road within [SNAP_M].
 *
 * Bounded on purpose: the box is the two ends plus a margin and never wider than a pack read
 * allows (a longer trip answers `null`, as a house away does, unless a kept line [rejoin]s it), the search stops at [MAX_NODES]
 * expanded nodes or [BUDGET_MS], and a pack is data, not code: a road of a kind that is not
 * driven is left out, and one with a point that is not a place never got this far ([MapPack]).
 *
 * A box whose read reached the pack's row cap ([MapPack.LIMIT]) answers `null`: the graph would
 * have silent holes, and a dense city trip may be refused for it.
 *
 * An end for which [covered] says no (the pack has no roads there: [MapPacks.covers] on the
 * point, T-1048) answers `null` too, so the caller asks the house instead of being sent on a
 * detour. Only the two ends are asked, not each stretch of the path between them.
 *
 * A road the pack marks [PackRoad.closed] (access private or no, T-1049) is no way through: it is
 * left out of the search unless an end was put on it, and an end is put on one only when it is
 * nearer than every open road (a destination up a private drive, not one at its foot on the
 * street). A trip whose only way is a closed road answers `null`, so the house is asked.
 *
 * Not covered: turn restrictions, traffic, ferries, tolls, and the maneuvers
 * after the first: the route holds the next one only, as the house's does, and the guide asks
 * again once the car is past it. Blocking: call it off the main thread.
 */
object PackRouter {
    /** Further than this from every road, an end is not on the map. */
    const val SNAP_M = 150.0

    /** Nodes the search may expand; a trip that needs more answers `null`. */
    const val MAX_NODES = 200_000

    const val BUDGET_MS = 3_000L

    /** A bend of more than this at a junction, or a change of street, is a maneuver. */
    const val TURN_DEG = 35.0

    private const val U_TURN_DEG = 150.0
    private const val MARGIN_DEG = 0.005
    private const val MAX_SPAN_DEG = MapPack.MAX_SPAN_DEG - 1e-6
    /** How far ahead of the car on the kept line a far reroute may join it, nearest first. */
    private val JOIN_AHEAD_M = doubleArrayOf(100.0, 600.0, 2500.0)
    private const val MAX_KPH = 130.0
    private const val MIN_KPH = 5.0
    private const val MIN_EDGE_M = 0.5f

    /** An end this near a vertex of a road is on it. */
    private const val VERTEX_M = 0.5

    /** Roads tried for each end; the nearest may be a stub that joins nothing. */
    private const val SNAPS = 4

    /** Getting from where one is to the road, and from the road to the door: a crawl, so a nearer road is worth a little. */
    private const val SNAP_MPS = 2.0

    /** The roads a car drives, and their speed when the pack posts none. */
    private val USUAL_KPH = mapOf(
        "motorway" to 100, "trunk" to 80, "primary" to 60, "secondary" to 50, "tertiary" to 45,
        "unclassified" to 40, "residential" to 30, "living_street" to 15, "service" to 20, "road" to 30,
        "motorway_link" to 50, "trunk_link" to 45, "primary_link" to 40, "secondary_link" to 40, "tertiary_link" to 35,
    )

    /** A route and every maneuver on it, in order; the last is the arrival. */
    class Plan(val route: HouseRoute, val turns: List<HouseRoute.Turn>)

    /** [roads] answers the pack roads in a box (west, south, east, north). */
    fun route(
        from: LatLon,
        to: LatLon,
        roads: (Double, Double, Double, Double) -> List<PackRoad>,
        budgetMs: Long = BUDGET_MS,
        kept: HouseRoute? = null,
        covered: (LatLon) -> Boolean = { true },
    ): HouseRoute? = plan(from, to, roads, budgetMs, kept = kept, covered = covered)?.route

    /**
     * The route from [from] to [to] and its maneuvers, or `null`: no path, an end off the map, a budget spent.
     *
     * A [to] beyond the span of one pack read is refused, unless the line [kept] (the route the
     * drive was following) runs on to it: then the route is the way back onto that line at the
     * nearest point ahead of [from] within the span, and the rest of the line (T-1047, [rejoin]).
     */
    fun plan(
        from: LatLon,
        to: LatLon,
        roads: (Double, Double, Double, Double) -> List<PackRoad>,
        budgetMs: Long = BUDGET_MS,
        maxNodes: Int = MAX_NODES,
        kept: HouseRoute? = null,
        covered: (LatLon) -> Boolean = { true },
    ): Plan? {
        val deadline = System.nanoTime() + budgetMs * 1_000_000L
        if (!(from.lat.isFinite() && from.lon.isFinite() && to.lat.isFinite() && to.lon.isFinite())) return null
        val dLat = abs(from.lat - to.lat)
        val dLon = abs(from.lon - to.lon)
        if (dLat > MAX_SPAN_DEG || dLon > MAX_SPAN_DEG) return kept?.let { rejoin(from, it, roads, deadline, maxNodes, covered) }
        // An end where the extract has no roads is off the map: the house is asked, not a detour built (T-1048).
        if (!covered(from) || !covered(to)) return null
        val padLat = min(max(MARGIN_DEG, 0.3 * dLat), (MAX_SPAN_DEG - dLat) / 2)
        val padLon = min(max(MARGIN_DEG, 0.3 * dLon), (MAX_SPAN_DEG - dLon) / 2)
        val got = roads(
            min(from.lon, to.lon) - padLon, min(from.lat, to.lat) - padLat,
            max(from.lon, to.lon) + padLon, max(from.lat, to.lat) + padLat,
        )
        // A read cut short at its row cap leaves roads out, so a path over it may be a long way
        // round or none: better no route than a wrong one.
        if (got is CutRoads || got.size >= MapPack.LIMIT) return null
        val g = Graph(GeoPlane(from.lat, from.lon))
        for ((i, r) in got.withIndex()) {
            if ((i and 255) == 0 && System.nanoTime() > deadline) return null
            g.add(r)
        }
        if (System.nanoTime() > deadline) return null
        val starts = g.snap(from)
        val goals = g.snap(to)
        if (starts.isEmpty() || goals.isEmpty()) return null
        g.allow(starts + goals)
        g.splice(starts + goals)
        // The nearest road to an end may be a stub nothing joins, or a long way round: every start
        // is searched from at once, each at its own cost of getting to the road at a crawl, and the
        // quickest way to any end, its own crawl from the road added, is kept. One search, not one
        // per pair: 16 of them on a grid near MAX_NODES cost 1.0 to 1.3 s (T-1088, PackRouterWorstCaseTest).
        return g.search(starts, goals, deadline, maxNodes)?.let(g::plan)
    }

    /**
     * The way from [from] back onto the line [kept] and along the rest of it (T-1047): the pack
     * route to the nearest vertex at least [JOIN_AHEAD_M] ahead of where [from] projects onto the
     * line (the car is moving, and a point at its door is behind it by the time the answer is),
     * then the line's own vertices to its end. Further joins are tried when no road leads to the
     * first, and `null` is the answer when no vertex ahead is within the span of one read, so the
     * refusal stays as it was.
     *
     * The rest of the line keeps its length and time by the share of it that is left, and the one
     * maneuver the kept route held if that is ahead of the join; the others it never held (a route
     * holds the next one only), and the guide asks again once the car is past it.
     */
    private fun rejoin(
        from: LatLon,
        kept: HouseRoute,
        roads: (Double, Double, Double, Double) -> List<PackRoad>,
        deadline: Long,
        maxNodes: Int,
        covered: (LatLon) -> Boolean,
    ): Plan? {
        val line = kept.shape
        if (line.size < 2) return null
        // A plane of doubles anchored at the car: the line may be long, and only its order matters here.
        val perLon = GeoPlane.M_PER_DEG * cos(Math.toRadians(from.lat))
        val e = DoubleArray(line.size) { (line[it].lon - from.lon) * perLon }
        val n = DoubleArray(line.size) { (line[it].lat - from.lat) * GeoPlane.M_PER_DEG }
        val s = DoubleArray(line.size)
        for (i in 1 until line.size) s[i] = s[i - 1] + hypot(e[i] - e[i - 1], n[i] - n[i - 1])
        val total = s.last()
        if (!(total > 0.0)) return null
        var nearest = Double.MAX_VALUE
        var projS = 0.0
        for (i in 0 until line.size - 1) {
            val dx = e[i + 1] - e[i]
            val dy = n[i + 1] - n[i]
            val len2 = dx * dx + dy * dy
            val t = if (len2 > 0.0) ((-e[i] * dx - n[i] * dy) / len2).coerceIn(0.0, 1.0) else 0.0
            val d = hypot(e[i] + t * dx, n[i] + t * dy)
            if (d < nearest) {
                nearest = d
                projS = s[i] + t * (s[i + 1] - s[i])
            }
        }
        var tried = -1
        for (ahead in JOIN_AHEAD_M) {
            val j = (1 until line.size).firstOrNull { s[it] >= projS + ahead } ?: break
            // Later vertices are only further off: none within the span, none to join.
            if (abs(line[j].lat - from.lat) > MAX_SPAN_DEG || abs(line[j].lon - from.lon) > MAX_SPAN_DEG) break
            if (j == tried) continue
            tried = j
            val left = (deadline - System.nanoTime()) / 1_000_000L
            if (left <= 0) return null
            val piece = plan(from, line[j], roads, left, maxNodes, covered = covered) ?: continue
            // The kept maneuver, if it is on the part of the line that is kept (an arrival is at its end).
            val at = kept.turn?.at
            val k = at?.let { a -> line.indices.minBy { hypot(e[it] - (a.lon - from.lon) * perLon, n[it] - (a.lat - from.lat) * GeoPlane.M_PER_DEG) } }
            val tail = kept.turn?.takeIf { k != null && s[k] >= s[j] }
            val turns = piece.turns.dropLast(1) + listOfNotNull(tail) +
                (if (tail != null && k == line.size - 1) emptyList() else listOf(HouseRoute.Turn("Arrive", "", line.last())))
            val share = ((total - s[j]) / total).toFloat()
            val route = HouseRoute(
                piece.route.shape + line.subList(j + 1, line.size),
                piece.route.distanceM + kept.distanceM * share, piece.route.durationS + kept.durationS * share,
                piece.route.street, turns.first(),
            )
            return Plan(route, turns)
        }
        return null
    }

    /** A point on road segment [seg], [t] of the way along it, [d] metres from what was asked; [node] is what it became once spliced in. */
    private class Snap(val seg: Int, val t: Double, val d: Double) {
        var node = -1
    }

    private class Graph(val plane: GeoPlane) {
        val lat = ArrayList<Double>()
        val lon = ArrayList<Double>()

        /** Segments meeting at each node: more than two is a junction, two is a bend or a join. */
        val degree = ArrayList<Int>()
        private val ids = HashMap<Long, Int>()
        private val roads = ArrayList<PackRoad>()

        // Edges as linked lists per node, grown by doubling.
        private var head = IntArray(1024) { -1 }
        private var eNext = IntArray(1024)
        private var eFrom = IntArray(1024)
        private var eTo = IntArray(1024)
        private var eRoad = IntArray(1024)
        private var eM = FloatArray(1024)
        private var eS = FloatArray(1024)
        private var edges = 0
        private var fastest = 1.0

        // Segment i runs along road segRoad[i] from node segA[i] to node segB[i], in the line's order.
        private val segA = ArrayList<Int>()
        private val segB = ArrayList<Int>()
        private val segRoad = ArrayList<Int>()

        private fun speed(r: PackRoad): Double =
            (if (r.kph > 0) r.kph.toDouble() else USUAL_KPH.getValue(r.kind).toDouble()).coerceIn(MIN_KPH, MAX_KPH) / 3.6

        fun add(r: PackRoad) {
            if (r.kind !in USUAL_KPH || r.line.size < 2) return
            val mps = speed(r)
            fastest = max(fastest, mps)
            val road = roads.size
            roads += r
            var prev = node(r.line[0])
            for (i in 1 until r.line.size) {
                val next = node(r.line[i])
                if (next == prev) continue
                segA += prev; segB += next; segRoad += road
                degree[prev]++; degree[next]++
                link(prev, next, road, mps, r.oneway)
                prev = next
            }
        }

        private fun node(p: LatLon): Int {
            val key = (Math.round(p.lat * 1e6) shl 32) xor (Math.round(p.lon * 1e6) and 0xffffffffL)
            return ids.getOrPut(key) { newNode(p.lat, p.lon) }
        }

        private fun newNode(la: Double, lo: Double): Int {
            lat += la; lon += lo; degree += 0
            if (head.size < lat.size) {
                val old = head.size
                head = head.copyOf(old * 2).also { java.util.Arrays.fill(it, old, it.size, -1) }
            }
            return lat.size - 1
        }

        private fun link(a: Int, b: Int, road: Int, mps: Double, oneway: Int) {
            if (oneway >= 0) edge(a, b, road, mps)
            if (oneway <= 0) edge(b, a, road, mps)
        }

        private fun edge(a: Int, b: Int, road: Int, mps: Double) {
            if (edges == eTo.size) {
                val n = edges * 2
                eNext = eNext.copyOf(n); eFrom = eFrom.copyOf(n); eTo = eTo.copyOf(n)
                eRoad = eRoad.copyOf(n); eM = eM.copyOf(n); eS = eS.copyOf(n)
            }
            val m = metres(a, b)
            eFrom[edges] = a; eTo[edges] = b; eRoad[edges] = road; eM[edges] = m.toFloat(); eS[edges] = (m / mps).toFloat()
            eNext[edges] = head[a]; head[a] = edges++
        }

        private fun metres(a: Int, b: Int): Double = hypot(
            (lon[b] - lon[a]) * GeoPlane.M_PER_DEG * cos(Math.toRadians((lat[a] + lat[b]) / 2)),
            (lat[b] - lat[a]) * GeoPlane.M_PER_DEG,
        )

        /** The nearest drivable point of each of the [SNAPS] nearest roads within [SNAP_M], nearest first; empty when none is. */
        fun snap(p: LatLon): List<Snap> {
            val pe = plane.east(p.lon).toDouble()
            val pn = plane.north(p.lat).toDouble()
            val best = HashMap<Int, Pair<Double, Snap>>()
            for (i in segA.indices) {
                val ae = plane.east(lon[segA[i]]).toDouble()
                val an = plane.north(lat[segA[i]]).toDouble()
                val dx = plane.east(lon[segB[i]]) - ae
                val dy = plane.north(lat[segB[i]]) - an
                val len2 = dx * dx + dy * dy
                val t = if (len2 > 0) (((pe - ae) * dx + (pn - an) * dy) / len2).coerceIn(0.0, 1.0) else 0.0
                val d = hypot(ae + t * dx - pe, an + t * dy - pn)
                // One per road: the next segments of the same road are no other way to go.
                if (d < SNAP_M && d < (best[segRoad[i]]?.first ?: Double.MAX_VALUE)) best[segRoad[i]] = d to Snap(i, t, d)
            }
            // T-1049: a closed road is an end's own road only when it is nearer than every open one: at the foot of a private
            // drive, on the street, the street is the end's road and the drive is not a way for it. With no open road in reach, any is.
            val sorted = best.values.sortedBy { it.first }
            val nearestOpen = sorted.firstOrNull { !roads[segRoad[it.second.seg]].closed }?.first ?: Double.MAX_VALUE
            return sorted.filter { !roads[segRoad[it.second.seg]].closed || it.first < nearestOpen }.take(SNAPS).map { it.second }
        }

        /**
         * Makes each snap a node of its own on its segment, chained along it in order so two ends
         * on one street have a direct way between them, and a one-way street still only one way.
         * The segment's own edge stays: a path through the segment's ends is still a path.
         */
        fun splice(snaps: List<Snap>) {
            for ((seg, group) in snaps.groupBy { it.seg }) {
                val a = segA[seg]
                val b = segB[seg]
                val road = roads[segRoad[seg]]
                val len = metres(a, b)
                var prev = a
                for (s in group.sortedBy { it.t }) {
                    // On a vertex, the vertex itself: a node of its own there would be joined only
                    // the way a one-way street runs and cut off from the roads that meet at it.
                    if (s.t * len < VERTEX_M) { s.node = a; continue }
                    if ((1 - s.t) * len < VERTEX_M) { s.node = b; continue }
                    s.node = newNode(lat[a] + (lat[b] - lat[a]) * s.t, lon[a] + (lon[b] - lon[a]) * s.t)
                    link(prev, s.node, segRoad[seg], speed(road), road.oneway)
                    prev = s.node
                }
                if (prev != a) link(prev, b, segRoad[seg], speed(road), road.oneway)
            }
        }

        /** Closed roads (T-1049) a path may use: those an end was snapped to, so a drive to a door on one is not refused. */
        private val allowed = HashSet<Int>()

        fun allow(snaps: List<Snap>) {
            for (s in snaps) if (roads[segRoad[s.seg]].closed) allowed += segRoad[s.seg]
        }

        /**
         * The edges of the quickest drive from any of [starts] to any of [goals], each start at the
         * crawl from its point to the road ([Snap.d] at [SNAP_MPS]) and each goal likewise, or
         * `null`: none, or past the node or time budget with no end reached. Past the budget, the
         * best end reached so far stands.
         *
         * An end is reached by an edge, and not by a path that began on that node: a start and a goal
         * on one node are no drive (an empty path would win at no cost, and out and back along a road
         * is a lap of the block); a start may still reach another start's goal node. Each node keeps
         * the origin of its best path only, so a dearer path from another start into a node whose
         * best path is a lap is not weighed.
         */
        fun search(starts: List<Snap>, goals: List<Snap>, deadlineNs: Long, maxNodes: Int): List<Int>? {
            val n = lat.size
            val cost = DoubleArray(n) { Double.MAX_VALUE }
            val via = IntArray(n) { -1 }
            // The start node each node's best path began at: an end is no end for a path that began on it.
            val origin = IntArray(n) { -1 }
            val done = BooleanArray(n)
            val exit = DoubleArray(n) { Double.MAX_VALUE }
            for (g in goals) exit[g.node] = min(exit[g.node], g.d / SNAP_MPS)
            val goalNodes = goals.map { it.node }.distinct().toIntArray()
            val queue = PriorityQueue<Pair<Double, Int>>(compareBy { it.first })
            // The least any drive on from i can cost: the nearest end by a straight line, plus its crawl. A least of
            // consistent bounds is consistent, so a node popped is final.
            fun guess(i: Int): Double {
                var least = Double.MAX_VALUE
                for (g in goalNodes) least = min(least, metres(i, g) / fastest + exit[g])
                return least
            }
            for (s in starts) {
                val entry = s.d / SNAP_MPS
                if (entry < cost[s.node]) {
                    cost[s.node] = entry
                    origin[s.node] = s.node
                    queue.add((entry + guess(s.node)) to s.node)
                }
            }
            var bestS = Double.MAX_VALUE
            var bestEdge = -1
            var expanded = 0
            while (true) {
                val (key, at) = queue.poll() ?: break
                // Nothing left can beat the end already reached.
                if (key >= bestS) break
                if (done[at]) continue
                done[at] = true
                expanded++
                if (expanded > maxNodes || ((expanded and 255) == 1 && System.nanoTime() > deadlineNs)) break
                var e = head[at]
                while (e >= 0) {
                    val to = eTo[e]
                    val c = cost[at] + eS[e]
                    // A closed road is no way through: left out unless an end is on it.
                    val shut = roads[eRoad[e]].closed && eRoad[e] !in allowed
                    if (!shut) {
                        if (exit[to] != Double.MAX_VALUE && origin[at] != to && c + exit[to] < bestS) {
                            bestS = c + exit[to]
                            bestEdge = e
                        }
                        if (!done[to] && c < cost[to]) {
                            cost[to] = c
                            via[to] = e
                            origin[to] = origin[at]
                            queue.add((c + guess(to)) to to)
                        }
                    }
                    e = eNext[e]
                }
            }
            if (bestEdge < 0) return null
            val out = arrayListOf(bestEdge)
            var at = eFrom[bestEdge]
            while (via[at] >= 0) {
                out += via[at]
                at = eFrom[via[at]]
            }
            return out.asReversed()
        }

        /** The route along [path] (edges, in order), with a maneuver where the street changes or the road turns at a junction. */
        fun plan(path: List<Int>): Plan? {
            if (path.isEmpty()) return null
            val shape = ArrayList<LatLon>(path.size + 1)
            shape += LatLon(lat[eFrom[path[0]]], lon[eFrom[path[0]]])
            var metres = 0.0
            var seconds = 0.0
            val turns = ArrayList<HouseRoute.Turn>()
            for ((i, e) in path.withIndex()) {
                val a = eFrom[e]
                val b = eTo[e]
                shape += LatLon(lat[b], lon[b])
                metres += eM[e]
                seconds += eS[e]
                // A snap that fell on a vertex leaves a zero-length edge, which has no bearing.
                if (i == 0 || eM[e] < MIN_EDGE_M || eM[path[i - 1]] < MIN_EDGE_M) continue
                val name = roads[eRoad[e]].name.trim()
                val renamed = name != roads[eRoad[path[i - 1]]].name.trim()
                val angle = signedTurn(bearing(eFrom[path[i - 1]], a), bearing(a, b))
                val bend = abs(angle) > TURN_DEG && (degree[a] > 2 || renamed)
                // A road that loses its name is not a maneuver; a bend on it still is.
                if (!bend && (!renamed || name.isEmpty())) continue
                turns += HouseRoute.Turn(words(angle, name, renamed), name, LatLon(lat[a], lon[a]), side(angle))
            }
            if (metres <= 0.0) return null
            turns += HouseRoute.Turn("Arrive", "", shape.last())
            val street = path.map { roads[eRoad[it]].name.trim() }.firstOrNull { it.isNotEmpty() }.orEmpty()
            return Plan(HouseRoute(shape, metres.toFloat(), seconds.toFloat(), street, turns.first()), turns)
        }

        private fun bearing(a: Int, b: Int): Double = Math.toDegrees(
            atan2((lon[b] - lon[a]) * cos(Math.toRadians(lat[a])), lat[b] - lat[a]),
        )

        /** Degrees to turn, right positive, in -180..180. */
        private fun signedTurn(was: Double, now: Double): Double = ((now - was + 540.0) % 360.0 + 360.0) % 360.0 - 180.0

        /** The turn's side for a reader that words it itself (the console's `sayStep`, T-1075). */
        private fun side(angle: Double): String = when {
            abs(angle) >= U_TURN_DEG -> "uturn"
            abs(angle) <= TURN_DEG -> "straight"
            angle > 0 -> "right"
            else -> "left"
        }

        private fun words(angle: Double, street: String, renamed: Boolean): String {
            val onto = if (street.isEmpty()) "" else " onto $street"
            return when {
                abs(angle) >= U_TURN_DEG -> "Make a U-turn$onto"
                abs(angle) <= TURN_DEG -> if (renamed) "Continue$onto" else "Continue"
                angle > 0 -> "Turn right$onto"
                else -> "Turn left$onto"
            }
        }
    }
}
