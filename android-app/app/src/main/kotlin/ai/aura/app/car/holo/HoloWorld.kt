package ai.aura.app.car.holo

import ai.aura.app.ui.theme.JarvisTokens.Color
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.math.tan

/**
 * The place itself, drawn solid (T-1060; the owner, 2026-11-11: "instead of buildings being
 * blocks, can we have the actual shape and color of them and all of their details? Almost
 * like a direct camera view, same for all of the environment, like trees, road lines, parking
 * spots, light posts, trees, construction").
 *
 * What a map pack holds since T-1066 becomes opaque, lit, coloured polygons in a [HoloFrame]'s
 * solid layer, which the painter draws far to near under the scene's light (the route, the
 * car, the names): the ground's water, green and parking, each road's surface with its lane
 * lines, crossings and parking bays, every building in its real outline with shaded walls,
 * windows and a roof, and trees, lamps, signals and roadworks where the map has them.
 *
 * A colour is the map's where it gives one. It rarely does (a few buildings in a thousand),
 * so the rest take a usual colour for their kind, varied a little per building: believable,
 * not surveyed. Nothing that moves or changes is drawn (no other cars, no people, no signal
 * showing red or green): no source reports them, and a made-up one is a lie a driver might
 * act on.
 *
 * Plain Kotlin, tested on the JVM. [select] sorts and may allocate and runs when the car has
 * moved a few metres; [build] runs every frame and allocates nothing.
 *
 * Does not cover: occlusion by a depth buffer (polygons are ordered by their distance, so
 * two long shapes that pass through each other can draw in the wrong order); a roof's real
 * form beyond one ridge (a pitched one rises to a point over the middle, or to one ridge where
 * the map gives a direction; no dormers, domes or two slopes of different pitch); a building
 * drawn in parts whose parts cover under [COVER_SHARE] of its outline (the outline is drawn
 * with them); photographs of anything.
 */
class HoloWorld {
    private var buildings: List<HoloBuilding> = emptyList()
    private var roads: List<HoloRoad> = emptyList()
    private var details: List<HoloDetail> = emptyList()
    private var live: List<HoloDetail> = emptyList()

    private var bCount = 0
    private val bRing = Array(MAX_BUILDINGS) { FloatArray(2 * 8) }
    private val bLen = IntArray(MAX_BUILDINGS)
    private val bTop = FloatArray(MAX_BUILDINGS)
    private val bBase = FloatArray(MAX_BUILDINGS)
    private val bRise = FloatArray(MAX_BUILDINGS)
    private val bCcw = BooleanArray(MAX_BUILDINGS)
    private val bWall = IntArray(MAX_BUILDINGS)
    private val bRoof = IntArray(MAX_BUILDINGS)
    private val bGlazed = BooleanArray(MAX_BUILDINGS)
    /** The building's own number, from where it stands: the same however many stand nearer (T-1107). */
    private val bSeed = IntArray(MAX_BUILDINGS)
    private val bMidE = FloatArray(MAX_BUILDINGS)
    private val bMidN = FloatArray(MAX_BUILDINGS)
    // T-1106: the business's name, its board's colour, whether it is a home, whether its walls are
    // panels (else courses), and which wall faces the street (-1: none worth a front).
    private val bSign = arrayOfNulls<String>(MAX_BUILDINGS)
    private val bFascia = IntArray(MAX_BUILDINGS)
    private val bHome = BooleanArray(MAX_BUILDINGS)
    private val bPanel = BooleanArray(MAX_BUILDINGS)
    private val bFront = IntArray(MAX_BUILDINGS)
    // T-1185: a mapped ridge's two ends (east, north each), NaN with none; and the buildings whose parts stand for them.
    private val bRidge = FloatArray(MAX_BUILDINGS * 4)
    private var covered = BooleanArray(0)
    /** Specks on the road and the grass near the car: east, north and a tone each (T-1106). */
    private var fleckCount = 0
    private val flecks = FloatArray(MAX_FLECKS * 2)
    private val fleckColor = IntArray(MAX_FLECKS)

    /** Road pieces, nearest first, [PIECE] floats each: both ends, half the width, the class, the lanes, [ONE_WAY], [KERBED], [BRIDGE] and [APPROACH], a deck's height at each end, and the way's length to each end, and its place in [arrows] (-1 for none, T-1235). */
    private var pieces = FloatArray(0)
    private var pieceCount = 0

    /** T-1235: the lane turns painted at the ends of a piece (its start, its end; null for none), nearest piece first. */
    private val arrows = arrayOfNulls<Array<IntArray?>>(MAX_ARROWS)
    private var arrowCount = 0

    private var aCount = 0
    private val aRing = Array(MAX_AREAS) { FloatArray(2 * 8) }
    private val aLen = IntArray(MAX_AREAS)
    private val aColor = IntArray(MAX_AREAS)
    private val aSite = BooleanArray(MAX_AREAS)
    /** Land cover (T-1188): farmland, homes, trade; drawn under every other area, and capped so it cannot crowd a lake out. */
    private val aCover = BooleanArray(MAX_AREAS)
    private var coverCount = 0

    private var bayCount = 0
    private val bays = Array(MAX_BAYS) { FloatArray(2 * MAX_BAY_RING) }
    private val bayLen = IntArray(MAX_BAYS)

    private var treeCount = 0
    private val trees = FloatArray(MAX_TREES * 3)
    private var lampCount = 0
    private val lamps = FloatArray(MAX_LAMPS * 2)
    private var signalCount = 0
    /** Four floats each (T-1237): where the pole stands, then where the map put the signal. */
    private val signals = FloatArray(MAX_SIGNALS * 4)
    private var crossCount = 0
    private val crossings = FloatArray(MAX_CROSSINGS * 5)
    private var barrierCount = 0
    private val barriers = FloatArray(MAX_BARRIERS * 4)
    private var postCount = 0
    private val posts = FloatArray(MAX_POSTS * 3)
    private val postText = arrayOfNulls<String>(MAX_POSTS)
    // T-1104: stop lines (where, the road's way, the bar's offset and half width), rails, name blades.
    private var barCount = 0
    private val bars = FloatArray(MAX_BARS * 6)
    private var railCount = 0

    /** A closed road's ends (T-1108): where, and which way the road runs there. */
    private var closureCount = 0
    private val closures = FloatArray(MAX_CLOSURES * 4)
    private val rails = FloatArray(MAX_RAILS * 4)
    private var bladeCount = 0
    private val blades = FloatArray(MAX_BLADES * 2)
    private val bladeNames = arrayOfNulls<String>(MAX_BLADES * 2)
    // T-1183: the way each name's street runs there (east, north), for the blade to face along.
    private val bladeWays = FloatArray(MAX_BLADES * 4)

    /** A vertex two named streets share: where, the first street's way out of it and its half width. */
    private class Corner(val e: Float, val n: Float, var ue: Float, var un: Float, var half: Float) {
        // Each street's name, and the way that street runs out of this vertex.
        val names = LinkedHashMap<String, FloatArray>()
        // The ways out of it, by bearing: the same way read from two map tiles is still one.
        val arms = HashSet<Int>()
    }

    // T-1167: a numbered road's shields (where, and the number), and which pieces are a bridge's.
    // T-1183: the number is without its network, which is the marker's shape ([routeMark]).
    private class Shield(val e: Float, val n: Float, val d: Float, val text: String, val net: String)
    private var shieldCount = 0
    private val shields = FloatArray(MAX_SHIELDS * 2)
    private val shieldText = arrayOfNulls<String>(MAX_SHIELDS)
    private val shieldNet = arrayOfNulls<String>(MAX_SHIELDS)
    // A guide board where a link with something on its sign leaves a road: where, and its rows of words.
    private class Guide(val e: Float, val n: Float, val d: Float, val rows: List<String>)
    private var guideCount = 0
    private val guides = FloatArray(MAX_GUIDES * 2)
    private val guideRows = arrayOfNulls<List<String>>(MAX_GUIDES)
    private var deckCount = 0
    private var decks = IntArray(0)
    // The bridge piece being drawn (-1: the ground), and the distance all of it is ordered by.
    private var deck = -1
    private var deckDepth = RAISED

    // T-1184: a tunnel's mouths (east, north, the way out of it, the road's half width), which
    // buildings stand over a tunnel, and whether the car is in one this frame.
    private var portalCount = 0
    private val portals = FloatArray(MAX_PORTALS * 5)
    private val bOver = BooleanArray(MAX_BUILDINGS)
    private var inTunnel = false
    private var tunnelCount = 0
    private val tunnelAt = IntArray(MAX_TUNNEL_PIECES)

    // Per frame.
    private lateinit var frame: HoloFrame
    private lateinit var cam: HoloCamera
    // The ground's height (T-1105): the cells held, and the square picked round the car.
    private var cells: Map<Long, HoloGroundCell> = emptyMap()
    private val gh = FloatArray(G * G)
    private var gE0 = 0f
    private var gN0 = 0f
    private var flat = true
    private var carUp = 0f
    private var pinned = Float.NaN
    private var landCount = 0

    private var pe = 0f
    private var pn = 0f
    private var sinH = 0f
    private var cosH = 1f
    private var dashes = 0
    private var windows = 0
    private var wallLines = 0
    /** How many one-quad window rows the last frame drew on far walls (T-1185); for tests. */
    internal var rows = 0; private set

    // The light (T-1107). Unclocked, the scene is the night it always was, lamps on.
    private var clocked = false
    /** 0 in the dark to 1 in full day. */
    var day = 0f; private set
    private var night = 1f
    private var sunE = SUN_E
    private var sunN = SUN_N
    /** Metres of shadow a metre of height throws (T-1164); 0 at night and with no clock. */
    private var reach = 0f
    /** Set round a solid that gives light of its own (a pool, a lit window): day does not brighten it. */
    private var glows = false
    /** How many windows of the last frame had a light on; for tests. */
    var litWindows = 0; private set

    /**
     * Light the world by a sun [elevationDeg] over the horizon at [azimuthDeg] clockwise from
     * north: daylight brightens every surface, walls and slopes facing the sun are the lit
     * ones, and lamps and windows come on as it goes. Under the end of civil twilight nothing
     * says where the light is from, so walls keep the side they always had. null: no clock,
     * and the scene is as it was before it had one.
     */
    fun light(elevationDeg: Double?, azimuthDeg: Double = 0.0) {
        clocked = elevationDeg != null
        day = if (elevationDeg == null) 0f else HoloLight.daylight(elevationDeg)
        night = 1f - day
        reach = if (elevationDeg == null) 0f else shadowReach(elevationDeg)
        // The side that is lit turns from the one it always was to the sun's as the light comes
        // up through twilight, not in one step at its edge.
        val w = min(1f, day * SUN_TURN)
        val e = SUN_E + (sin(Math.toRadians(azimuthDeg)).toFloat() - SUN_E) * w
        val n = SUN_N + (cos(Math.toRadians(azimuthDeg)).toFloat() - SUN_N) * w
        val len = sqrt(e * e + n * n)
        // Half way between two opposite bearings there is no side: the light is flat for that minute.
        sunE = if (len > 0.001f) e / len * min(1f, len * 4f) else 0f
        sunN = if (len > 0.001f) n / len * min(1f, len * 4f) else 0f
    }
    private var shopSigns = 0

    private var polyIn = FloatArray(3 * 96)
    private var polyCam = FloatArray(3 * 96)
    private var polyOut = FloatArray(3 * 97)

    /** Each stop line the last [select] kept, for the tests: where, the road's way, its offset to the right and its half width. */
    internal val stopLines: List<FloatArray> get() = (0 until barCount).map { bars.copyOfRange(it * 6, it * 6 + 6) }

    /** Off, a solid keeps its own colour at any distance: a test counting colours cannot tell a far canopy from a near shaded one. */
    var hazy = true

    /** Whether a road surface is drawn, so the scene leaves out its own board road and grid. */
    val paved: Boolean get() = pieceCount > 0

    /** How many of each thing the last [select] kept, for the tests and the log. */
    val counts: IntArray get() = intArrayOf(
        bCount, pieceCount, aCount, bayCount, treeCount, lampCount, signalCount, crossCount, barrierCount, postCount,
        barCount, railCount, bladeCount, landCount, fleckCount, closureCount, shieldCount, guideCount,
    )

    /** Where each kept route marker stands (east, north), for the tests. */
    internal val shieldSpots: List<FloatArray> get() = (0 until shieldCount).map { floatArrayOf(shields[it * 2], shields[it * 2 + 1]) }

    /** The way each name on each kept blade's street runs (east, north), blade by blade, for the tests. */
    internal val bladeFaces: List<FloatArray> get() = (0 until bladeCount * 2).filter { bladeNames[it] != null }.map { bladeWays.copyOfRange(it * 2, it * 2 + 2) }

    /** The wall each kept building fronts the street with (-1: none), nearest building first, for the tests. */
    internal val fronts: IntArray get() = bFront.copyOf(bCount)

    fun setBuildings(list: List<HoloBuilding>) { buildings = list; covered = cover(list) }

    /** Each picked building's ridge as east, north, east, north (NaN: none), nearest first, for the tests. */
    internal val ridges: FloatArray get() = bRidge.copyOf(bCount * 4)

    /**
     * Which outlines are drawn by their parts instead (T-1185): the pack holds a building drawn
     * in parts as its outline and each part, and both drawn stood the same walls twice. An
     * outline goes when the parts whose middles lie in it cover [COVER_SHARE] of it; parts
     * covering less leave it standing, so a lone annex does not take its house away. Works in
     * degrees: both areas share the same scale, so their ratio is true.
     */
    private fun cover(list: List<HoloBuilding>): BooleanArray {
        val out = BooleanArray(list.size)
        val grid = HashMap<Long, ArrayList<Int>>()
        fun cell(v: Double) = Math.floor(v / COVER_CELL_DEG).toLong()
        fun key(x: Long, y: Long) = x * 1_000_003L + y
        var parts = 0
        for (i in list.indices) {
            if (!list[i].look.part || list[i].ring.size < 3) continue
            val r = list[i].ring
            grid.getOrPut(key(cell(r.sumOf { it.lon } / r.size), cell(r.sumOf { it.lat } / r.size))) { ArrayList() } += i
            parts++
        }
        if (parts == 0) return out
        for (i in list.indices) {
            val r = list[i].ring
            if (list[i].look.part || r.size < 3) continue
            val lo = r.minOf { it.lon }; val hi = r.maxOf { it.lon }
            val la = r.minOf { it.lat }; val lb = r.maxOf { it.lat }
            val whole = degArea(r)
            if (whole == 0.0) continue
            var sum = 0.0
            for (x in cell(lo)..cell(hi)) for (y in cell(la)..cell(lb)) {
                for (j in grid[key(x, y)] ?: continue) {
                    val q = list[j].ring
                    val mx = q.sumOf { it.lon } / q.size
                    val my = q.sumOf { it.lat } / q.size
                    if (mx in lo..hi && my in la..lb && inside(r, mx, my)) sum += degArea(q)
                }
            }
            out[i] = sum >= COVER_SHARE * whole
        }
        return out
    }

    private fun degArea(r: List<LatLon>): Double {
        // From the first corner: degrees are large beside a building, and the products would lose it.
        var a = 0.0
        for (k in r.indices) {
            val b = r[(k + 1) % r.size]
            a += (r[k].lon - r[0].lon) * (b.lat - r[0].lat) - (b.lon - r[0].lon) * (r[k].lat - r[0].lat)
        }
        return Math.abs(a) / 2
    }

    private fun inside(r: List<LatLon>, x: Double, y: Double): Boolean {
        var in_ = false
        for (k in r.indices) {
            val a = r[k]; val b = r[(k + 1) % r.size]
            if ((a.lat > y) != (b.lat > y) && x < (b.lon - a.lon) * (y - a.lat) / (b.lat - a.lat) + a.lon) in_ = !in_
        }
        return in_
    }

    fun setRoads(list: List<HoloRoad>) { roads = list }

    fun setDetails(list: List<HoloDetail>) { details = list }

    /**
     * What the house says is under way today (T-1108): "roadworks" along a line, drawn as the
     * mapped ones are, and "closure", the same with the road barred across at each end.
     */
    fun setLive(list: List<HoloDetail>) { live = list }

    /** The ground's heights (T-1105); with none, or none under the car, the place is drawn flat. */
    fun setGround(list: List<HoloGroundCell>) { cells = list.associateBy { key(it.cx, it.cy) } }

    /** Whether the place picked has ground that rises and falls. */
    val hilly: Boolean get() = !flat

    /**
     * Metres the ground at the plane's ([e], [n]) stands above the ground under the car, as of
     * the last [build]; 0 on a flat map. Past the picked square it is the square's edge.
     */
    fun rise(e: Float, n: Float): Float = ground(e, n) - carUp

    /**
     * [rise], or a bridge's deck there (T-1167) for something going the way ([he], [hn]): the
     * route's line over a bridge lies on its deck, and under one stays on the ground. With no
     * way given any deck over the point counts.
     */
    fun riseOn(e: Float, n: Float, he: Float = 0f, hn: Float = 0f): Float =
        deckAt(e, n, he, hn).let { if (it.isNaN()) ground(e, n) else it } - carUp

    /** Whether anything stands above the level ground: heights, or a bridge. The route's line is cut to follow it. */
    val raised: Boolean get() = !flat || deckCount > 0

    private fun ground(e: Float, n: Float): Float = if (flat) 0f else up(e, n)

    /**
     * A bridge deck's height at ([e], [n]) when one is under it, else NaN (T-1167). With a
     * heading ([he], [hn]) only a bridge running that way counts: a car passing under a
     * bridge is not on it.
     */
    private fun deckAt(e: Float, n: Float, he: Float, hn: Float): Float {
        for (i in 0 until deckCount) {
            val o = decks[i] * PIECE
            val dx = pieces[o + 2] - pieces[o]
            val dy = pieces[o + 3] - pieces[o + 1]
            val l2 = dx * dx + dy * dy
            if (l2 == 0f) continue
            val t = ((e - pieces[o]) * dx + (n - pieces[o + 1]) * dy) / l2
            if (t < 0f || t > 1f) continue
            if (hypot(e - (pieces[o] + dx * t), n - (pieces[o + 1] + dy * t)) > pieces[o + 4] + DECK_EDGE_M) continue
            if ((he != 0f || hn != 0f) && abs(he * dx + hn * dy) < ON_DECK_COS * sqrt(l2) * hypot(he, hn)) continue
            return pieces[o + 8] + (pieces[o + 9] - pieces[o + 8]) * t
        }
        return Float.NaN
    }

    /** Picks what is near ([pe], [pn]) in [p] and moves it into the plane. Sorts and may allocate. */
    fun select(p: GeoPlane, pe: Float, pn: Float) {
        selectGround(p, pe, pn)
        selectBuildings(p, pe, pn)
        selectRoads(p, pe, pn)
        selectDetails(p, pe, pn)
        selectFronts(pe, pn)
        selectFlecks(pe, pn)
        selectTunnels(pe, pn)
    }

    private fun selectBuildings(p: GeoPlane, pe: Float, pn: Float) {
        bCount = 0
        if (buildings.isEmpty()) return
        // By its nearest wall, not its first corner: a mall the car is beside starts far away.
        // Sorted as (distance bits, index) longs: a thousand footprints a pick, none boxed. A
        // distance is never negative, so its float bits sort as the distance does.
        val keys = LongArray(buildings.size)
        var near = 0
        for (i in buildings.indices) {
            if (i < covered.size && covered[i]) continue
            val r = buildings[i].ring
            var best = Float.MAX_VALUE
            for (k in r.indices) {
                val a = r[k]
                val b = r[(k + 1) % r.size]
                best = min(best, toPiece(pe, pn, p.east(a.lon), p.north(a.lat), p.east(b.lon), p.north(b.lat)))
            }
            if (best <= RADIUS) keys[near++] = (best.toRawBits().toLong() shl 32) or i.toLong()
        }
        java.util.Arrays.sort(keys, 0, near)
        for (q in 0 until near) {
            if (bCount == MAX_BUILDINGS) break
            val i = (keys[q] and 0xFFFFFFFFL).toInt()
            val b = buildings[i]
            val kept = ring(p, b.ring, MAX_RING_CORNERS, bRing, bCount)
            if (kept < 3) continue
            val r = bRing[bCount]
            val area = area(r, kept)
            if (area == 0f) continue
            var me = 0f
            var mn = 0f
            for (k in 0 until kept) { me += r[k * 2]; mn += r[k * 2 + 1] }
            val look = b.look
            val base = look.minM.coerceIn(0f, MAX_HEIGHT_M)
            var top = (b.heightM ?: HoloScene.DEFAULT_HEIGHT_M).coerceIn(MIN_HEIGHT_M, MAX_HEIGHT_M)
            val home = look.kind in HOMES
            val seed = (r[0] * 7.3f + r[1] * 3.1f).toInt()
            bLen[bCount] = kept
            bSeed[bCount] = seed
            bCcw[bCount] = area > 0f
            bBase[bCount] = if (base < top - 1f) base else 0f
            bTop[bCount] = top
            bMidE[bCount] = me / kept
            bMidN[bCount] = mn / kept
            bWall[bCount] = if (look.colour != 0) look.colour else vary(wallFor(look.kind), seed)
            val pitched = look.roofShape in PITCHED ||
                (look.roofShape.isEmpty() && (look.roofM > 0f || home && kept <= PITCH_MAX_RING && kotlin.math.abs(area) / 2 < PITCH_MAX_M2))
            bRise[bCount] = if (pitched) (sqrt(kotlin.math.abs(area) / 2) * 0.22f).coerceIn(1.2f, 4.5f) else 0f
            bRidge.fill(Float.NaN, bCount * 4, bCount * 4 + 4)
            if (pitched && look.roofM > 0f) {
                // A mapped roof height is part of the building's own (OSM's height runs to the
                // ridge): the walls stop where it starts, at least half the height up.
                val wallTop = max(top - look.roofM, top / 2)
                bRise[bCount] = top - wallTop
                bTop[bCount] = wallTop
                top = wallTop
            }
            if (pitched) ridge(bCount, r, kept, look, me / kept, mn / kept)
            bRoof[bCount] = if (look.roofColour != 0) look.roofColour else vary(if (pitched) Color.WORLD_ROOF_HOME else Color.WORLD_ROOF, seed)
            bGlazed[bCount] = look.kind !in WORKS
            bSign[bCount] = look.sign.ifEmpty { null }
            bFascia[bCount] = fasciaFor(look.trade)
            bHome[bCount] = home
            bPanel[bCount] = look.kind in TRADE || look.kind in WORKS
            bFront[bCount] = -1
            bCount++
        }
    }

    /**
     * Sets building [slot]'s ridge (T-1185) where the map gives the roof a direction: its slope
     * faces `roof:direction`, so the ridge runs across that; or its orientation, `along` or
     * `across` the longest wall. The ridge lies through the middle of the footprint's width and
     * as long as it (a gable), or shortened by half the width at each end (a hip). Leaves NaN
     * with neither, and the roof rises to a point.
     */
    private fun ridge(slot: Int, r: FloatArray, n: Int, look: HoloLook, me: Float, mn: Float) {
        var ue: Float
        var un: Float
        if (look.roofDir >= 0f) {
            val a = (look.roofDir + 90f) * PI.toFloat() / 180f
            ue = sin(a); un = cos(a)
        } else if (look.roofOrient.isNotEmpty()) {
            var best = 0f
            ue = 1f; un = 0f
            for (k in 0 until n) {
                val j = (k + 1) % n
                val de = r[j * 2] - r[k * 2]
                val dn = r[j * 2 + 1] - r[k * 2 + 1]
                val l = hypot(de, dn)
                if (l > best) { best = l; ue = de / l; un = dn / l }
            }
            if (look.roofOrient == "across") { val t = ue; ue = -un; un = t }
        } else return
        var t0 = Float.MAX_VALUE; var t1 = -Float.MAX_VALUE
        var s0 = Float.MAX_VALUE; var s1 = -Float.MAX_VALUE
        for (k in 0 until n) {
            val x = r[k * 2] - me
            val y = r[k * 2 + 1] - mn
            val t = x * ue + y * un
            val s = -x * un + y * ue
            t0 = min(t0, t); t1 = max(t1, t); s0 = min(s0, s); s1 = max(s1, s)
        }
        val half = (s1 - s0) / 2
        var a = t0
        var b = t1
        if (look.roofShape in HIPPED) { a += half; b -= half; if (a > b) { a = (t0 + t1) / 2; b = a } }
        val cs = (s0 + s1) / 2
        val o = slot * 4
        bRidge[o] = me + a * ue - cs * un; bRidge[o + 1] = mn + a * un + cs * ue
        bRidge[o + 2] = me + b * ue - cs * un; bRidge[o + 3] = mn + b * un + cs * ue
    }

    /**
     * The heights of a square round the car, a sample every [G_STEP_M], read once here so a
     * frame's thousands of points each cost four numbers. A point the map has no height for
     * takes the car's, so the edge of what is known is level ground, not a cliff down to the sea.
     */
    private fun selectGround(p: GeoPlane, pe: Float, pn: Float) {
        flat = true
        if (cells.isEmpty()) return
        val here = metres(p.lat(pn), p.lon(pe))
        if (here.isNaN()) return
        // On whole patches of the plane, so a patch of land is the same patch (and the same
        // shade) after the car has moved and the square is picked again.
        val span = MESH * G_STEP_M
        gE0 = floor((pe - G_HALF_M) / span) * span
        gN0 = floor((pn - G_HALF_M) / span) * span
        var lo = here
        var hi = here
        for (j in 0 until G) for (i in 0 until G) {
            val v = metres(p.lat(gN0 + j * G_STEP_M), p.lon(gE0 + i * G_STEP_M))
            val m = if (v.isNaN()) here else v
            gh[j * G + i] = m
            lo = min(lo, m)
            hi = max(hi, m)
        }
        // Level ground is drawn as it always was: no land, no cut roads.
        flat = hi - lo < LEVEL_M
    }

    private fun key(cx: Int, cy: Int): Long = (cx.toLong() shl 32) or (cy.toLong() and 0xFFFFFFFFL)

    /** Metres above sea level at a place, between its cell's four samples; NaN where no cell is held. */
    private fun metres(lat: Double, lon: Double): Float {
        val x = lon * HoloGroundCell.PER_DEG
        val y = lat * HoloGroundCell.PER_DEG
        val cx = floor(x).toInt()
        val cy = floor(y).toInt()
        val c = cells[key(cx, cy)] ?: return Float.NaN
        val fx = ((x - cx) * (c.n - 1)).toFloat()
        val fy = ((y - cy) * (c.n - 1)).toFloat()
        val i = min(fx.toInt(), c.n - 2)
        val j = min(fy.toInt(), c.n - 2)
        val o = j * c.n + i
        val south = c.h[o] + (c.h[o + 1] - c.h[o]) * (fx - i)
        val north = c.h[o + c.n] + (c.h[o + c.n + 1] - c.h[o + c.n]) * (fx - i)
        return south + (north - south) * (fy - j)
    }

    /** Metres above sea level at the plane's ([e], [n]), from the picked square. */
    private fun up(e: Float, n: Float): Float {
        val x = ((e - gE0) / G_STEP_M).coerceIn(0f, G - 1.001f)
        val y = ((n - gN0) / G_STEP_M).coerceIn(0f, G - 1.001f)
        val i = x.toInt()
        val j = y.toInt()
        val o = j * G + i
        val south = gh[o] + (gh[o + 1] - gh[o]) * (x - i)
        val north = gh[o + G] + (gh[o + G + 1] - gh[o + G]) * (x - i)
        return south + (north - south) * (y - j)
    }

    /** What a point of the car's frame is lifted by: its ground's rise, or the one a building is [pinned] to. */
    private fun lift(x: Float, z: Float): Float = when {
        !pinned.isNaN() -> pinned
        // Level ground is 0, and below the car when the car is up on a bridge.
        flat -> -carUp
        else -> up(pe + x * cosH + z * sinH, pn - x * sinH + z * cosH) - carUp
    }

    /**
     * Open ground, where it has height: the square as a sheet of [MESH] by [MESH] samples a
     * patch, each lit by how its slope faces the sun, which is what makes a hill read as one.
     * Does not hide what is behind a rise: the ground is drawn first and nothing is cut by it.
     */
    private fun land() {
        val span = MESH * G_STEP_M
        for (j in 0 until G - 1 step MESH) for (i in 0 until G - 1 step MESH) {
            val a = gh[j * G + i]
            val b = gh[j * G + i + MESH]
            val c = gh[(j + MESH) * G + i + MESH]
            val d = gh[(j + MESH) * G + i]
            val de = ((b - a) + (c - d)) / (2 * span)
            val dn = ((d - a) + (c - b)) / (2 * span)
            val e0 = gE0 + i * G_STEP_M
            val n0 = gN0 + j * G_STEP_M
            world(0, e0, n0, 0f); world(1, e0 + span, n0, 0f); world(2, e0 + span, n0 + span, 0f); world(3, e0, n0 + span, 0f)
            // Its outward lean is downhill, (-de, -dn): lit when that is towards the sun.
            val lit = (0.5f - (de * sunE + dn * sunN) * SHADE_GAIN).coerceIn(0f, 1f)
            if (solid(4, landTone(lit), FLAT) >= 0f) landCount++
        }
    }

    /**
     * Bare ground (T-1188): one sheet of [Color.WORLD_GROUND] [GROUND_HALF_M] each way from the
     * car, under every other fill, lit with them. Without it ground where nothing is mapped was
     * the sky's own dark, the same grey as tarmac. Does not know the climate: a desert or a
     * snowfield is olive too, the tone kept muted for that.
     */
    private fun ground() {
        val h = GROUND_HALF_M
        // Level with the car: a sheet lifted by the heights at its far corners would tilt through the land.
        pinned = -carUp
        world(0, pe - h, pn - h, 0f); world(1, pe + h, pn - h, 0f); world(2, pe + h, pn + h, 0f); world(3, pe - h, pn + h, 0f)
        pinned = Float.NaN
        solid(4, Color.WORLD_GROUND, FLAT)
    }

    /** Keeps a closed road's end at [end] when it is near; [inward] is the next point along the closure. */
    private fun closure(p: GeoPlane, end: LatLon, inward: LatLon) {
        if (closureCount == MAX_CLOSURES) return
        val e = p.east(end.lon); val n = p.north(end.lat)
        val de = p.east(inward.lon) - e; val dn = p.north(inward.lat) - n
        val len = hypot(de, dn)
        if (len < MIN_SEGMENT_M || hypot(e - pe, n - pn) > NEAR_RADIUS) return
        val o = closureCount++ * 4
        closures[o] = e; closures[o + 1] = n; closures[o + 2] = de / len; closures[o + 3] = dn / len
    }

    /**
     * One bridge's deck (T-1181): the straight line between the ground at its two ends, [lift]
     * metres over it, so it is level on flat ground and follows a slope's own tilt on a hill.
     */
    private class Deck(val ae: Float, val an: Float, val ga: Float, val gb: Float, val de: Float, val dn: Float, val lift: Float) {
        fun at(e: Float, n: Float): Float {
            val l2 = de * de + dn * dn
            val t = if (l2 < 1f) 0f else (((e - ae) * de + (n - an) * dn) / l2).coerceIn(0f, 1f)
            return ga + (gb - ga) * t + lift
        }
    }

    /** The decks by road (null off a bridge) and the metres a road's vertex is lifted by, for those that are. */
    private class Plan(val decks: Array<Deck?>, val lifts: HashMap<LatLon, Float>, val along: FloatArray)

    /** Which road and which vertex of it stand at a point, for every road that is not a bridge or a tunnel; built once for a set of roads. */
    private var adjOf: List<HoloRoad>? = null
    private val adj = HashMap<LatLon, ArrayList<Long>>()

    private fun adjacency(): HashMap<LatLon, ArrayList<Long>> {
        if (adjOf !== roads) {
            adj.clear()
            for (i in roads.indices) {
                val r = roads[i]
                if (r.bridge || r.tunnel) continue
                for (k in r.line.indices) adj.getOrPut(r.line[k]) { ArrayList(2) } += (i.toLong() shl 32) or k.toLong()
            }
            adjOf = roads
        }
        return adj
    }

    /**
     * Each bridge's level deck (T-1181) and the lift that leads to it. Bridge ways that share an end
     * are one deck. The roads at a deck's ends climb to it at [APPROACH_GRADE] at most, walked along
     * the roads that join there. Does not plan a deck none of whose vertices is near, and a deck
     * over steep ground is lifted till it clears the ground under it, so its approaches grow long.
     */
    private fun planDecks(p: GeoPlane, pe: Float, pn: Float): Plan? {
        val ways = ArrayList<Int>()
        for (i in roads.indices) { val r = roads[i]; if (r.bridge && !r.tunnel && r.line.size >= 2) ways += i }
        if (ways.isEmpty()) return null
        val group = IntArray(ways.size) { it }
        fun find(i: Int): Int { var k = i; while (group[k] != k) k = group[k]; return k }
        // T-1191: ways of different layers that meet at a vertex are two decks, one over the other, not one at the higher level.
        val ends = HashMap<Pair<LatLon, Int>, Int>()
        for ((w, i) in ways.withIndex()) for (v in arrayOf(roads[i].line.first(), roads[i].line.last())) {
            val other = ends.getOrPut(v to max(1, roads[i].layer)) { w }
            if (other != w) group[find(w)] = find(other)
        }
        val joined = HashMap<Int, ArrayList<Int>>()
        for ((w, i) in ways.withIndex()) joined.getOrPut(find(w)) { ArrayList() } += i
        val decks = arrayOfNulls<Deck>(roads.size)
        val lifts = HashMap<LatLon, Float>()
        val along = FloatArray(roads.size)
        val queue = ArrayDeque<LatLon>()
        for (members in joined.values) {
            val reach = RADIUS + DECK_AHEAD_M
            if (members.none { i -> roads[i].line.any { hypot(p.east(it.lon) - pe, p.north(it.lat) - pn) <= reach } }) continue
            val ends2 = members.flatMap { listOf(roads[it].line.first(), roads[it].line.last()) }
            val a = ends2[0]
            val ae = p.east(a.lon); val an = p.north(a.lat)
            val b = ends2.maxByOrNull { hypot(p.east(it.lon) - ae, p.north(it.lat) - an) }!!
            val ga = ground(ae, an)
            val gb = ground(p.east(b.lon), p.north(b.lat))
            val de = p.east(b.lon) - ae; val dn = p.north(b.lat) - an
            var length = 0f
            var layer = 1
            for (i in members) {
                val r = roads[i]
                layer = max(layer, r.layer)
                for (k in 1 until r.line.size) {
                    length += hypot(p.east(r.line[k].lon) - p.east(r.line[k - 1].lon), p.north(r.line[k].lat) - p.north(r.line[k - 1].lat))
                }
            }
            // A short span on its own is not a hump steeper than a road is.
            val base = min(DECK_M * layer, length * DECK_GRADE)
            val level = Deck(ae, an, ga, gb, de, dn, 0f)
            // Over a hill the deck still clears what is under it: lift it by the most the ground stands over the line.
            var lift = base
            if (!flat) for (i in members) {
                val r = roads[i]
                for (k in 1 until r.line.size) {
                    val e0 = p.east(r.line[k - 1].lon); val n0 = p.north(r.line[k - 1].lat)
                    val e1 = p.east(r.line[k].lon); val n1 = p.north(r.line[k].lat)
                    val steps = ceil(hypot(e1 - e0, n1 - n0) / DECK_CUT_M).toInt().coerceIn(1, MAX_CUTS)
                    for (s in 0..steps) {
                        val e = e0 + (e1 - e0) * s / steps
                        val n = n0 + (n1 - n0) * s / steps
                        lift = max(lift, base + ground(e, n) - level.at(e, n))
                    }
                }
            }
            val deck = Deck(ae, an, ga, gb, de, dn, lift)
            // T-1191: where each way starts along the deck, so the piers keep one spacing over all its ways. A way drawn
            // against the deck's direction counts from its far end, so its piers fall on the same marks.
            val dl = hypot(de, dn)
            fun at(v: LatLon) = if (dl < 1f) 0f else ((p.east(v.lon) - ae) * de + (p.north(v.lat) - an) * dn) / dl
            val t0 = ends2.minOf { at(it) }
            for (i in members) {
                val f = at(roads[i].line.first()) - t0
                along[i] = if (at(roads[i].line.last()) - t0 >= f) f else PIER_FIRST_M + Math.floorMod(Math.round(-f - PIER_FIRST_M), PIER_EVERY_M.toInt())
            }
            for (i in members) {
                decks[i] = deck
                // Where a way meets a road, the road is as far up as the deck is over the ground there.
                for (v in arrayOf(roads[i].line.first(), roads[i].line.last())) {
                    val e = p.east(v.lon); val n = p.north(v.lat)
                    val up = deck.at(e, n) - ground(e, n)
                    if (up > APPROACH_MIN_M && up > (lifts[v] ?: 0f)) { lifts[v] = up; queue.add(v) }
                }
            }
        }
        if (queue.isNotEmpty()) {
            val roadsAt = adjacency()
            while (queue.isNotEmpty()) {
                val v = queue.removeFirst()
                val lv = lifts[v] ?: continue
                val ve = p.east(v.lon); val vn = p.north(v.lat)
                for (code in roadsAt[v] ?: continue) {
                    val r = roads[(code shr 32).toInt()]
                    val k = (code and 0xffffffffL).toInt()
                    for (j in intArrayOf(k - 1, k + 1)) if (j in r.line.indices) {
                        val w = r.line[j]
                        val next = lv - APPROACH_GRADE * hypot(p.east(w.lon) - ve, p.north(w.lat) - vn)
                        if (next > APPROACH_MIN_M && next > (lifts[w] ?: 0f) + APPROACH_MIN_M) { lifts[w] = next; queue.add(w) }
                    }
                }
            }
        }
        return Plan(decks, lifts, along)
    }

    private fun selectRoads(p: GeoPlane, pe: Float, pn: Float) {
        pieceCount = 0
        barrierCount = 0
        bladeCount = 0
        shieldCount = 0
        guideCount = 0
        deckCount = 0
        if (roads.isEmpty()) { binPieces(); return }
        val near = ArrayList<FloatArray>()
        val arrowsOf = ArrayList<Array<IntArray?>>()
        val corners = HashMap<LatLon, Corner>()
        val numbered = ArrayList<Shield>()
        val boards = ArrayList<Guide>()
        // T-1183: the numbers that meet at each vertex of a numbered street ("" for one without),
        // so a marker stands after a junction or where the number changes, not along a stretch.
        val meets = HashMap<LatLon, HashSet<String>>()
        for (r in roads) if (HoloScene.halfWidth(r.kind) > 0f && numberOf(r).isNotEmpty()) for (v in r.line) meets.getOrPut(v) { HashSet() }
        if (meets.isNotEmpty()) for (r in roads) if (HoloScene.halfWidth(r.kind) > 0f) {
            val number = numberOf(r)
            for (v in r.line) meets[v]?.add(number)
        }
        // T-1181: each near bridge's level deck, and the lift its roads carry down from it.
        val plan = planDecks(p, pe, pn)
        for (ri in roads.indices) {
            val r = roads[ri]
            val street = HoloScene.halfWidth(r.kind)
            val cls = if (street > 0f) STREET else if (r.kind in DRIVES) MINOR else PATH
            // A ramp the map counts no lanes on is one lane (T-1234): with its parent's width and
            // the default two it was a 14 m road with a dashed line where the place has a slip road.
            val ramp = cls == STREET && r.lanes <= 0 && r.kind.endsWith("_link")
            val half = when {
                ramp -> RAMP_HALF_M
                cls == STREET -> max(street, r.lanes * LANE_M / 2)
                cls == MINOR -> MINOR_HALF
                else -> PATH_HALF
            }
            // Lanes the map does not count: two on anything classed, none marked on a back street.
            val lanes = if (r.lanes > 0) r.lanes else if (ramp) 1 else if (cls == STREET && r.kind in MARKED) 2 else 0
            // A tunnel has no pavement and, from outside, no surface (T-1184).
            val flags = (if (r.oneway != 0) ONE_WAY else 0) or (if (r.oneway < 0) ONE_WAY_BACK else 0) or
                (if (r.tunnel) TUNNEL else if (cls == STREET && r.kind.removeSuffix("_link") in TOWN) KERBED else 0) or
                (if (r.kind.removeSuffix("_link") !in MARKED) UNPAINTED else 0)
            // Every street at a vertex counts towards its being a junction, named or not (T-1167).
            if (cls == STREET && !r.tunnel && r.line.size >= 2) for (k in r.line.indices) {
                val e = p.east(r.line[k].lon)
                val n = p.north(r.line[k].lat)
                if (hypot(e - pe, n - pn) > BLADE_RADIUS) continue
                val to = r.line[if (k + 1 < r.line.size) k + 1 else k - 1]
                val de = p.east(to.lon) - e
                val dn = p.north(to.lat) - n
                val len = hypot(de, dn)
                if (len < MIN_SEGMENT_M) continue
                val corner = corners.getOrPut(r.line[k]) { Corner(e, n, de / len, dn / len, half) }
                if (r.name.isNotBlank()) {
                    // The blade stands by the first street with a name, not one nobody named.
                    if (corner.names.isEmpty()) { corner.ue = de / len; corner.un = dn / len; corner.half = half }
                    corner.names.putIfAbsent(whole(r.name.trim(), BLADE_CHARS), floatArrayOf(de / len, dn / len))
                }
                for (j in intArrayOf(k - 1, k + 1)) if (j in r.line.indices) {
                    corner.arms += Math.floorMod(Math.round(atan2(p.north(r.line[j].lat) - n, p.east(r.line[j].lon) - e) / ARM_RAD), ARMS)
                }
            }
            // A bridge's deck (T-1167, T-1181): one level for the whole of its joined ways.
            val deckPlan = if (r.bridge && !r.tunnel && r.line.size >= 2) plan?.decks?.get(ri) else null
            val decked = deckPlan != null
            val lifts = if (plan != null && !decked && !r.tunnel) plan.lifts else null
            val shift = if (decked) plan?.along?.get(ri) ?: 0f else 0f
            val number = if (cls == STREET) numberOf(r) else ""
            val mark = routeMark(number)
            // Where along the line its markers stand (T-1183), nearest the start first.
            val cum = FloatArray(r.line.size)
            for (k in 1 until r.line.size) cum[k] = cum[k - 1] + hypot(p.east(r.line[k].lon) - p.east(r.line[k - 1].lon), p.north(r.line[k].lat) - p.north(r.line[k - 1].lat))
            val stands = if (number.isEmpty() || decked || r.tunnel || r.line.size < 2) emptyList() else standsOf(r, cum, number, meets)
            var si = 0
            // A link that says something where it leaves a road has a board there (T-1183).
            val rows = if (cls == STREET && !r.tunnel && r.kind.endsWith("_link") && r.line.size >= 2) boardRows(r) else emptyList()
            if (rows.isNotEmpty()) {
                val a = if (r.oneway < 0) r.line.size - 1 else 0
                val b = if (r.oneway < 0) a - 1 else 1
                val e0 = p.east(r.line[a].lon); val n0 = p.north(r.line[a].lat)
                val de = p.east(r.line[b].lon) - e0; val dn = p.north(r.line[b].lat) - n0
                val len = hypot(de, dn)
                if (len >= MIN_SEGMENT_M) {
                    val out = half + GUIDE_OUT_M
                    val e = e0 + dn / len * out
                    val n = n0 - de / len * out
                    val d = hypot(e - pe, n - pn)
                    if (d <= NEAR_RADIUS) boards += Guide(e, n, d, rows)
                }
            }
            // T-1235: what each lane may do at the end of the way travelled, by the end it is at: the line's
            // start for a way drawn against its line, and for a two-way road's :backward list.
            val painted = cls == STREET && !r.tunnel && r.line.size >= 2 && (flags and UNPAINTED) == 0
            val startTurns = if (!painted) null else laneTurns(
                if (r.oneway < 0) r.turnLanes else if (r.oneway == 0) r.turnLanesBackward else null, lanes, r.oneway != 0,
            )
            val endTurns = if (!painted) null else laneTurns(
                if (r.oneway > 0) r.turnLanes else if (r.oneway == 0) r.turnLanesForward else null, lanes, r.oneway != 0,
            )
            var run = 0f
            for (k in 1 until r.line.size) {
                val e0 = p.east(r.line[k - 1].lon)
                val n0 = p.north(r.line[k - 1].lat)
                val e1 = p.east(r.line[k].lon)
                val n1 = p.north(r.line[k].lat)
                // Over ground with height a long straight is cut, so it lies on the rise between its ends.
                val seg = hypot(e1 - e0, n1 - n0)
                // A road that leads to a deck climbs to it: the lift at each end, fading along it (T-1181).
                val lu = lifts?.get(r.line[k - 1]) ?: 0f
                val lv = lifts?.get(r.line[k]) ?: 0f
                val lifted = lu > 0f || lv > 0f
                // A deck is cut finer still, flat ground or not: its climb is a curve.
                val parts = if (decked || lifted) ceil(seg / DECK_CUT_M).toInt().coerceIn(1, MAX_CUTS)
                else if (flat) 1 else ceil(seg / CUT_M).toInt().coerceIn(1, MAX_CUTS)
                for (c in 0 until parts) {
                    val ae = e0 + (e1 - e0) * c / parts
                    val an = n0 + (n1 - n0) * c / parts
                    val be = e0 + (e1 - e0) * (c + 1) / parts
                    val bn = n0 + (n1 - n0) * (c + 1) / parts
                    val d = toPiece(pe, pn, ae, an, be, bn)
                    if (d > RADIUS) continue
                    val t0 = seg * c / parts
                    val t1 = seg * (c + 1) / parts
                    val l0 = max(0f, max(lu - APPROACH_GRADE * t0, lv - APPROACH_GRADE * (seg - t0)))
                    val l1 = max(0f, max(lu - APPROACH_GRADE * t1, lv - APPROACH_GRADE * (seg - t1)))
                    val climbs = lifted && max(l0, l1) > APPROACH_MIN_M
                    // The piece at an end of the line carries that end's arrows: its slot is a place in [arrowsOf], or -1.
                    val atStart = startTurns != null && k == 1 && c == 0
                    val atEnd = endTurns != null && k == r.line.size - 1 && c == parts - 1
                    var arrow = -1f
                    if (atStart || atEnd) {
                        arrowsOf += arrayOf(if (atStart) startTurns else null, if (atEnd) endTurns else null)
                        arrow = (arrowsOf.size - 1).toFloat()
                    }
                    near += floatArrayOf(
                        ae, an, be, bn, half, cls.toFloat(), lanes.toFloat(),
                        (flags or (if (decked) BRIDGE else if (climbs) BRIDGE or APPROACH else 0)).toFloat(),
                        if (deckPlan != null) deckPlan.at(ae, an) else if (climbs) ground(ae, an) + l0 else 0f,
                        if (deckPlan != null) deckPlan.at(be, bn) else if (climbs) ground(be, bn) + l1 else 0f,
                        shift + run + t0, shift + run + t1, arrow, d,
                    )
                }
                // A numbered road says its number on a shield to the right of the way travelled,
                // at the stands [standsOf] found; not on a bridge, where the post would stand on
                // the ground under the deck.
                if (si < stands.size && seg >= MIN_SEGMENT_M) while (si < stands.size && stands[si].at <= run + seg) {
                    val t = ((stands[si].at - run) / seg).coerceIn(0f, 1f)
                    val out = (half + BLADE_OUT_M) * if (stands[si].left) -1f else 1f
                    val e = e0 + (e1 - e0) * t + (n1 - n0) / seg * out
                    val n = n0 + (n1 - n0) * t - (e1 - e0) / seg * out
                    val d = hypot(e - pe, n - pn)
                    if (d <= NEAR_RADIUS && !lifted) numbered += Shield(e, n, d, mark.second, mark.first)
                    si++
                }
                run += seg
            }
        }
        // A blade stands on the corner between the first street and the one to its right.
        // T-1167: also where a named street meets any other, so its one name is said.
        val signed = corners.values.filter { it.names.size >= 2 || (it.names.isNotEmpty() && it.arms.size >= 3) }
        for (c in signed.sortedBy { hypot(it.e - pe, it.n - pn) }.take(MAX_BLADES)) {
            val out = c.half + BLADE_OUT_M
            blades[bladeCount * 2] = c.e + (c.ue + c.un) * out
            blades[bladeCount * 2 + 1] = c.n + (c.un - c.ue) * out
            val names = c.names.entries.iterator()
            for (k in 0..1) {
                val at = bladeCount * 2 + k
                val named = if (names.hasNext()) names.next() else null
                bladeNames[at] = named?.key
                bladeWays[at * 2] = named?.value?.get(0) ?: 0f
                bladeWays[at * 2 + 1] = named?.value?.get(1) ?: 0f
            }
            bladeCount++
        }
        // The nearest shields, and one number once in a stretch: two carriageways are one road.
        for (c in numbered.sortedBy { it.d }) {
            if (shieldCount == MAX_SHIELDS) break
            if ((0 until shieldCount).any { shieldText[it] == c.text && shieldNet[it] == c.net && hypot(shields[it * 2] - c.e, shields[it * 2 + 1] - c.n) < SHIELD_APART_M }) continue
            shields[shieldCount * 2] = c.e
            shields[shieldCount * 2 + 1] = c.n
            shieldNet[shieldCount] = c.net
            shieldText[shieldCount++] = c.text
        }
        // The nearest boards; a link that carries on past its first piece says the same, once.
        for (g in boards.sortedBy { it.d }) {
            if (guideCount == MAX_GUIDES) break
            if ((0 until guideCount).any { guideRows[it] == g.rows && hypot(guides[it * 2] - g.e, guides[it * 2 + 1] - g.n) < GUIDE_APART_M }) continue
            guides[guideCount * 2] = g.e
            guides[guideCount * 2 + 1] = g.n
            guideRows[guideCount++] = g.rows
        }
        near.sortBy { it[PIECE] }
        pieceCount = min(near.size, MAX_PIECES)
        if (pieces.size < pieceCount * PIECE) pieces = FloatArray(pieceCount * PIECE)
        for (i in 0 until pieceCount) near[i].copyInto(pieces, i * PIECE, 0, PIECE)
        // T-1235: the nearest pieces keep their arrows, so a cap drops the far ones, not whatever came last.
        arrowCount = 0
        for (i in 0 until pieceCount) {
            val at = pieces[i * PIECE + ARROW_SLOT].toInt()
            if (at >= 0 && arrowCount < MAX_ARROWS) {
                arrows[arrowCount] = arrowsOf[at]
                pieces[i * PIECE + ARROW_SLOT] = (arrowCount++).toFloat()
            } else {
                pieces[i * PIECE + ARROW_SLOT] = -1f
            }
        }
        if (decks.size < pieceCount) decks = IntArray(pieceCount)
        for (i in 0 until pieceCount) if (bridge(i * PIECE)) decks[deckCount++] = i
        binPieces()
    }

    /**
     * Which wall of a near shop or home faces the street: the one whose middle, a step out from
     * the wall, is nearest a street (a driveway only when no street is near); with no road near, its longest. Does not know a corner shop
     * has two fronts, or which side its door is really on.
     */
    private fun selectFronts(pe: Float, pn: Float) {
        // Only the road pieces a near front could face: the pieces are nearest first, so they are the first few.
        val roadsNear = piecesWithin(pe, pn, FRONT_RADIUS + FRONT_REACH_M + MAX_FRONT_HALF_M)
        for (i in 0 until bCount) {
            if ((bSign[i] == null && !bHome[i]) || hypot(bMidE[i] - pe, bMidN[i] - pn) > FRONT_RADIUS) continue
            val r = bRing[i]
            val n = bLen[i]
            var best = Float.MAX_VALUE
            for (k in 0 until n) {
                val j = (k + 1) % n
                val de = r[j * 2] - r[k * 2]
                val dn = r[j * 2 + 1] - r[k * 2 + 1]
                val len = hypot(de, dn)
                if (len < DOOR_WALL_M) continue
                val oe = (if (bCcw[i]) dn else -dn) / len
                val on = (if (bCcw[i]) -de else de) / len
                val me = (r[k * 2] + r[j * 2]) / 2 + oe * FRONT_STEP_M
                val mn = (r[k * 2 + 1] + r[j * 2 + 1]) / 2 + on * FRONT_STEP_M
                var d = 3 * FRONT_REACH_M + 1f / len  // no road in reach: the longest wall
                for (q in 0 until roadsNear) {
                    val o = q * PIECE
                    if (pieces[o + 5].toInt() == PATH || tunnel(o)) continue
                    val to = toPiece(me, mn, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3])
                    // A street before a driveway: a shop beside its own car park still faces the street.
                    if (to < FRONT_REACH_M) d = min(d, if (pieces[o + 5].toInt() == STREET) to else to + FRONT_REACH_M)
                }
                if (d < best) { best = d; bFront[i] = k }
            }
        }
    }

    /**
     * Specks on the asphalt and the grass round the car, so a surface is not one flat tone. They
     * sit on fixed points of the plane (the same every drive), off the painted lines.
     */
    private fun selectFlecks(pe: Float, pn: Float) {
        fleckCount = 0
        val roadsNear = piecesWithin(pe, pn, FLECK_RADIUS * 1.5f + MAX_FRONT_HALF_M)
        val e0 = floor((pe - FLECK_RADIUS) / FLECK_STEP_M).toInt()
        val n0 = floor((pn - FLECK_RADIUS) / FLECK_STEP_M).toInt()
        val side = (2 * FLECK_RADIUS / FLECK_STEP_M).toInt()
        for (a in e0..e0 + side) for (b in n0..n0 + side) {
            // Mixed before its low bits are read: unmixed, they repeat along a diagonal and the specks line up.
            val hash = hash(a, b)
            if (hash and 1 != 0 || fleckCount == MAX_FLECKS) continue
            val e = (a + ((hash shr 4) and 15) / 16f) * FLECK_STEP_M
            val n = (b + ((hash shr 8) and 15) / 16f) * FLECK_STEP_M
            val tone = if (hash and 16 == 0) FLECK_LIGHT else FLECK_DARK
            var color = 0
            for (q in 0 until roadsNear) {
                val o = q * PIECE
                if (pieces[o + 5].toInt() == PATH || tunnel(o)) continue
                val half = pieces[o + 4]
                val off = toPiece(e, n, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3])
                if (off > half) continue
                // On a road: a speck only clear of the centre, lane and edge lines.
                val lane = off % LANE_M
                color = if (off < half - 2 * EDGE_IN_M && lane > FLECK_CLEAR_M && lane < LANE_M - FLECK_CLEAR_M) {
                    shade(if (pieces[o + 5].toInt() == STREET) Color.WORLD_ASPHALT else Color.WORLD_ASPHALT_MINOR, tone)
                } else 0
                break
            }
            if (color == 0 && onRoad(e, n, roadsNear)) continue
            if (color == 0) for (i in 0 until aCount) {
                if (aColor[i] != Color.WORLD_GREEN || !inside(aRing[i], aLen[i], e, n)) continue
                color = shade(Color.WORLD_GREEN, tone)
                break
            }
            if (color == 0) continue
            flecks[fleckCount * 2] = e
            flecks[fleckCount * 2 + 1] = n
            fleckColor[fleckCount++] = color
        }
    }

    /** How many of the pieces, which are kept nearest first, come within [reach] of the point they were picked round. */
    private fun piecesWithin(pe: Float, pn: Float, reach: Float): Int {
        for (q in 0 until pieceCount) {
            val o = q * PIECE
            if (toPiece(pe, pn, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3]) > reach) return q
        }
        return pieceCount
    }

    /** Whether the point is on any road's surface: grass under a road is not speckled through it. */
    private fun onRoad(e: Float, n: Float, count: Int): Boolean {
        for (q in 0 until count) {
            val o = q * PIECE
            if (!tunnel(o) && toPiece(e, n, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3]) <= pieces[o + 4] + PAVE_HALF * 2) return true
        }
        return false
    }

    private fun selectDetails(p: GeoPlane, pe: Float, pn: Float) {
        aCount = 0; coverCount = 0; bayCount = 0; treeCount = 0; lampCount = 0; signalCount = 0; crossCount = 0; postCount = 0
        barCount = 0; railCount = 0; closureCount = 0
        if (details.isEmpty() && live.isEmpty()) return
        val near = ArrayList<Pair<Float, HoloDetail>>()
        for (d in if (live.isEmpty()) details else details + live) {
            val line = d.kind in HoloDetail.LINES || d.kind == CLOSURE
            var best = Float.MAX_VALUE
            var w = Float.MAX_VALUE; var s = Float.MAX_VALUE; var e = -Float.MAX_VALUE; var n = -Float.MAX_VALUE
            var le = 0f
            var ln = 0f
            for ((k, pt) in d.pts.withIndex()) {
                val de = p.east(pt.lon)
                val dn = p.north(pt.lat)
                // A line by its nearest stretch: a long row's ends are far from a car beside it.
                best = min(best, if (k > 0 && line) toPiece(pe, pn, le, ln, de, dn) else hypot(de - pe, dn - pn))
                le = de; ln = dn
                w = min(w, de); e = max(e, de); s = min(s, dn); n = max(n, dn)
            }
            // A park or a lake the car is inside has no corner near it.
            if (d.kind in HoloDetail.AREAS && pe in w..e && pn in s..n) best = 0f
            if (best <= RADIUS) near += best to d
        }
        near.sortBy { it.first }
        for ((dist, d) in near) {
            val at = d.pts[0]
            val e = p.east(at.lon)
            val n = p.north(at.lat)
            when (d.kind) {
                "tree" -> tree(e, n)
                "lamp" -> if (lampCount < MAX_LAMPS && dist < NEAR_RADIUS) { lamps[lampCount * 2] = e; lamps[lampCount * 2 + 1] = n; lampCount++ }
                "signal" -> if (signalCount < MAX_SIGNALS && dist < NEAR_RADIUS) {
                    roadside(e, n, signals, signalCount * 4)
                    signalCount++
                    bar(e, n)
                }
                "crossing" -> if (dist < NEAR_RADIUS) crossing(e, n)
                "stop", "give_way", "speed_sign", "sign", "speed_camera", "camera", "alpr", "bollard", "gate" ->
                    if (postCount < MAX_POSTS && dist < NEAR_RADIUS) {
                        posts[postCount * 3] = e; posts[postCount * 3 + 1] = n
                        posts[postCount * 3 + 2] = POSTS.indexOf(d.kind).toFloat()
                        postText[postCount] = if (d.kind == "stop") STOP_WORD else d.text
                        postCount++
                        if (d.kind == "stop") bar(e, n)
                    }
                "barrier" -> for (k in 1 until d.pts.size) {
                    if (railCount == MAX_RAILS) break
                    val e0 = p.east(d.pts[k - 1].lon); val n0 = p.north(d.pts[k - 1].lat)
                    val e1 = p.east(d.pts[k].lon); val n1 = p.north(d.pts[k].lat)
                    if (toPiece(pe, pn, e0, n0, e1, n1) > NEAR_RADIUS) continue
                    val o = railCount++ * 4
                    rails[o] = e0; rails[o + 1] = n0; rails[o + 2] = e1; rails[o + 3] = n1
                }
                "tree_row" -> row(p, d.pts, pe, pn)
                "roadworks", CLOSURE -> {
                    for (k in 1 until d.pts.size) {
                        if (barrierCount == MAX_BARRIERS) break
                        val e0 = p.east(d.pts[k - 1].lon); val n0 = p.north(d.pts[k - 1].lat)
                        val e1 = p.east(d.pts[k].lon); val n1 = p.north(d.pts[k].lat)
                        if (toPiece(pe, pn, e0, n0, e1, n1) > NEAR_RADIUS) continue
                        val o = barrierCount++ * 4
                        barriers[o] = e0; barriers[o + 1] = n0; barriers[o + 2] = e1; barriers[o + 3] = n1
                    }
                    if (d.kind == CLOSURE) {
                        closure(p, d.pts[0], d.pts[1])
                        closure(p, d.pts[d.pts.size - 1], d.pts[d.pts.size - 2])
                    }
                }
                "bay" -> if (bayCount < MAX_BAYS && dist < NEAR_RADIUS) {
                    val kept = ring(p, d.pts, MAX_BAY_RING, bays, bayCount)
                    if (kept >= 3) bayLen[bayCount++] = kept
                }
                else -> {
                    // A kind a newer house sends and this app does not draw is no tint: 0, not a site (T-1188).
                    val tint = when (d.kind) {
                        "water" -> Color.WORLD_WATER
                        "green" -> Color.WORLD_GREEN
                        "wood" -> Color.WORLD_WOOD
                        "parking" -> Color.WORLD_PARKING
                        "construction" -> Color.WORLD_CONSTRUCTION
                        "field" -> Color.WORLD_FIELD
                        "yard" -> Color.WORLD_YARD
                        "paved" -> Color.WORLD_PAVED
                        else -> 0
                    }
                    val cover = d.kind == "field" || d.kind == "yard" || d.kind == "paved"
                    if (tint != 0 && aCount < MAX_AREAS && (!cover || coverCount < MAX_COVER)) {
                        val kept = ring(p, d.pts, MAX_AREA_RING, aRing, aCount)
                        if (kept >= 3) {
                            aLen[aCount] = kept
                            aSite[aCount] = d.kind == "construction"
                            aCover[aCount] = cover
                            if (cover) coverCount++
                            aColor[aCount] = tint
                            aCount++
                        }
                    }
                }
            }
        }
    }

    private fun tree(e: Float, n: Float) {
        if (treeCount == MAX_TREES) return
        trees[treeCount * 3] = e
        trees[treeCount * 3 + 1] = n
        // No two trees are one size; which is which is the place's own, the same every drive.
        trees[treeCount * 3 + 2] = 1.9f + ((e * 12.9898f + n * 78.233f).toInt() and 7) * 0.13f
        treeCount++
    }

    /** A row's trees within [RADIUS] of the car: a long row is not walked, or kept, end to end. */
    private fun row(p: GeoPlane, pts: List<LatLon>, pe: Float, pn: Float) {
        for (k in 1 until pts.size) {
            val e0 = p.east(pts[k - 1].lon); val n0 = p.north(pts[k - 1].lat)
            val e1 = p.east(pts[k].lon); val n1 = p.north(pts[k].lat)
            val len = hypot(e1 - e0, n1 - n0)
            if (len <= 0f) {
                if (hypot(e0 - pe, n0 - pn) <= RADIUS) tree(e0, n0)
                continue
            }
            // Where along it the car is; trees stand every ROW_EVERY_M from the row's start.
            val along = ((pe - e0) * (e1 - e0) + (pn - n0) * (n1 - n0)) / len
            var s = ceil(max(0f, along - RADIUS) / ROW_EVERY_M) * ROW_EVERY_M
            val end = min(len, along + RADIUS)
            while (s <= end && treeCount < MAX_TREES) {
                val t = s / len
                val e = e0 + (e1 - e0) * t
                val n = n0 + (n1 - n0) * t
                if (hypot(e - pe, n - pn) <= RADIUS) tree(e, n)
                s += ROW_EVERY_M
            }
        }
    }

    /** A crossing lies across the street it is on: the nearest street piece gives it its way and width. */
    private fun crossing(e: Float, n: Float) {
        if (crossCount == MAX_CROSSINGS) return
        var best = CROSSING_SNAP_M
        var at = -1
        for (i in 0 until pieceCount) {
            val o = i * PIECE
            if (pieces[o + 5].toInt() != STREET) continue
            val d = toPiece(e, n, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3])
            if (d < best) { best = d; at = o }
        }
        if (at < 0) return
        val de = pieces[at + 2] - pieces[at]
        val dn = pieces[at + 3] - pieces[at + 1]
        val len = hypot(de, dn)
        if (len < MIN_SEGMENT_M) return
        val o = crossCount++ * 5
        crossings[o] = e; crossings[o + 1] = n
        crossings[o + 2] = de / len; crossings[o + 3] = dn / len
        crossings[o + 4] = pieces[at + 4]
    }

    /**
     * Where a signal mapped at ([e], [n]) stands, into [into]`[at]` and `[at + 1]`, and the map's
     * own point after it (T-1237). The map puts a signal on the road's line or its junction node,
     * where a pole would stand in the lane; real ones stand on the verge. One within the nearest
     * street's half width goes to the middle of its pavement: the nearer side when it is off the
     * line, else the right of traffic approaching the piece's nearer end (right-hand traffic). Does
     * not cover which end is the junction, a one-way street's own direction, or a mast over the lanes.
     */
    private fun roadside(e: Float, n: Float, into: FloatArray, at: Int) {
        into[at] = e; into[at + 1] = n; into[at + 2] = e; into[at + 3] = n
        var best = Float.MAX_VALUE
        var o = -1
        for (i in 0 until pieceCount) {
            val q = i * PIECE
            if (pieces[q + 5].toInt() != STREET || tunnel(q)) continue
            val d = toPiece(e, n, pieces[q], pieces[q + 1], pieces[q + 2], pieces[q + 3])
            if (d < best) { best = d; o = q }
        }
        if (o < 0 || best >= pieces[o + 4]) return
        val de = pieces[o + 2] - pieces[o]
        val dn = pieces[o + 3] - pieces[o + 1]
        val len = hypot(de, dn)
        if (len < MIN_SEGMENT_M) return
        val ue = de / len
        val un = dn / len
        val along = ((e - pieces[o]) * ue + (n - pieces[o + 1]) * un).coerceIn(0f, len)
        val off = (e - pieces[o]) * un - (n - pieces[o + 1]) * ue
        val side = if (kotlin.math.abs(off) > BAR_BESIDE_M) kotlin.math.sign(off) else if (along * 2 < len) -1f else 1f
        val out = side * (pieces[o + 4] + PAVE_HALF)
        into[at] = pieces[o] + ue * along + un * out
        into[at + 1] = pieces[o + 1] + un * along - ue * out
    }

    /**
     * The stop line of a stop sign or a signal at ([e], [n]) (T-1104), on the nearest street.
     * A sign standing beside the road stands by the lane it stops, so the line is that half of
     * the road; one mapped on the road's own line does not say which way it faces, and a
     * one-way street has one way: the whole width. A signal or a stop mapped on the junction
     * itself (three or more street ends meeting there) gets none: which arm it governs, and
     * where that arm's line is, the map does not say, and a guess could bar the main road.
     */
    private fun bar(e: Float, n: Float) {
        if (barCount == MAX_BARS) return
        var best = CROSSING_SNAP_M
        var at = -1
        var ends = 0
        for (i in 0 until pieceCount) {
            val o = i * PIECE
            if (pieces[o + 5].toInt() != STREET) continue
            if (hypot(pieces[o] - e, pieces[o + 1] - n) < JUNCTION_M) ends++
            if (hypot(pieces[o + 2] - e, pieces[o + 3] - n) < JUNCTION_M) ends++
            val d = toPiece(e, n, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3])
            if (d < best) { best = d; at = o }
        }
        if (at < 0 || ends >= 3) return
        val de = pieces[at + 2] - pieces[at]
        val dn = pieces[at + 3] - pieces[at + 1]
        val len = hypot(de, dn)
        if (len < MIN_SEGMENT_M) return
        val ue = de / len
        val un = dn / len
        val along = ((e - pieces[at]) * ue + (n - pieces[at + 1]) * un).coerceIn(0f, len)
        val right = (e - pieces[at]) * un - (n - pieces[at + 1]) * ue
        val half = pieces[at + 4] - EDGE_IN_M
        val oneWay = (pieces[at + 7].toInt() and ONE_WAY) != 0
        val o = barCount++ * 6
        bars[o] = pieces[at] + ue * along; bars[o + 1] = pieces[at + 1] + un * along
        bars[o + 2] = ue; bars[o + 3] = un
        if (!oneWay && kotlin.math.abs(right) > BAR_BESIDE_M) {
            bars[o + 4] = if (right > 0f) half / 2 else -half / 2
            bars[o + 5] = half / 2
        } else {
            bars[o + 4] = 0f
            bars[o + 5] = half
        }
    }

    /** [pts] into [into]`[slot]` as plane metres, thinned to at most [cap] corners; how many were kept. */
    private fun ring(p: GeoPlane, pts: List<LatLon>, cap: Int, into: Array<FloatArray>, slot: Int): Int {
        var n = pts.size
        if (n > 1 && pts[0] == pts[n - 1]) n--
        if (n < 3) return 0
        if (into[slot].size < n * 2) into[slot] = FloatArray(n * 2)
        for (k in 0 until n) {
            val pt = pts[k]
            into[slot][k * 2] = p.east(pt.lon)
            into[slot][k * 2 + 1] = p.north(pt.lat)
        }
        return thin(into[slot], n, cap)
    }

    /** One frame of the world round ([pe], [pn]) seen along the camera's heading, into [frame]'s solids. */
    fun build(frame: HoloFrame, cam: HoloCamera, pe: Float, pn: Float, sinH: Float, cosH: Float) {
        this.frame = frame; this.cam = cam
        this.pe = pe; this.pn = pn; this.sinH = sinH; this.cosH = cosH
        dashes = 0
        windows = 0
        rows = 0
        litWindows = 0
        wallLines = 0
        shopSigns = 0
        landCount = 0
        // The car's own road is where heights are counted from: the deck, when it is on a bridge.
        carUp = deckAt(pe, pn, sinH, cosH).let { if (it.isNaN()) ground(pe, pn) else it }
        pinned = Float.NaN
        deck = -1
        inTunnel = tunnelCount > 0 && carInTunnel()
        // Bare ground first, one sheet under everything, where the map has a place to show (T-1188).
        if (paved || aCount > 0 || bCount > 0) ground()
        if (!flat) land()
        // The ground, in the order it lies: land, then roads over it, then paint over them.
        // Land cover first, so a park or a lake given before a field is still over it.
        for (i in 0 until aCount) if (aCover[i]) area(i)
        for (i in 0 until aCount) if (!aCover[i]) area(i)
        // Pavements under every road surface, so a side street's mouth and a driveway cut through them.
        for (i in 0 until pieceCount) pavement(i * PIECE)
        for (cls in PATH downTo MINOR) for (i in 0 until pieceCount) if (pieces[i * PIECE + 5].toInt() == cls) surface(i * PIECE)
        // Streets a width at a time, the narrowest first, each width's surface and then its
        // paint: where two meet, the wider one's surface lies over the other's lines, so only
        // the main road's lines run through a junction, as painted ones do.
        var done = 0f
        while (true) {
            var next = Float.MAX_VALUE
            for (i in 0 until pieceCount) {
                val o = i * PIECE
                if (pieces[o + 5].toInt() == STREET && pieces[o + 4] > done) next = min(next, pieces[o + 4])
            }
            if (next == Float.MAX_VALUE) break
            for (i in 0 until pieceCount) if (street(i * PIECE, next)) surface(i * PIECE)
            for (i in 0 until pieceCount) if (street(i * PIECE, next)) markings(i * PIECE)
            done = next
        }
        // T-1184: in a tunnel the surface's furniture, trees and the buildings over it are not seen.
        val outside = !inTunnel
        if (outside) {
            for (i in 0 until fleckCount) fleck(i)
            for (i in 0 until crossCount) zebra(i * 5)
            for (i in 0 until barCount) stopLine(i * 6)
            for (i in 0 until bayCount) bay(i)
            if (night > POOL_MIN) for (i in 0 until lampCount) pool(lamps[i * 2], lamps[i * 2 + 1])
            // Only with a clock: a map with none is the picture it always was (every golden).
            if (clocked && night > POOL_MIN) headlights()
            if (reach > 0f) for (i in 0 until treeCount) treeShadow(i * 3)
        }
        // What stands on it; the frame orders these by distance.
        // A building stands level, on the ground under its middle.
        for (i in 0 until bCount) {
            if (!outside && bOver[i]) continue
            if (!flat) pinned = up(bMidE[i], bMidN[i]) - carUp
            building(i)
            pinned = Float.NaN
        }
        if (outside) {
            for (i in 0 until treeCount) tree(i * 3)
            for (i in 0 until lampCount) { onItsDeck(lamps[i * 2], lamps[i * 2 + 1]); lamp(lamps[i * 2], lamps[i * 2 + 1]) }
            // The deck is looked up where the map put it: the verge a pole moves to is past DECK_EDGE_M.
            for (i in 0 until signalCount) { onItsDeck(signals[i * 4 + 2], signals[i * 4 + 3]); signal(signals[i * 4], signals[i * 4 + 1]) }
            pinned = Float.NaN
            for (i in 0 until barrierCount) barrier(i * 4)
            for (i in 0 until railCount) rail(i * 4)
            for (i in 0 until closureCount) barricade(i * 4)
            for (i in 0 until postCount) { onItsDeck(posts[i * 3], posts[i * 3 + 1]); post(i * 3) }
            pinned = Float.NaN
            for (i in 0 until bladeCount) blade(i)
            for (i in 0 until shieldCount) shield(i)
            for (i in 0 until guideCount) guide(i)
        }
        for (i in 0 until deckCount) span(decks[i] * PIECE)
        if (outside) for (i in 0 until portalCount) portal(i * 5) else tubes()
    }

    private fun fleck(i: Int) {
        val e = flecks[i * 2]
        val n = flecks[i * 2 + 1]
        world(0, e - FLECK_HALF, n - FLECK_HALF, 0f); world(1, e + FLECK_HALF, n - FLECK_HALF, 0f)
        world(2, e + FLECK_HALF, n + FLECK_HALF, 0f); world(3, e - FLECK_HALF, n + FLECK_HALF, 0f)
        solid(4, fleckColor[i], FLAT)
    }

    private fun street(o: Int, half: Float) = pieces[o + 5].toInt() == STREET && pieces[o + 4] == half

    private fun bridge(o: Int) = (pieces[o + 7].toInt() and BRIDGE) != 0

    private fun tunnel(o: Int) = (pieces[o + 7].toInt() and TUNNEL) != 0

    /** A tunnel piece not drawn this frame: the car is not in a tunnel, and a tunnel is under what it passes beneath (T-1184). */
    private fun veiled(o: Int) = !inTunnel && tunnel(o)

    /**
     * Picks the tunnels' mouths (an end no other tunnel piece carries on from) and which buildings
     * stand over one. Sorts nothing; allocates nothing. Does not judge a tunnel's depth: any
     * building a tunnel's line runs under counts, whatever the layers say.
     */
    private fun selectTunnels(pe: Float, pn: Float) {
        portalCount = 0
        tunnelCount = 0
        bOver.fill(false)
        for (i in 0 until pieceCount) {
            if (tunnelCount == MAX_TUNNEL_PIECES) break
            if (tunnel(i * PIECE)) tunnelAt[tunnelCount++] = i * PIECE
        }
        for (q in 0 until tunnelCount) {
            val o = tunnelAt[q]
            for (end in 0..1) {
                val e = pieces[o + end * 2]; val n = pieces[o + end * 2 + 1]
                var carried = false
                for (r in 0 until tunnelCount) {
                    val p = tunnelAt[r]
                    if (p == o) continue
                    if (hypot(pieces[p] - e, pieces[p + 1] - n) <= MARK_JOIN_M ||
                        hypot(pieces[p + 2] - e, pieces[p + 3] - n) <= MARK_JOIN_M) { carried = true; break }
                }
                if (carried || portalCount == MAX_PORTALS) continue
                val oe = e - pieces[o + (1 - end) * 2]; val on = n - pieces[o + (1 - end) * 2 + 1]
                val l = hypot(oe, on)
                if (l < MIN_SEGMENT_M) continue
                val at = portalCount++ * 5
                portals[at] = e; portals[at + 1] = n; portals[at + 2] = oe / l; portals[at + 3] = on / l
                portals[at + 4] = pieces[o + 4]
            }
        }
        for (b in 0 until bCount) {
            val r = bRing[b]
            val k = bLen[b]
            for (q in 0 until tunnelCount) {
                val o = tunnelAt[q]
                if (bOver[b]) break
                if (toPiece(pe, pn, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3]) > TUBE_RADIUS_M) continue
                bOver[b] = standsOver(r, k, o)
            }
        }
    }

    /** Whether the footprint [r] of [k] corners lies over the tunnel piece at [o], judged by a sample every [OVER_STEP_M] (a sliver between two is missed). */
    private fun standsOver(r: FloatArray, k: Int, o: Int): Boolean {
        val ae = pieces[o]; val an = pieces[o + 1]; val be = pieces[o + 2]; val bn = pieces[o + 3]
        for (c in 0 until k) if (toPiece(r[c * 2], r[c * 2 + 1], ae, an, be, bn) <= pieces[o + 4]) return true
        val steps = max(1, (hypot(be - ae, bn - an) / OVER_STEP_M).toInt())
        for (s in 0..steps) {
            val t = s.toFloat() / steps
            if (inside(r, k, ae + (be - ae) * t, an + (bn - an) * t)) return true
        }
        return false
    }

    /**
     * Whether the car is on a tunnel piece, going the way it runs. A surface road over a tunnel
     * that the car is on, going the same way, wins: it is not beneath anything.
     */
    private fun carInTunnel(): Boolean {
        var found = false
        for (i in 0 until pieceCount) {
            val o = i * PIECE
            if (bridge(o)) continue
            val dx = pieces[o + 2] - pieces[o]
            val dy = pieces[o + 3] - pieces[o + 1]
            val len = hypot(dx, dy)
            if (len < MIN_SEGMENT_M || toPiece(pe, pn, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3]) > pieces[o + 4]) continue
            if (abs(sinH * dx + cosH * dy) < ON_DECK_COS * len) continue
            if (!tunnel(o)) return false
            found = true
        }
        return found
    }

    /** The mouth at portal [at]: a face of the hillside round a dark opening, seen only from outside and ahead of the car. */
    private fun portal(at: Int) {
        val e = portals[at]; val n = portals[at + 1]
        val oe = portals[at + 2]; val on = portals[at + 3]
        val half = portals[at + 4]
        if (carZ(e, n) < OVERHEAD_AHEAD_M) return
        val wide = half + PORTAL_WALL_M
        val top = TUNNEL_H + PORTAL_LINTEL_M
        world(0, e + on * wide, n - oe * wide, 0f); world(1, e - on * wide, n + oe * wide, 0f)
        world(2, e - on * wide, n + oe * wide, top); world(3, e + on * wide, n - oe * wide, top)
        val face = solid(4, shade(Color.WORLD_BARRIER, PORTAL_DARK), RAISED)
        if (face < 0f) return
        world(0, e + on * half, n - oe * half, 0f); world(1, e - on * half, n + oe * half, 0f)
        world(2, e - on * half, n + oe * half, TUNNEL_H); world(3, e + on * half, n - oe * half, TUNNEL_H)
        solid(4, shade(Color.WORLD_BARRIER, MOUTH_DARK), face)
    }

    /** From inside a tunnel: its two walls and a short roof just ahead, so the road is not seen to run under the open sky. */
    private fun tubes() {
        var drawn = 0
        for (q in 0 until tunnelCount) {
            val o = tunnelAt[q]
            val ae = pieces[o]; val an = pieces[o + 1]
            val dx = pieces[o + 2] - ae; val dy = pieces[o + 3] - an
            val len = hypot(dx, dy)
            if (len < MIN_SEGMENT_M || toPiece(pe, pn, ae, an, pieces[o + 2], pieces[o + 3]) > TUBE_RADIUS_M) continue
            val ue = dx / len; val un = dy / len
            val half = pieces[o + 4]
            val wall = shade(Color.WORLD_BARRIER, TUBE_WALL_DARK)
            var s = 0f
            while (s < len && drawn < MAX_TUBE_SOLIDS) {
                val t = min(len, s + TUBE_CUT_M)
                for (side in 0..1) {
                    val lat = if (side == 0) half else -half
                    val e0 = ae + ue * s + un * lat; val n0 = an + un * s - ue * lat
                    val e1 = ae + ue * t + un * lat; val n1 = an + un * t - ue * lat
                    world(0, e0, n0, 0f); world(1, e1, n1, 0f); world(2, e1, n1, TUNNEL_H); world(3, e0, n0, TUNNEL_H)
                    solid(4, wall, RAISED)
                    drawn++
                }
                s = t
            }
            // The lid is only the stretch a few metres ahead: the camera is higher than the roof, and a
            // roof over or behind the car would be seen from above and hide the road it covers.
            val z0 = carZ(ae, an)
            val dz = carZ(pieces[o + 2], pieces[o + 3]) - z0
            if (abs(dz) < MIN_SEGMENT_M) continue
            val sa = (OVERHEAD_AHEAD_M - z0) / dz * len
            val sb = (OVERHEAD_AHEAD_M + TUBE_ROOF_M - z0) / dz * len
            val lo = max(0f, min(sa, sb)); val hi = min(len, max(sa, sb))
            if (hi - lo < MIN_SEGMENT_M || drawn >= MAX_TUBE_SOLIDS) continue
            val e0 = ae + ue * lo; val n0 = an + un * lo; val e1 = ae + ue * hi; val n1 = an + un * hi
            world(0, e0 + un * half, n0 - ue * half, TUNNEL_H); world(1, e1 + un * half, n1 - ue * half, TUNNEL_H)
            world(2, e1 - un * half, n1 + ue * half, TUNNEL_H); world(3, e0 - un * half, n0 + ue * half, TUNNEL_H)
            solid(4, shade(Color.WORLD_BARRIER, TUBE_ROOF_DARK), RAISED)
            drawn++
        }
    }

    /** The deck being drawn, at ([e], [n]) along it, over the car's own road. */
    private fun deckY(e: Float, n: Float): Float {
        val dx = pieces[deck + 2] - pieces[deck]
        val dy = pieces[deck + 3] - pieces[deck + 1]
        val t = (((e - pieces[deck]) * dx + (n - pieces[deck + 1]) * dy) / (dx * dx + dy * dy)).coerceIn(-0.2f, 1.2f)
        return pieces[deck + 8] + (pieces[deck + 9] - pieces[deck + 8]) * t - carUp
    }

    /** Whether a road on the ground runs through ([e], [n]): no pier stands in one. */
    private fun roadUnder(e: Float, n: Float): Boolean {
        val c = cellY(n) * gridNx + cellX(e)
        for (k in gridStart[c] until gridStart[c + 1]) {
            val o = gridItems[k] * PIECE
            scans++
            if (!bridge(o) && !tunnel(o) && toPiece(e, n, pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3]) <= pieces[o + 4] + PIER_HALF_M) return true
        }
        return false
    }

    /** A solid of the deck being drawn: all of one piece is ordered as one thing, in the order given. */
    private fun onDeck(color: Int) {
        val at = solid(4, color, deckDepth)
        if (deckDepth == RAISED && at >= 0f) deckDepth = at
    }

    private fun approach(o: Int) = (pieces[o + 7].toInt() and APPROACH) != 0

    /** Whether water lies at ([e], [n]): a pier has no ground to stand on there. */
    private fun onWater(e: Float, n: Float): Boolean {
        for (i in 0 until aCount) if (aColor[i] == Color.WORLD_WATER && inside(aRing[i], aLen[i], e, n)) return true
        return false
    }

    /**
     * Where the piers of the bridge piece [o] stand (T-1181): every [PIER_EVERY_M] along its way,
     * the first [PIER_FIRST_M] in, and not at one of its ends; none where the deck is under
     * [PIER_MIN_M], or on a road or water beneath. Calls [at] with east, north, the way along it and the
     * deck's height and the ground's there (absolute). A lifted road has none: it is a bank.
     */
    private inline fun eachPier(o: Int, at: (Float, Float, Float, Float, Float) -> Unit) {
        if (approach(o)) return
        val a0 = pieces[o + 10]; val a1 = pieces[o + 11]
        if (a1 <= a0) return
        var along = PIER_FIRST_M + ceil(max(0f, a0 - PIER_FIRST_M) / PIER_EVERY_M) * PIER_EVERY_M
        while (along < a1) {
            val t = (along - a0) / (a1 - a0)
            val e = pieces[o] + (pieces[o + 2] - pieces[o]) * t
            val n = pieces[o + 1] + (pieces[o + 3] - pieces[o + 1]) * t
            val y = pieces[o + 8] + (pieces[o + 9] - pieces[o + 8]) * t
            val under = ground(e, n)
            if (y - under > PIER_MIN_M && !roadUnder(e, n) && !onWater(e, n)) at(e, n, along, y, under)
            along += PIER_EVERY_M
        }
    }

    /** The piers of the bridges near, as east, north and the distance along their way. */
    internal fun piersOf(): List<FloatArray> {
        val out = ArrayList<FloatArray>()
        for (i in 0 until deckCount) eachPier(decks[i] * PIECE) { e, n, along, _, _ -> out += floatArrayOf(e, n, along) }
        return out
    }

    /**
     * One piece of a bridge (T-1167): the deck's side faces, the piers under it, the road and its
     * paint on top, and a rail either side. A piece of a road climbing to a deck (T-1181) is drawn
     * the same way but its sides are filled down to the ground, a bank, with no pier and no rail.
     * Does not carry what stands on it (a lamp on a bridge stands on the ground below) or hide
     * what is under it from what is drawn later.
     */
    private fun span(o: Int) {
        val e0 = pieces[o]; val n0 = pieces[o + 1]; val e1 = pieces[o + 2]; val n1 = pieces[o + 3]
        val len = hypot(e1 - e0, n1 - n0)
        if (len < MIN_SEGMENT_M) return
        val ue = (e1 - e0) / len
        val un = (n1 - n0) / len
        val half = pieces[o + 4]
        val y0 = pieces[o + 8] - carUp
        val y1 = pieces[o + 9] - carUp
        val bank = approach(o)
        if (!bank && !clipped && overCamera(o)) { spanAhead(o); return }
        deck = o; deckDepth = RAISED; pinned = 0f
        for (side in -1..1 step 2) {
            val oe = un * half * side; val on = -ue * half * side
            if (bank) {
                // The bank's side runs from the road's edge down to the ground there.
                val u0 = ground(e0 + oe, n0 + on) - carUp
                val u1 = ground(e1 + oe, n1 + on) - carUp
                if (y0 - u0 < APPROACH_MIN_M && y1 - u1 < APPROACH_MIN_M) continue
                world(0, e0 + oe, n0 + on, u0); world(1, e1 + oe, n1 + on, u1)
                world(2, e1 + oe, n1 + on, y1); world(3, e0 + oe, n0 + on, y0)
                onDeck(shade(Color.WORLD_LAND, BANK_DARK))
                continue
            }
            if (sharedSide(o, (e0 + e1) / 2 + oe, (n0 + n1) / 2 + on)) continue
            world(0, e0 + oe, n0 + on, y0 - DECK_THICK_M); world(1, e1 + oe, n1 + on, y1 - DECK_THICK_M)
            world(2, e1 + oe, n1 + on, y1); world(3, e0 + oe, n0 + on, y0)
            onDeck(shade(Color.WORLD_BARRIER, FASCIA_DARK))
        }
        eachPier(o) { e, n, _, y, ground ->
            // A column, across the road and along it so it is seen from the road and from beside it.
            val top = y - carUp
            val under = ground - carUp
            world(0, e + un * PIER_HALF_M, n - ue * PIER_HALF_M, under); world(1, e - un * PIER_HALF_M, n + ue * PIER_HALF_M, under)
            world(2, e - un * PIER_HALF_M, n + ue * PIER_HALF_M, top); world(3, e + un * PIER_HALF_M, n - ue * PIER_HALF_M, top)
            onDeck(shade(Color.WORLD_BARRIER, PIER_DARK))
            world(0, e - ue * PIER_HALF_M, n - un * PIER_HALF_M, under); world(1, e + ue * PIER_HALF_M, n + un * PIER_HALF_M, under)
            world(2, e + ue * PIER_HALF_M, n + un * PIER_HALF_M, top); world(3, e - ue * PIER_HALF_M, n - un * PIER_HALF_M, top)
            onDeck(shade(Color.WORLD_BARRIER, PIER_DARK))
        }
        surface(o)
        if (pieces[o + 5].toInt() == STREET) markings(o)
        if (!bank) for (side in -1..1 step 2) {
            val oe = un * half * side; val on = -ue * half * side
            if (sharedSide(o, (e0 + e1) / 2 + oe, (n0 + n1) / 2 + on)) continue
            world(0, e0 + oe, n0 + on, y0); world(1, e1 + oe, n1 + on, y1)
            world(2, e1 + oe, n1 + on, y1 + RAIL_TOP_M); world(3, e0 + oe, n0 + on, y0 + RAIL_TOP_M)
            onDeck(Color.WORLD_BARRIER)
        }
        deck = -1; pinned = Float.NaN
    }

    private var clipped = false
    private val clipKeep = FloatArray(PIECE)

    /**
     * T-1191: an overhead deck the car is driving under is drawn from [OVERHEAD_AHEAD_M] ahead of the car,
     * not dropped whole piece by piece: [span] runs on a copy of the piece cut at that line.
     */
    private fun spanAhead(o: Int) {
        val z0 = carZ(pieces[o], pieces[o + 1]); val z1 = carZ(pieces[o + 2], pieces[o + 3])
        if (max(z0, z1) <= OVERHEAD_AHEAD_M) return
        val t = (OVERHEAD_AHEAD_M - z0) / (z1 - z0)
        val from = if (z0 < z1) t else 0f
        val to = if (z0 < z1) 1f else t
        System.arraycopy(pieces, o, clipKeep, 0, PIECE)
        // The start and end of each: east, north, deck height, way along.
        fun cut(s: Int, e: Int) {
            val a = clipKeep[s]; val b = clipKeep[e]
            pieces[o + s] = a + (b - a) * from
            pieces[o + e] = a + (b - a) * to
        }
        cut(0, 2); cut(1, 3); cut(8, 9); cut(10, 11)
        clipped = true
        span(o)
        clipped = false
        System.arraycopy(clipKeep, 0, pieces, o, PIECE)
    }

    /**
     * Whether the bridge piece [o] is a deck overhead that reaches back to the car or behind it (T-1181):
     * with no depth to this painter it would be drawn over the road and the car, where the real
     * camera would have passed under it (a footbridge just behind the car filled the screen).
     * Not the deck the car is on, which is at its own height.
     */
    private fun overCamera(o: Int): Boolean {
        if ((pieces[o + 8] + pieces[o + 9]) / 2 - carUp < OVERHEAD_MIN_M) return false
        // Any of it level with the car or behind: the camera is behind the car and would be under it.
        return min(carZ(pieces[o], pieces[o + 1]), carZ(pieces[o + 2], pieces[o + 3])) < OVERHEAD_AHEAD_M
    }

    /**
     * Whether ([e], [n]), the middle of one edge of the bridge piece [o], lies on another bridge
     * piece's surface at its height (T-1181): two carriageways, or a junction, on one deck. That
     * edge is inside the deck and gets no side face and no rail; each drawn there crossed the
     * junction as a slab. Judged at the edge's middle only, so a long piece half beside another
     * keeps or loses the whole side.
     */
    private fun sharedSide(o: Int, e: Float, n: Float): Boolean {
        val y = pieces[o + 8] + pieces[o + 9]
        val c = cellY(n) * gridNx + cellX(e)
        for (k in gridStart[c] until gridStart[c + 1]) {
            val q = gridItems[k] * PIECE
            scans++
            if (q == o || !bridge(q) || approach(q) || abs(pieces[q + 8] + pieces[q + 9] - y) > 2 * SHARED_DECK_M) continue
            if (toPiece(e, n, pieces[q], pieces[q + 1], pieces[q + 2], pieces[q + 3]) < pieces[q + 4] - SHARED_IN_M) return true
        }
        return false
    }

    private fun area(i: Int) {
        val r = aRing[i]
        val n = aLen[i]
        grow(n)
        for (k in 0 until n) world(k, r[k * 2], r[k * 2 + 1], 0f)
        solid(n, aColor[i], FLAT)
        if (!aSite[i]) return
        // A site's corners are coned off.
        for (k in 0 until min(n, MAX_CONES)) {
            val x = carX(r[k * 2], r[k * 2 + 1])
            val z = carZ(r[k * 2], r[k * 2 + 1])
            car(0, x - 0.28f, 0f, z); car(1, x + 0.28f, 0f, z); car(2, x, 0.75f, z)
            solid(3, Color.WORLD_CONE, RAISED)
        }
    }

    private fun surface(o: Int) {
        // A bridge's piece is drawn with what stands, by [span], not with the ground.
        if (deck < 0 && bridge(o) || veiled(o)) return
        val cls = pieces[o + 5].toInt()
        val color = when (cls) {
            STREET -> Color.WORLD_ASPHALT
            MINOR -> Color.WORLD_ASPHALT_MINOR
            else -> Color.WORLD_PATH
        }
        // A little past each end, so two pieces meeting at a bend leave no wedge of ground.
        stripe(pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3], 0f, pieces[o + 4], min(pieces[o + 4], 2f), color)
    }

    /** A town street's pavement and kerb stone, either side (T-1104); only near, where they can be told apart. */
    private fun pavement(o: Int) {
        if ((pieces[o + 7].toInt() and KERBED) == 0 || bridge(o)) return
        val e0 = pieces[o]; val n0 = pieces[o + 1]; val e1 = pieces[o + 2]; val n1 = pieces[o + 3]
        if (toPiece(pe, pn, e0, n0, e1, n1) > KERB_RADIUS) return
        val half = pieces[o + 4]
        stripe(e0, n0, e1, n1, half + PAVE_HALF, PAVE_HALF, PAVE_HALF, Color.WORLD_PAVEMENT)
        stripe(e0, n0, e1, n1, -(half + PAVE_HALF), PAVE_HALF, PAVE_HALF, Color.WORLD_PAVEMENT)
        stripe(e0, n0, e1, n1, half + KERB_HALF, KERB_HALF, KERB_HALF, Color.WORLD_KERB)
        stripe(e0, n0, e1, n1, -(half + KERB_HALF), KERB_HALF, KERB_HALF, Color.WORLD_KERB)
    }

    private fun stopLine(o: Int) {
        val e = bars[o]; val n = bars[o + 1]
        val ue = bars[o + 2] * BAR_HALF_M; val un = bars[o + 3] * BAR_HALF_M
        stripe(e - ue, n - un, e + ue, n + un, bars[o + 4], bars[o + 5], 0f, Color.WORLD_MARKING)
    }

    /**
     * A street's paint: both edges, the line between opposing lanes, and dashes between the
     * rest. A one-way street has no opposing lanes, so no centre line: all its lanes are
     * parted by dashes (T-1104).
     */
    private fun markings(o: Int) {
        val e0 = pieces[o]; val n0 = pieces[o + 1]; val e1 = pieces[o + 2]; val n1 = pieces[o + 3]
        val half = pieces[o + 4]
        val lanes = pieces[o + 6].toInt()
        val oneWay = (pieces[o + 7].toInt() and ONE_WAY) != 0
        if (deck < 0 && bridge(o) || veiled(o)) return
        // A back street is plain tarmac, whatever lanes the map counts on it (T-1189).
        if ((pieces[o + 7].toInt() and UNPAINTED) != 0) return
        if (toPiece(pe, pn, e0, n0, e1, n1) > MARK_RADIUS) return
        gatherNear(o)
        // T-1246: a one-way road's left edge, left of its travel, is the centre line's yellow, as on the road. A positive
        // offset is the right of the piece's own direction; a way mapped oneway=-1 is travelled against it, so the
        // left is the positive side. Right-hand traffic only: the scene has no keep-left flag to mirror it by.
        val back = (pieces[o + 7].toInt() and ONE_WAY_BACK) != 0
        paint(o, e0, n0, e1, n1, half - EDGE_IN_M, if (oneWay && back) Color.WORLD_MARKING_CENTRE else Color.WORLD_MARKING)
        paint(o, e0, n0, e1, n1, -(half - EDGE_IN_M), if (oneWay && !back) Color.WORLD_MARKING_CENTRE else Color.WORLD_MARKING)
        arrows(o)
        if (lanes < 2) return
        val lane = 2 * (half - EDGE_IN_M) / lanes
        for (k in 1 until lanes) {
            val off = (k - lanes / 2f) * lane
            if (lanes % 2 == 0 && k == lanes / 2 && !oneWay) {
                paint(o, e0, n0, e1, n1, off, Color.WORLD_MARKING_CENTRE)
            } else {
                dashed(o, e0, n0, e1, n1, off)
            }
        }
    }

    /**
     * T-1235: what each lane may do at the end of its way, as one list a lane: [TURN_LEFT], [TURN_THROUGH] and [TURN_RIGHT]
     * (and the slight and sharp ones as their side), or null when the map's [said] list is absent, says nothing a lane
     * is painted with, or has more lanes than the way has in its direction ([lanes] all of them on a one-way road,
     * half on a two-way one): a list that does not fit the road would put an arrow in the wrong lane.
     */
    private fun laneTurns(said: String?, lanes: Int, oneWay: Boolean): IntArray? {
        if (said == null) return null
        val each = said.split('|')
        if (each.size > if (oneWay) lanes else lanes / 2) return null
        val turns = IntArray(each.size) { i -> each[i].split(';').fold(0) { acc, v -> acc or turnOf(v) } }
        return if (turns.any { it != 0 }) turns else null
    }

    private fun turnOf(word: String) = when (word) {
        "left", "slight_left", "sharp_left" -> TURN_LEFT
        "through" -> TURN_THROUGH
        "right", "slight_right", "sharp_right" -> TURN_RIGHT
        else -> 0
    }

    /**
     * T-1235: an arrow on the centre of each lane that turns, ending [ARROW_BACK_M] before the end of the piece the
     * way runs to, in the marking colour; on a deck too, as paint of the deck. The lanes of the way's direction lie
     * on its right (right-hand traffic) and the list is left to right, so the last lane is the one by the edge. Not
     * drawn on a piece too short to hold one, or on a deck piece cut short for a car beneath it.
     */
    private fun arrows(o: Int) {
        val at = pieces[o + ARROW_SLOT].toInt()
        if (at < 0 || clipped) return
        val e0 = pieces[o]; val n0 = pieces[o + 1]; val e1 = pieces[o + 2]; val n1 = pieces[o + 3]
        val len = hypot(e1 - e0, n1 - n0)
        val lanes = pieces[o + 6].toInt()
        if (len < ARROW_BACK_M + ARROW_LEN_M || lanes < 1) return
        val ends = arrows[at] ?: return
        val half = pieces[o + 4] - EDGE_IN_M
        val lane = 2 * half / lanes
        for (end in 0..1) {
            val turns = ends[end] ?: continue
            // Towards the junction at this end: along the piece at its end, against it at its start.
            val sign = if (end == 1) 1f else -1f
            val ue = (e1 - e0) / len * sign
            val un = (n1 - n0) / len * sign
            val tail = ARROW_BACK_M + ARROW_LEN_M
            for (i in turns.indices) {
                if (turns[i] == 0) continue
                val side = half - (turns.size - i - 0.5f) * lane
                aE = (if (end == 1) e1 else e0) - ue * tail + un * side
                aN = (if (end == 1) n1 else n0) - un * tail - ue * side
                aUe = ue; aUn = un
                arrowTap?.invoke(floatArrayOf(aE, aN, ue, un, turns[i].toFloat()))
                arrow(turns[i], lane)
            }
        }
    }

    // The arrow being drawn: its tail, and the way it points (east, north); its own frame is f along it and s to its right.
    private var aE = 0f
    private var aN = 0f
    private var aUe = 0f
    private var aUn = 0f
    private val arrowPt = FloatArray(8)

    /** Test hook: each arrow laid as [tail east, tail north, east, north of the way it points, its turns]. */
    internal var arrowTap: ((FloatArray) -> Unit)? = null

    private fun arrow(turn: Int, lane: Float) {
        val through = (turn and TURN_THROUGH) != 0
        val fork = if (through) ARROW_LEN_M - ARROW_HEAD_M else ARROW_FORK_M
        band(0f, 0f, fork, 0f, 0f, 1f)
        if (through) head(fork, 0f, 1f, 0f)
        // A turn leaves the shaft at 45 degrees, kept inside the lane's width.
        val lat = min(ARROW_BRANCH_M, lane * ARROW_BRANCH_LANE)
        for (side in intArrayOf(-1, 1)) {
            if ((turn and (if (side < 0) TURN_LEFT else TURN_RIGHT)) == 0) continue
            val s = side.toFloat()
            band(ARROW_FORK_M, 0f, ARROW_FORK_M + lat, s * lat, -s * DIAG, DIAG)
            head(ARROW_FORK_M + lat, s * lat, DIAG, s * DIAG)
        }
    }

    private fun at(k: Int, f: Float, s: Float) {
        arrowPt[k * 2] = aE + aUe * f + aUn * s
        arrowPt[k * 2 + 1] = aN + aUn * f - aUe * s
    }

    /** A band from ([f0], [s0]) to ([f1], [s1]) as wide as an arrow's shaft, across the unit direction ([pf], [ps]). */
    private fun band(f0: Float, s0: Float, f1: Float, s1: Float, pf: Float, ps: Float) {
        val h = ARROW_SHAFT_HALF
        at(0, f0 + pf * h, s0 + ps * h); at(1, f1 + pf * h, s1 + ps * h)
        at(2, f1 - pf * h, s1 - ps * h); at(3, f0 - pf * h, s0 - ps * h)
        flat(4, Color.WORLD_MARKING)
    }

    /** A head on the end of a shaft at ([f], [s]), pointing along the unit direction ([df], [ds]). */
    private fun head(f: Float, s: Float, df: Float, ds: Float) {
        at(0, f + df * ARROW_HEAD_M, s + ds * ARROW_HEAD_M)
        at(1, f - ds * ARROW_HEAD_HALF, s + df * ARROW_HEAD_HALF)
        at(2, f + ds * ARROW_HEAD_HALF, s - df * ARROW_HEAD_HALF)
        flat(3, Color.WORLD_MARKING)
    }

    /** The [arrowPt] polygon of [n] points, flat on the ground or on the deck being drawn. */
    private fun flat(n: Int, color: Int) {
        for (k in 0 until 4) {
            val j = min(k, n - 1)
            val e = arrowPt[j * 2]; val nn = arrowPt[j * 2 + 1]
            world(k, e, nn, if (deck < 0) 0f else deckY(e, nn))
        }
        // A triangle repeats its last corner: [onDeck] takes four.
        if (deck < 0) solid(4, color, FLAT) else onDeck(color)
    }

    private fun dashed(o: Int, e0: Float, n0: Float, e1: Float, n1: Float, off: Float) {
        val len = hypot(e1 - e0, n1 - n0)
        if (len < MIN_SEGMENT_M) return
        val ue = (e1 - e0) / len
        val un = (n1 - n0) / len
        var s = 0f
        while (s < len && dashes < MAX_DASHES) {
            val t = min(len, s + DASH_M)
            paint(o, e0 + ue * s, n0 + un * s, e0 + ue * t, n0 + un * t, off, Color.WORLD_MARKING)
            dashes++
            s += DASH_EVERY_M
        }
    }

    // --- T-1182: paint stops where another road's surface is --------------------------------

    /** Test hook: each painted run as [e0, n0, e1, n1, colour, then its piece's e0, n0, e1, n1, half]. */
    internal var lineTap: ((FloatArray) -> Unit)? = null

    /** Test hook (T-1191): the pieces the per-frame neighbour scans (marking cut, shared sides, piers) looked at, to show they are binned. */
    internal var scans = 0L
    private var markNear = IntArray(MARK_NEAR)
    private var markNearCount = 0
    private var markOwn = 0
    private val cutAt = FloatArray(MARK_CUTS * 2)
    private var cutCount = 0
    private var coverFrom = 0f
    private var coverTo = 0f

    /**
     * The pieces of other roads whose surface may reach piece [o]'s paint: the nearest [MARK_NEAR]
     * (the pieces are in order of distance), on the same level, a deck only with a deck at its height.
     */
    private fun gatherNear(o: Int) {
        markNearCount = 0
        markOwn = o
        val reach = pieces[o + 4]
        val loE = min(pieces[o], pieces[o + 2]) - reach; val hiE = max(pieces[o], pieces[o + 2]) + reach
        val loN = min(pieces[o + 1], pieces[o + 3]) - reach; val hiN = max(pieces[o + 1], pieces[o + 3]) + reach
        val onBridge = bridge(o)
        seenAt++
        for (cy in cellY(loN)..cellY(hiN)) for (cx in cellX(loE)..cellX(hiE)) {
            val c = cy * gridNx + cx
            for (k in gridStart[c] until gridStart[c + 1]) {
                val q = gridItems[k]
                if (seen[q] == seenAt) continue
                seen[q] = seenAt
                val p = q * PIECE
                scans++
                // A tunnel is its own level (T-1184): its surface neither cuts a street's paint nor is cut by it.
                if (p == o || bridge(p) != onBridge || tunnel(p) != tunnel(o)) continue
                // T-1238: a path is drawn under every street, so its surface never needs paint cut for it; kept in the list, the
                // footways beside a subdivided street crowded the street itself out of the nearest MARK_NEAR, and its edge lines ran on across it.
                if (pieces[p + 5].toInt() == PATH) continue
                if (onBridge && abs(pieces[p + 8] + pieces[p + 9] - pieces[o + 8] - pieces[o + 9]) > DECK_M) continue
                val r = pieces[p + 4] + 2f
                if (max(pieces[p], pieces[p + 2]) + r < loE || min(pieces[p], pieces[p + 2]) - r > hiE) continue
                if (max(pieces[p + 1], pieces[p + 3]) + r < loN || min(pieces[p + 1], pieces[p + 3]) - r > hiN) continue
                // The cells come in no order: keep the MARK_NEAR nearest by index, as the scan in order did.
                if (markNearCount == MARK_NEAR) {
                    if (p >= markNear[MARK_NEAR - 1]) continue
                    markNearCount--
                }
                var j = markNearCount++
                while (j > 0 && markNear[j - 1] > p) { markNear[j] = markNear[j - 1]; j-- }
                markNear[j] = p
            }
        }
    }

    // T-1191: the pieces binned by the cells their surface (and 2 m) reaches, so a neighbour query reads a few cells, not every piece.
    // ponytail: a bounding box per piece, so a long diagonal piece sits in cells it misses; cells only widen past GRID_CELL_M to stay under GRID_MAX a side.
    private var gridStart = IntArray(2)
    private var gridItems = IntArray(0)
    private var seen = IntArray(0)
    private var seenAt = 0
    private var gridE0 = 0f
    private var gridN0 = 0f
    private var gridCell = GRID_CELL_M
    private var gridNx = 1
    private var gridNy = 1

    private fun cellX(e: Float) = ((e - gridE0) / gridCell).toInt().coerceIn(0, gridNx - 1)
    private fun cellY(n: Float) = ((n - gridN0) / gridCell).toInt().coerceIn(0, gridNy - 1)

    private fun binPieces() {
        var loE = Float.MAX_VALUE; var hiE = -Float.MAX_VALUE; var loN = Float.MAX_VALUE; var hiN = -Float.MAX_VALUE
        for (q in 0 until pieceCount) {
            val p = q * PIECE
            loE = min(loE, min(pieces[p], pieces[p + 2])); hiE = max(hiE, max(pieces[p], pieces[p + 2]))
            loN = min(loN, min(pieces[p + 1], pieces[p + 3])); hiN = max(hiN, max(pieces[p + 1], pieces[p + 3]))
        }
        if (pieceCount == 0) { loE = 0f; hiE = 0f; loN = 0f; hiN = 0f }
        gridE0 = loE; gridN0 = loN
        gridCell = max(GRID_CELL_M, max(hiE - loE, hiN - loN) / GRID_MAX)
        gridNx = ((hiE - loE) / gridCell).toInt() + 1
        gridNy = ((hiN - loN) / gridCell).toInt() + 1
        if (gridStart.size < gridNx * gridNy + 1) gridStart = IntArray(gridNx * gridNy + 1)
        if (seen.size < pieceCount) seen = IntArray(pieceCount)
        seen.fill(0, 0, pieceCount); seenAt = 0
        val cells = gridNx * gridNy
        gridStart.fill(0, 0, cells + 1)
        // Two passes: count each cell's pieces, then place them (the starts, moved on as each is placed, then stand one cell late).
        for (pass in 0..1) {
            if (pass == 1) {
                for (c in 0 until cells) gridStart[c + 1] += gridStart[c]
                if (gridItems.size < gridStart[cells]) gridItems = IntArray(gridStart[cells])
            }
            for (q in 0 until pieceCount) {
                val p = q * PIECE
                val r = pieces[p + 4] + 2f
                for (cy in cellY(min(pieces[p + 1], pieces[p + 3]) - r)..cellY(max(pieces[p + 1], pieces[p + 3]) + r))
                    for (cx in cellX(min(pieces[p], pieces[p + 2]) - r)..cellX(max(pieces[p], pieces[p + 2]) + r)) {
                        val c = cy * gridNx + cx
                        if (pass == 0) gridStart[c + 1]++ else gridItems[gridStart[c]++] = q
                    }
            }
        }
        // Placing moved cell c's start to where c + 1's began: shift back one.
        for (c in cells downTo 1) gridStart[c] = gridStart[c - 1]
        gridStart[0] = 0
    }

    /**
     * Whether piece [q] is the road of the piece being painted carrying on: they meet end to start
     * (end to end, when one is drawn the other way about, [par] < 0). Two pieces that start at one
     * vertex and run the same way are a fork, however alike their lines are near it.
     */
    private fun carriesOn(q: Int, par: Float): Boolean {
        val o = markOwn
        val a = if (par < 0f) 0 else 2
        val b = if (par < 0f) 2 else 0
        return joined(o, q + a) || joined(o + 2, q + b)
    }

    private fun joined(i: Int, j: Int) = hypot(pieces[i] - pieces[j], pieces[i + 1] - pieces[j + 1]) <= MARK_JOIN_M

    /** The cosine between piece [p] leaving its end [s] (0 start, 2 end) and piece [x] leaving its end [t]: 1 when they leave that point the same way. */
    private fun awayDot(p: Int, s: Int, x: Int, t: Int): Float {
        val pe = pieces[p + 2 - s] - pieces[p + s]; val pn = pieces[p + 3 - s] - pieces[p + s + 1]
        val xe = pieces[x + 2 - t] - pieces[x + t]; val xn = pieces[x + 3 - t] - pieces[x + t + 1]
        return (pe * xe + pn * xn) / max(1e-6f, hypot(pe, pn) * hypot(xe, xn))
    }

    /**
     * T-1191: piece [q] is a fork of the piece being painted, leaving (or joining) it at a shared vertex at a
     * shallow angle, and the painted piece is the one that road runs straight on through: its lines then give
     * way to [q] only within [MARK_MOUTH_M] of the vertex ([len] the line's length), not over the whole overlap.
     * Needs a third piece at the vertex to say which is straight on; with none, both give way, as before.
     */
    private fun mouth(q: Int, len: Float) {
        val o = markOwn
        for (a in 0..1) for (b in 0..1) {
            val so = a * 2; val sq = b * 2
            if (!joined(o + so, q + sq) || awayDot(o, so, q, sq) < MARK_PARALLEL) continue
            for (k in 0 until markNearCount) {
                val r = markNear[k]
                if (r == q) continue
                for (c in 0..1) if (joined(o + so, r + c * 2)) {
                    val straight = -awayDot(o, so, r, c * 2)
                    if (straight >= MARK_PARALLEL && straight > -awayDot(q, sq, r, c * 2) + 0.001f) {
                        if (so == 0) coverTo = min(coverTo, MARK_MOUTH_M / len) else coverFrom = max(coverFrom, 1f - MARK_MOUTH_M / len)
                        return
                    }
                }
            }
        }
    }

    /** Narrows [coverFrom]..[coverTo] to where a + t(b - a) lies in [lo]..[hi]; false when nothing is left. */
    private fun clip(a: Float, b: Float, lo: Float, hi: Float): Boolean {
        val d = b - a
        if (abs(d) < 1e-6f) return a in lo..hi
        val t0 = (lo - a) / d
        val t1 = (hi - a) / d
        coverFrom = max(coverFrom, min(t0, t1))
        coverTo = min(coverTo, max(t0, t1))
        return coverFrom < coverTo
    }

    /**
     * Notes where the line a–b (the line's own ends, with its offset applied; unit direction
     * [ue], [un]; [off] its offset) lies on piece [q]'s surface, less [MARK_CUT_M]. Not where [q]
     * is the same road carrying on: near-parallel, with the line at the same offset from it.
     * Does not cut for more than [MARK_CUTS] roads on one line: the rest are painted over.
     */
    private fun cover(q: Int, ae: Float, an: Float, be: Float, bn: Float, ue: Float, un: Float, off: Float) {
        val qe0 = pieces[q]; val qn0 = pieces[q + 1]
        val ql = hypot(pieces[q + 2] - qe0, pieces[q + 3] - qn0)
        val limit = pieces[q + 4] - MARK_CUT_M
        if (ql < MIN_SEGMENT_M || limit <= 0f || cutCount == MARK_CUTS) return
        val qe = (pieces[q + 2] - qe0) / ql
        val qn = (pieces[q + 3] - qn0) / ql
        val past = min(pieces[q + 4], 2f)
        val ua = (ae - qe0) * qe + (an - qn0) * qn; val ub = (be - qe0) * qe + (bn - qn0) * qn
        val va = (ae - qe0) * qn - (an - qn0) * qe; val vb = (be - qe0) * qn - (bn - qn0) * qe
        coverFrom = 0f; coverTo = 1f
        if (!clip(ua, ub, -past, ql + past) || !clip(va, vb, -limit, limit)) return
        val par = ue * qe + un * qn
        if (abs(par) >= MARK_PARALLEL) {
            // T-1191: a bend swings the line's offset sideways by off * (1 - cos), so the tolerance grows with it;
            // and it is judged at the nearer end of the overlap, where the ways meet, not its middle.
            val want = if (par < 0f) -off else off
            val dev = min(abs(va + (vb - va) * coverFrom - want), abs(va + (vb - va) * coverTo - want))
            if (dev <= MARK_SAME_M + abs(off) * (1f - abs(par)) && carriesOn(q, par)) return
            mouth(q, hypot(be - ae, bn - an))
            if (coverFrom >= coverTo) return
        }
        cutAt[cutCount * 2] = coverFrom; cutAt[cutCount * 2 + 1] = coverTo
        cutCount++
    }

    /**
     * A line along piece [o]'s segment e0,n0–e1,n1, [off] to its right, in the runs of it that
     * lie on no other road's surface (T-1182): where roads of a width meet, none of either's
     * lines crosses the other. Does not know a road by its class, only by where its surface is.
     */
    private fun paint(o: Int, e0: Float, n0: Float, e1: Float, n1: Float, off: Float, color: Int) {
        val len = hypot(e1 - e0, n1 - n0)
        if (len < MIN_SEGMENT_M) return
        val ue = (e1 - e0) / len
        val un = (n1 - n0) / len
        val ae = e0 + un * off; val an = n0 - ue * off
        val be = e1 + un * off; val bn = n1 - ue * off
        cutCount = 0
        for (k in 0 until markNearCount) cover(markNear[k], ae, an, be, bn, ue, un, off)
        // Few cuts, so an insertion sort by where each starts.
        for (i in 1 until cutCount) {
            val f = cutAt[i * 2]; val t = cutAt[i * 2 + 1]
            var j = i - 1
            while (j >= 0 && cutAt[j * 2] > f) { cutAt[j * 2 + 2] = cutAt[j * 2]; cutAt[j * 2 + 3] = cutAt[j * 2 + 1]; j-- }
            cutAt[j * 2 + 2] = f; cutAt[j * 2 + 3] = t
        }
        var from = 0f
        for (i in 0 until cutCount) {
            if (cutAt[i * 2] > from) run(o, ae, an, be, bn, len, from, cutAt[i * 2], color)
            from = max(from, cutAt[i * 2 + 1])
        }
        if (from < 1f) run(o, ae, an, be, bn, len, from, 1f, color)
    }

    private fun run(o: Int, ae: Float, an: Float, be: Float, bn: Float, len: Float, t0: Float, t1: Float, color: Int) {
        if ((t1 - t0) * len < MIN_SEGMENT_M) return
        val e0 = ae + (be - ae) * t0; val n0 = an + (bn - an) * t0
        val e1 = ae + (be - ae) * t1; val n1 = an + (bn - an) * t1
        lineTap?.invoke(floatArrayOf(e0, n0, e1, n1, color.toFloat(), pieces[o], pieces[o + 1], pieces[o + 2], pieces[o + 3], pieces[o + 4]))
        stripe(e0, n0, e1, n1, 0f, LINE_HALF, 0f, color)
    }

    private fun zebra(o: Int) {
        val e = crossings[o]; val n = crossings[o + 1]
        val ue = crossings[o + 2]; val un = crossings[o + 3]
        val half = crossings[o + 4] - EDGE_IN_M
        var across = -half + ZEBRA_EVERY_M / 2
        while (across < half) {
            stripe(e - ue * ZEBRA_LONG_M / 2, n - un * ZEBRA_LONG_M / 2, e + ue * ZEBRA_LONG_M / 2, n + un * ZEBRA_LONG_M / 2,
                across, ZEBRA_EVERY_M / 4, 0f, Color.WORLD_MARKING)
            across += ZEBRA_EVERY_M
        }
    }

    private fun bay(i: Int) {
        val r = bays[i]
        val n = bayLen[i]
        for (k in 0 until n) {
            val j = (k + 1) % n
            stripe(r[k * 2], r[k * 2 + 1], r[j * 2], r[j * 2 + 1], 0f, LINE_HALF, 0f, Color.WORLD_MARKING)
        }
    }

    /**
     * The car's own lights on the road ahead of it after dark (T-1164), in bands that fade
     * with distance. Does not light what stands in them: a wall ahead stays its night tone.
     */
    private fun headlights() {
        glows = true
        for (k in 0 until BEAM.size / 3 - 1) {
            val o = k * 3
            car(0, -BEAM[o + 1], 0f, BEAM[o]); car(1, BEAM[o + 1], 0f, BEAM[o])
            car(2, BEAM[o + 4], 0f, BEAM[o + 3]); car(3, -BEAM[o + 4], 0f, BEAM[o + 3])
            // Each band at the mean of its two ends, so the last fades out and does not end in an edge.
            solid(4, BEAM_INK or (((BEAM[o + 2] + BEAM[o + 5]) / 2 * night).toInt() shl 24), FLAT)
        }
        glows = false
    }

    /** The shadow of a tree's crown, on the ground away from the sun (T-1164). */
    private fun treeShadow(o: Int) {
        val r = trees[o + 2]
        val far = min(SHADOW_MAX_M, (TRUNK_M + r) * reach)
        if (far < SHADOW_MIN_M) return
        val e = trees[o] - sunE * far
        val n = trees[o + 1] - sunN * far
        for (k in 0 until POOL_SIDES) world(k, e + r * COS8[k], n + r * SIN8[k], 0f)
        glows = true
        solid(POOL_SIDES, ink(SHADOW_ALPHA * day), FLAT)
        glows = false
    }

    /** The light a lamp throws on the ground under it. */
    private fun pool(e: Float, n: Float) {
        for (k in 0 until POOL_SIDES) world(k, e + POOL_M * COS8[k], n + POOL_M * SIN8[k], 0f)
        glows = true
        solid(POOL_SIDES, rgb((POOL_ALPHA * night).toInt(), Color.WORLD_LAMP shr 16 and 0xFF, Color.WORLD_LAMP shr 8 and 0xFF, Color.WORLD_LAMP and 0xFF), FLAT)
        glows = false
    }

    private fun building(i: Int) {
        val r = bRing[i]
        val n = bLen[i]
        val top = bTop[i]
        val base = bBase[i]
        val camE = pe + HoloCamera.CAM_Z * sinH + HoloCamera.CAM_X * cosH
        val camN = pn + HoloCamera.CAM_Z * cosH - HoloCamera.CAM_X * sinH
        val wall = bWall[i]
        val rise = bRise[i]
        if (rise == 0f) {
            // Before the walls: ties in the frame's buckets keep the order given, and a side
            // wall at the roof's own distance was painted under it (T-1136).
            grow(n)
            for (k in 0 until n) world(k, r[k * 2], r[k * 2 + 1], top)
            solid(n, bRoof[i], RAISED)
        }
        // Its shadow and the dark at its foot lie on the ground it stands on (T-1164). A part
        // that starts above the ground throws none of its own: the block under it does, as far
        // as that block is tall (a tower on a podium throws the podium's shadow, not its own).
        val grounded = clocked && base == 0f
        val thrown = if (grounded && hypot(bMidE[i] - pe, bMidN[i] - pn) < SHADOW_RADIUS) min(SHADOW_MAX_M, top * reach) else 0f
        for (k in 0 until n) {
            val j = (k + 1) % n
            val ae = r[k * 2]; val an = r[k * 2 + 1]
            val be = r[j * 2]; val bn = r[j * 2 + 1]
            val de = be - ae
            val dn = bn - an
            val len = hypot(de, dn)
            if (len < MIN_SEGMENT_M) continue
            // Outward is right of the edge for an anticlockwise ring, left for a clockwise.
            val oe = (if (bCcw[i]) dn else -dn) / len
            val on = (if (bCcw[i]) -de else de) / len
            // The edges facing away from the sun sweep the shadow: side by side they cover it
            // once, and none of it under the building. A ring with a notch overlaps itself there.
            if (thrown > SHADOW_MIN_M && oe * sunE + on * sunN < 0f) {
                val se = -sunE * thrown
                val sn = -sunN * thrown
                // The shadow lies on the land it falls on, not level at the building's own foot.
                val held = pinned
                pinned = Float.NaN
                world(0, ae, an, 0f); world(1, be, bn, 0f); world(2, be + se, bn + sn, 0f); world(3, ae + se, an + sn, 0f)
                glows = true
                solid(4, ink(SHADOW_ALPHA * day), FLAT)
                glows = false
                pinned = held
            }
            // A wall facing away is behind its own building.
            if (oe * (camE - (ae + be) / 2) + on * (camN - (an + bn) / 2) <= 0f) continue
            val lit = WALL_DARK + (1f - WALL_DARK) * max(0f, oe * sunE + on * sunN)
            world(0, ae, an, base); world(1, be, bn, base); world(2, be, bn, top); world(3, ae, an, top)
            val depth = solid(4, shade(wall, lit), RAISED)
            if (depth < 0f || depth > ROW_RANGE_M) continue
            if (depth > WINDOW_RANGE_M) {
                // Too far to tell a window from the next: a row a floor, one quad each (T-1185).
                if (bGlazed[i] && len >= 2 * WINDOW_EVERY_M) {
                    val g = shade(mix(Color.WORLD_GLASS, wall, 0.12f), 0.7f + 0.3f * lit)
                    val s0 = ROW_PAD_M / len
                    val s1 = 1f - s0
                    var y = base + SILL_M
                    while (y + WINDOW_H_M < top && rows < MAX_ROWS) {
                        world(0, ae + de * s0, an + dn * s0, y); world(1, ae + de * s1, an + dn * s1, y)
                        world(2, ae + de * s1, an + dn * s1, y + WINDOW_H_M); world(3, ae + de * s0, an + dn * s0, y + WINDOW_H_M)
                        solid(4, g, depth)
                        rows++
                        y += FLOOR_M
                    }
                }
                continue
            }
            if (grounded && depth <= LINE_RANGE_M) {
                // Less light gets in where a wall meets the ground: a dark strip at its foot.
                world(0, ae, an, 0f); world(1, be, bn, 0f)
                world(2, be + oe * FOOT_M, bn + on * FOOT_M, 0f); world(3, ae + oe * FOOT_M, an + on * FOOT_M, 0f)
                glows = true
                solid(4, ink(FOOT_ALPHA), FLAT)
                glows = false
            }
            val glass = shade(mix(Color.WORLD_GLASS, wall, 0.12f), 0.7f + 0.3f * lit)
            val front = k == bFront[i] && base == 0f
            val shop = front && bSign[i] != null && len >= SHOP_WALL_M && top >= SHOP_TOP_M + SILL_M
            if (depth <= LINE_RANGE_M) wallLines(i, ae, an, de, dn, len, base, top, shade(wall, lit * LINE_DARK), depth)
            if (shop) shopfront(i, ae, an, de, dn, len, glass, lit, depth)
            if (front && !shop && bHome[i]) {
                // A home's door, in the middle of the wall that faces its street.
                val s0 = 0.5f - DOOR_HALF / len
                val s1 = 0.5f + DOOR_HALF / len
                world(0, ae + de * s0, an + dn * s0, 0f); world(1, ae + de * s1, an + dn * s1, 0f)
                world(2, ae + de * s1, an + dn * s1, DOOR_M); world(3, ae + de * s0, an + dn * s0, DOOR_M)
                solid(4, shade(bRoof[i], DOOR_DARK * lit), depth)
            }
            if (!bGlazed[i]) continue
            val cols = min(MAX_WINDOW_COLS, (len / WINDOW_EVERY_M).toInt())
            val pad = (len - cols * WINDOW_EVERY_M) / 2 + (WINDOW_EVERY_M - WINDOW_W_M) / 2
            // A shop's ground floor is its front; a home's door takes the window it would stand in.
            var y = base + SILL_M + if (shop) FLOOR_M else 0f
            val door = front && !shop && bHome[i]
            while (y + WINDOW_H_M < top && windows < MAX_WINDOWS) {
                for (c in 0 until cols) {
                    val s0 = (pad + c * WINDOW_EVERY_M) / len
                    val s1 = (pad + c * WINDOW_EVERY_M + WINDOW_W_M) / len
                    if (door && y < DOOR_M && s1 * len > len / 2 - DOOR_HALF - DOOR_GAP && s0 * len < len / 2 + DOOR_HALF + DOOR_GAP) continue
                    world(0, ae + de * s0, an + dn * s0, y); world(1, ae + de * s1, an + dn * s1, y)
                    world(2, ae + de * s1, an + dn * s1, y + WINDOW_H_M); world(3, ae + de * s0, an + dn * s0, y + WINDOW_H_M)
                    // After dark some windows have a light on: the same ones every frame and every drive.
                    val on = clocked && night > LIT_FROM && (hash(bSeed[i] * 131 + k, (y * 4).toInt() * 31 + c) and 7) < LIT_OF_8
                    if (on) { glows = true; litWindows++ }
                    solid(4, if (on) mix(glass, Color.WORLD_WINDOW_LIT, (night - LIT_FROM) / (1f - LIT_FROM)) else glass, depth)
                    glows = false
                    windows++
                }
                y += FLOOR_M
            }
        }
        if (rise == 0f) return
        val ridged = !bRidge[i * 4].isNaN()
        val re = bRidge[i * 4]
        val rn = bRidge[i * 4 + 1]
        val de0 = bRidge[i * 4 + 2] - re
        val dn0 = bRidge[i * 4 + 3] - rn
        val l2 = de0 * de0 + dn0 * dn0
        for (k in 0 until n) {
            val j = (k + 1) % n
            val de = r[j * 2] - r[k * 2]
            val dn = r[j * 2 + 1] - r[k * 2 + 1]
            val len = hypot(de, dn)
            if (len < MIN_SEGMENT_M) continue
            val oe = (if (bCcw[i]) dn else -dn) / len
            val on = (if (bCcw[i]) -de else de) / len
            val color = shade(bRoof[i], ROOF_DARK + (1f - ROOF_DARK) * max(0f, oe * sunE + on * sunN))
            world(0, r[k * 2], r[k * 2 + 1], top); world(1, r[j * 2], r[j * 2 + 1], top)
            if (!ridged) {
                world(2, bMidE[i], bMidN[i], top + rise)
                solid(3, color, RAISED)
                continue
            }
            // Each corner climbs to the nearest point of the ridge: a long wall's slope is a
            // trapezoid, a gable end a triangle, a hip's end a triangle that tapers to the ridge's end.
            val ta = if (l2 == 0f) 0f else (((r[k * 2] - re) * de0 + (r[k * 2 + 1] - rn) * dn0) / l2).coerceIn(0f, 1f)
            val tb = if (l2 == 0f) 0f else (((r[j * 2] - re) * de0 + (r[j * 2 + 1] - rn) * dn0) / l2).coerceIn(0f, 1f)
            world(2, re + de0 * tb, rn + dn0 * tb, top + rise); world(3, re + de0 * ta, rn + dn0 * ta, top + rise)
            solid(4, color, RAISED)
        }
    }

    /**
     * A shop's ground floor on the wall from ([ae], [an]) along ([de], [dn]) (T-1106): glass
     * from the stall riser up, a door in it, and over them a board in its trade's colour with
     * the business's name, as large as fits the wall. Does not draw a logo: the map has names.
     */
    private fun shopfront(i: Int, ae: Float, an: Float, de: Float, dn: Float, len: Float, glass: Int, lit: Float, depth: Float) {
        val s0 = SHOP_INSET_M / len
        val s1 = 1f - s0
        world(0, ae + de * s0, an + dn * s0, SHOP_RISER_M); world(1, ae + de * s1, an + dn * s1, SHOP_RISER_M)
        world(2, ae + de * s1, an + dn * s1, SHOP_GLASS_M); world(3, ae + de * s0, an + dn * s0, SHOP_GLASS_M)
        solid(4, glass, depth)
        val d0 = 0.5f - DOOR_HALF / len
        val d1 = 0.5f + DOOR_HALF / len
        world(0, ae + de * d0, an + dn * d0, 0f); world(1, ae + de * d1, an + dn * d1, 0f)
        world(2, ae + de * d1, an + dn * d1, DOOR_M); world(3, ae + de * d0, an + dn * d0, DOOR_M)
        solid(4, shade(glass, DOOR_DARK), depth)
        world(0, ae + de * s0, an + dn * s0, SHOP_GLASS_M); world(1, ae + de * s1, an + dn * s1, SHOP_GLASS_M)
        world(2, ae + de * s1, an + dn * s1, SHOP_TOP_M); world(3, ae + de * s0, an + dn * s0, SHOP_TOP_M)
        val board = solid(4, shade(bFascia[i], 0.75f + 0.25f * lit), depth)
        val name = bSign[i] ?: return
        if (shopSigns == MAX_SHOP_SIGNS) return
        val h = min(SHOP_TEXT_M, (len - 2 * SHOP_INSET_M - SHOP_TEXT_PAD_M) / (name.length * SHOP_CHAR))
        if (h < SHOP_TEXT_MIN_M) return  // a board too short for its name stays a board
        val before = frame.signCount
        say(name, carX(ae + de / 2, an + dn / 2), (SHOP_GLASS_M + SHOP_TOP_M) / 2, carZ(ae + de / 2, an + dn / 2), h, Color.WORLD_SIGN_PLATE, board)
        if (frame.signCount > before) shopSigns++
    }

    /** A near wall's joints (T-1106): upright panel joints on a shop, an office or a shed, a course a floor on anything else. */
    private fun wallLines(i: Int, ae: Float, an: Float, de: Float, dn: Float, len: Float, base: Float, top: Float, color: Int, depth: Float) {
        if (bPanel[i]) {
            var s = PANEL_EVERY_M
            while (s < len - 1f && wallLines < MAX_WALL_LINES) {
                val s0 = (s - LINE_HALF) / len
                val s1 = (s + LINE_HALF) / len
                world(0, ae + de * s0, an + dn * s0, base); world(1, ae + de * s1, an + dn * s1, base)
                world(2, ae + de * s1, an + dn * s1, top); world(3, ae + de * s0, an + dn * s0, top)
                solid(4, color, depth)
                wallLines++
                s += PANEL_EVERY_M
            }
            return
        }
        var y = base + FLOOR_M
        while (y < top - 1f && wallLines < MAX_WALL_LINES) {
            world(0, ae, an, y - LINE_HALF); world(1, ae + de, an + dn, y - LINE_HALF)
            world(2, ae + de, an + dn, y + LINE_HALF); world(3, ae, an, y + LINE_HALF)
            solid(4, color, depth)
            wallLines++
            y += FLOOR_M
        }
    }

    /**
     * A tree faces the camera: a trunk, its leaves in shade and a lit crown over them. Its size
     * picks one of three crowns (tall and narrow, round, broad with a low bough) and its tone
     * of green (T-1106), the same tree every drive. Does not know the species: the map rarely does.
     */
    private fun tree(o: Int) {
        val x = carX(trees[o], trees[o + 1])
        val z = carZ(trees[o], trees[o + 1])
        val r = trees[o + 2]
        car(0, x - TRUNK_HALF, 0f, z); car(1, x + TRUNK_HALF, 0f, z); car(2, x + TRUNK_HALF, TRUNK_M, z); car(3, x - TRUNK_HALF, TRUNK_M, z)
        val depth = solid(4, Color.WORLD_TRUNK, RAISED)
        if (depth < 0f) return
        val seed = ((r - 1.9f) / 0.13f + 0.5f).toInt()
        val form = seed % 3
        val wide = if (form == 0) CROWN_NARROW else 1f
        val tall = if (form == 0) CROWN_TALL else 1f
        val dark = vary(Color.WORLD_CANOPY_SHADE, seed)
        val light = vary(Color.WORLD_CANOPY, seed)
        val cy = TRUNK_M + r * tall * 0.8f
        if (form == 2) {
            // The low bough of a broad tree, out to one side under the crown.
            for (k in 0 until POOL_SIDES) car(k, x + r * 0.75f + r * 0.5f * COS8[k], cy - r * 0.45f + r * 0.42f * SIN8[k], z)
            solid(POOL_SIDES, dark, depth)
        }
        for (k in 0 until POOL_SIDES) car(k, x + r * wide * COS8[k], cy + r * tall * SIN8[k], z)
        solid(POOL_SIDES, dark, depth)
        for (k in 0 until POOL_SIDES) car(k, x - r * wide * 0.2f + r * wide * 0.62f * COS8[k], cy + r * tall * 0.24f + r * tall * 0.62f * SIN8[k], z)
        solid(POOL_SIDES, light, depth)
    }

    /**
     * Pins what is drawn next to the bridge deck at ([e], [n]) when there is one (T-1232): a
     * lamp, a signal or a sign on an overpass stands on its road, not on the ground under it,
     * where the deck hid every signal of a junction built on a bridge. Does not cover one
     * mapped further off the deck than [DECK_EDGE_M], nor paint (a zebra, a stop line).
     */
    private fun onItsDeck(e: Float, n: Float) {
        val d = deckAt(e, n, 0f, 0f)
        pinned = if (d.isNaN()) Float.NaN else d - carUp
    }

    private fun lamp(e: Float, n: Float) {
        val x = carX(e, n)
        val z = carZ(e, n)
        car(0, x - POLE_HALF, 0f, z); car(1, x + POLE_HALF, 0f, z); car(2, x + POLE_HALF, LAMP_M, z); car(3, x - POLE_HALF, LAMP_M, z)
        val depth = solid(4, Color.WORLD_POLE, RAISED)
        if (depth < 0f) return
        car(0, x - 0.45f, LAMP_M, z); car(1, x + 0.45f, LAMP_M, z); car(2, x + 0.45f, LAMP_M + 0.22f, z); car(3, x - 0.45f, LAMP_M + 0.22f, z)
        // Its head: lit after dark, a dull housing by day.
        glows = true
        solid(4, mix(shade(Color.WORLD_LAMP, LAMP_OFF), Color.WORLD_LAMP, night), depth)
        glows = false
    }

    /**
     * A signal's pole, its housing and its three lenses: red over amber over green, each in
     * its own colour and none lit (T-1104). Which one is on is not drawn: nothing says.
     */
    private fun signal(e: Float, n: Float) {
        val x = carX(e, n)
        val z = carZ(e, n)
        car(0, x - POLE_HALF, 0f, z); car(1, x + POLE_HALF, 0f, z); car(2, x + POLE_HALF, SIGNAL_M, z); car(3, x - POLE_HALF, SIGNAL_M, z)
        val depth = solid(4, Color.WORLD_POLE, RAISED)
        if (depth < 0f) return
        car(0, x - 0.24f, SIGNAL_M, z); car(1, x + 0.24f, SIGNAL_M, z); car(2, x + 0.24f, SIGNAL_M + 1.1f, z); car(3, x - 0.24f, SIGNAL_M + 1.1f, z)
        solid(4, Color.WORLD_SIGNAL, depth)
        lens(x, SIGNAL_M + 0.76f, z, Color.WORLD_SIGNAL_RED, depth)
        lens(x, SIGNAL_M + 0.42f, z, Color.WORLD_SIGNAL_AMBER, depth)
        lens(x, SIGNAL_M + 0.08f, z, Color.WORLD_SIGNAL_GREEN, depth)
    }

    private fun lens(x: Float, y: Float, z: Float, color: Int, depth: Float) {
        plate(x, y, LENS_HALF, 2 * LENS_HALF, z)
        solid(4, color, depth)
    }

    /**
     * [text] on a sign, centred on ([x], [y]) at [z] and [h] metres tall (T-1104). Only near
     * ([TEXT_RADIUS]) and only large enough to read; the frame keeps the first few. [plate] is
     * what [solid] answered for the plate, drawn just before: the words go on that solid, so
     * what hides the plate hides them (T-1120), and a plate that was not drawn has none.
     */
    private fun say(text: String?, x: Float, y: Float, z: Float, h: Float, color: Int, plate: Float) {
        if (text.isNullOrEmpty() || plate < 0f) return
        cam.view(x, y - h * TEXT_DROP + lift(x, z), z, polyCam, 0)
        val zc = polyCam[2]
        if (zc < HoloCamera.NEAR || zc > TEXT_RADIUS) return
        val px = h * cam.f / zc
        if (px < cam.labelMinPx * TEXT_MIN) return
        frame.sign(text, cam.screenX(polyCam[0], zc), cam.screenY(polyCam[1], zc), px, color)
    }

    /**
     * A junction's two street names on one post, one blade over the other (T-1104). Each blade
     * faces the traffic on the street it names (T-1183): square to that street's way, so a car
     * on it sees the blade full, and a car on the other street sees it edge-on, with no words
     * (below [BLADE_FACE_MIN] of the plate showing, the words would be a smear).
     */
    private fun blade(i: Int) {
        val e = blades[i * 2]
        val n = blades[i * 2 + 1]
        val x = carX(e, n)
        val z = carZ(e, n)
        if (z < PASSED_M) return
        car(0, x - POLE_HALF, 0f, z); car(1, x + POLE_HALF, 0f, z); car(2, x + POLE_HALF, BLADE_M, z); car(3, x - POLE_HALF, BLADE_M, z)
        val depth = solid(4, Color.WORLD_POLE, RAISED)
        if (depth < 0f) return
        val away = hypot(e - pe, n - pn)
        for (k in 0..1) {
            val name = bladeNames[i * 2 + k] ?: continue
            val we = bladeWays[(i * 2 + k) * 2]
            val wn = bladeWays[(i * 2 + k) * 2 + 1]
            val y = BLADE_M + k * (BLADE_H + BLADE_GAP)
            plateAlong(e, n, -wn, we, y, name.length * BLADE_H * BLADE_CHAR + BLADE_GAP, BLADE_H)
            val face = if (away < MIN_SEGMENT_M) 1f else abs((e - pe) * we + (n - pn) * wn) / away
            say(if (face >= BLADE_FACE_MIN) name else null, x, y + BLADE_H / 2, z, BLADE_H * 0.7f, Color.WORLD_SIGN_PLATE, solid(4, Color.WORLD_SIGN_BLADE, depth))
        }
    }

    /**
     * A numbered road's number on a plate on a post (T-1167), in its network's shape (T-1183):
     * the number alone, as the shape says the network. A plain plate says the whole ref.
     */
    private fun shield(i: Int) {
        val text = shieldText[i] ?: return
        val x = carX(shields[i * 2], shields[i * 2 + 1])
        val z = carZ(shields[i * 2], shields[i * 2 + 1])
        if (z < PASSED_M) return
        car(0, x - POLE_HALF, 0f, z); car(1, x + POLE_HALF, 0f, z); car(2, x + POLE_HALF, SIGN_M, z); car(3, x - POLE_HALF, SIGN_M, z)
        val depth = solid(4, Color.WORLD_POLE, RAISED)
        if (depth < 0f) return
        val half = max(SHIELD_H / 2, text.length * SHIELD_H * BLADE_CHAR + BLADE_GAP)
        val shape = when (shieldNet[i]) {
            "interstate" -> SHAPE_INTERSTATE
            "us" -> SHAPE_US
            "state" -> SHAPE_STATE
            else -> SHAPE_PLAIN
        }
        for (k in 0 until shape.size / 2) car(k, x + shape[k * 2] * half, SIGN_M - SHIELD_H / 2 + shape[k * 2 + 1] * SHIELD_H, z)
        say(text, x, SIGN_M, z, SHIELD_H * 0.7f, Color.WORLD_SIGN_INK, solid(shape.size / 2, Color.WORLD_SIGN_PLATE, depth))
    }

    /** A green guide board on a post at a link's split: its rows, the exit first (T-1183). */
    private fun guide(i: Int) {
        val rows = guideRows[i] ?: return
        val x = carX(guides[i * 2], guides[i * 2 + 1])
        val z = carZ(guides[i * 2], guides[i * 2 + 1])
        if (z < PASSED_M) return
        val high = GUIDE_LOW_M + rows.size * (GUIDE_H + BLADE_GAP) + BLADE_GAP
        car(0, x - POLE_HALF, 0f, z); car(1, x + POLE_HALF, 0f, z); car(2, x + POLE_HALF, high, z); car(3, x - POLE_HALF, high, z)
        val depth = solid(4, Color.WORLD_POLE, RAISED)
        if (depth < 0f) return
        val half = rows.maxOf { it.length } * GUIDE_H * BLADE_CHAR + BLADE_GAP
        plate(x, GUIDE_LOW_M, half, high - GUIDE_LOW_M, z)
        val board = solid(4, Color.WORLD_SIGN_BLADE, depth)
        for ((k, row) in rows.withIndex()) {
            val mid = high - BLADE_GAP - GUIDE_H / 2 - k * (GUIDE_H + BLADE_GAP)
            say(row, x, mid, z, GUIDE_H * 0.7f, Color.WORLD_SIGN_PLATE, board)
        }
    }

    /** The words a link's board carries (T-1183): "EXIT 12B", its route numbers, its places; none when it has none. */
    private fun boardRows(r: HoloRoad): List<String> {
        fun parts(s: String?) = s.orEmpty().split(';').map { it.trim() }.filter { it.isNotEmpty() }
        val rows = ArrayList<String>(3)
        parts(r.junctionRef).firstOrNull()?.let { rows += whole("EXIT $it", GUIDE_CHARS) }
        parts(r.destinationRef).takeIf { it.isNotEmpty() }?.let { rows += whole(it.joinToString(", "), GUIDE_CHARS) }
        parts(r.destination).takeIf { it.isNotEmpty() }?.let { rows += whole(it.joinToString(" / "), GUIDE_CHARS) }
        return rows
    }

    /** The first ref a street carries, "" for none: what a marker says and where the number changes. */
    private fun numberOf(r: HoloRoad): String = r.ref?.substringBefore(';')?.trim().orEmpty()

    private class Stand(val at: Float, val left: Boolean)

    /**
     * Where along [r]'s line its [number]'s markers stand (T-1183), by distance from the line's
     * start, nearest first: [SHIELD_AFTER_M] after each junction (a street, or another number,
     * meeting it, [meets]), then every [SHIELD_EVERY_M] until the next junction's, and none that
     * would stand closer than that to it. Each way a car can travel it; the right of that way.
     * Does not give a way no junction touches a marker: it has nowhere a driver has just turned on.
     */
    private fun standsOf(r: HoloRoad, cum: FloatArray, number: String, meets: Map<LatLon, Set<String>>): List<Stand> {
        val total = cum.last()
        val out = ArrayList<Stand>()
        for (back in if (r.oneway > 0) listOf(false) else if (r.oneway < 0) listOf(true) else listOf(false, true)) {
            val joins = ArrayList<Float>()
            for (k in if (back) r.line.indices.reversed() else r.line.indices) {
                if (meets[r.line[k]]?.any { it != number } == true) joins += if (back) total - cum[k] else cum[k]
            }
            for ((j, at) in joins.withIndex()) {
                val first = at + SHIELD_AFTER_M
                if (first > total) continue
                val next = if (j + 1 < joins.size) joins[j + 1] + SHIELD_AFTER_M else Float.MAX_VALUE
                var s = first
                while (s <= total) {
                    out += Stand(if (back) total - s else s, back)
                    s += SHIELD_EVERY_M
                    if (next - s < SHIELD_EVERY_M) break
                }
            }
        }
        return out.sortedBy { it.at }
    }

    /** A guard rail or a concrete barrier along its line (T-1104). */
    private fun rail(o: Int) {
        world(0, rails[o], rails[o + 1], RAIL_LOW_M); world(1, rails[o + 2], rails[o + 3], RAIL_LOW_M)
        world(2, rails[o + 2], rails[o + 3], RAIL_TOP_M); world(3, rails[o], rails[o + 1], RAIL_TOP_M)
        solid(4, Color.WORLD_BARRIER, RAISED)
    }

    /**
     * A sign or a camera on its post (T-1073), turned to the camera like a tree: a stop sign's
     * octagon, a give-way's triangle, a speed limit's plate, any other sign's diamond; a speed
     * camera's box, a camera's housing, and a plate reader's housing under its solar panel.
     * Since T-1104 a sign says what the map says it does: STOP, a limit's number on its plate,
     * any other sign's words on a plate under its diamond; a sign the map gives no words is
     * blank. A bollard is a short post and a gate a bar across between two.
     */
    private fun post(o: Int) {
        val x = carX(posts[o], posts[o + 1])
        val z = carZ(posts[o], posts[o + 1])
        val kind = posts[o + 2].toInt()
        val says = postText[o / 3]
        if (kind == BOLLARD) {
            plate(x, 0f, BOLLARD_HALF, BOLLARD_M, z)
            solid(4, Color.WORLD_BARRIER, RAISED)
            return
        }
        if (kind == GATE) {
            plate(x, GATE_M, GATE_HALF, 2 * POLE_HALF, z)
            val bar = solid(4, Color.WORLD_CONE, RAISED)
            if (bar < 0f) return
            plate(x - GATE_HALF, 0f, POLE_HALF, GATE_M, z)
            solid(4, Color.WORLD_POLE, bar)
            plate(x + GATE_HALF, 0f, POLE_HALF, GATE_M, z)
            solid(4, Color.WORLD_POLE, bar)
            return
        }
        val top = if (kind >= CAMERA) CAMERA_M else if (kind == SPEED_CAMERA) SPEED_CAMERA_M else SIGN_M
        car(0, x - POLE_HALF, 0f, z); car(1, x + POLE_HALF, 0f, z); car(2, x + POLE_HALF, top, z); car(3, x - POLE_HALF, top, z)
        val depth = solid(4, Color.WORLD_POLE, RAISED)
        if (depth < 0f) return
        val r = SIGN_HALF_M
        when (kind) {
            STOP -> {
                for (k in 0 until 8) {
                    val a = (k + 0.5f) * (PI.toFloat() / 4f)
                    car(k, x + r * cos(a), top + r * sin(a), z)
                }
                say(says, x, top, z, r * 0.56f, Color.WORLD_SIGN_PLATE, solid(8, Color.WORLD_SIGN_STOP, depth))
            }
            GIVE_WAY -> {
                car(0, x - r, top + r, z); car(1, x + r, top + r, z); car(2, x, top - r * 0.7f, z)
                solid(3, Color.WORLD_SIGN_PLATE, depth)
            }
            SPEED_SIGN -> {
                plate(x, top - r, r * 0.8f, 2.4f * r, z)
                say(says, x, top + 0.2f * r, z, r * 0.9f, Color.WORLD_SIGN_INK, solid(4, Color.WORLD_SIGN_PLATE, depth))
            }
            SIGN -> {
                car(0, x, top - r, z); car(1, x + r, top, z); car(2, x, top + r, z); car(3, x - r, top, z)
                solid(4, Color.WORLD_SIGN_WARN, depth)
                if (!says.isNullOrEmpty()) {
                    plate(x, top - r - WORDS_H - BLADE_GAP, says.length * WORDS_H * BLADE_CHAR + BLADE_GAP, WORDS_H, z)
                    say(says, x, top - r - WORDS_H / 2 - BLADE_GAP, z, WORDS_H * 0.7f, Color.WORLD_SIGN_INK, solid(4, Color.WORLD_SIGN_PLATE, depth))
                }
            }
            SPEED_CAMERA -> {
                plate(x, top, 0.38f, 0.9f, z)
                solid(4, Color.WORLD_CAMERA_SPEED, depth)
            }
            else -> {
                plate(x + 0.3f, top - 0.2f, 0.42f, 0.34f, z)
                solid(4, Color.WORLD_CAMERA, depth)
                if (kind == ALPR) {
                    plate(x, top + 0.25f, 0.6f, 0.16f, z)
                    solid(4, Color.WORLD_CAMERA_ALPR, depth)
                }
            }
        }
    }

    /** An upright rectangle [half] either side of [x], from [y] up [h], at [z]. */
    private fun plate(x: Float, y: Float, half: Float, h: Float, z: Float) {
        car(0, x - half, y, z); car(1, x + half, y, z); car(2, x + half, y + h, z); car(3, x - half, y + h, z)
    }

    /** An upright rectangle centred on ([e], [n]), [half] either side along ([ae], [an]), from [y] up [h] (T-1183). */
    private fun plateAlong(e: Float, n: Float, ae: Float, an: Float, y: Float, half: Float, h: Float) {
        world(0, e - ae * half, n - an * half, y); world(1, e + ae * half, n + an * half, y)
        world(2, e + ae * half, n + an * half, y + h); world(3, e - ae * half, n - an * half, y + h)
    }

    /**
     * A closed road's end (T-1108): a barricade across it, three striped rails on two legs.
     * ponytail: one width for every road (the feed gives a line, not which road); read the
     * road's lanes from the nearest piece if a lane-wide barricade on a wide road reads wrong.
     */
    private fun barricade(o: Int) {
        val e = closures[o]; val n = closures[o + 1]
        val ae = -closures[o + 3]; val an = closures[o + 2] // across the road
        for (rail in 0 until CLOSURE_RAILS) {
            val low = CLOSURE_LOW_M + rail * CLOSURE_STEP_M
            for (k in 0 until CLOSURE_STRIPES) {
                val a = -CLOSURE_HALF_M + 2 * CLOSURE_HALF_M * k / CLOSURE_STRIPES
                val b = -CLOSURE_HALF_M + 2 * CLOSURE_HALF_M * (k + 1) / CLOSURE_STRIPES
                world(0, e + ae * a, n + an * a, low); world(1, e + ae * b, n + an * b, low)
                world(2, e + ae * b, n + an * b, low + CLOSURE_RAIL_M); world(3, e + ae * a, n + an * a, low + CLOSURE_RAIL_M)
                solid(4, if ((k + rail) % 2 == 0) Color.WORLD_CONE else Color.WORLD_MARKING, RAISED)
            }
        }
        val top = CLOSURE_LOW_M + (CLOSURE_RAILS - 1) * CLOSURE_STEP_M + CLOSURE_RAIL_M
        for (side in intArrayOf(-1, 1)) {
            val a = side * (CLOSURE_HALF_M - 2 * POLE_HALF); val b = side * CLOSURE_HALF_M
            world(0, e + ae * a, n + an * a, 0f); world(1, e + ae * b, n + an * b, 0f)
            world(2, e + ae * b, n + an * b, top); world(3, e + ae * a, n + an * a, top)
            solid(4, Color.WORLD_POLE, RAISED)
        }
    }

    /** Roadworks: a board every few metres along the closed road. */
    private fun barrier(o: Int) {
        val e0 = barriers[o]; val n0 = barriers[o + 1]; val e1 = barriers[o + 2]; val n1 = barriers[o + 3]
        val len = hypot(e1 - e0, n1 - n0)
        if (len < MIN_SEGMENT_M) return
        val ue = (e1 - e0) / len
        val un = (n1 - n0) / len
        var s = 0f
        var k = 0
        while (s < len && k < MAX_BOARDS) {
            val t = min(len, s + BOARD_M)
            world(0, e0 + ue * s, n0 + un * s, 0.45f); world(1, e0 + ue * t, n0 + un * t, 0.45f)
            world(2, e0 + ue * t, n0 + un * t, 1.05f); world(3, e0 + ue * s, n0 + un * s, 1.05f)
            solid(4, if (k % 2 == 0) Color.WORLD_CONE else Color.WORLD_MARKING, RAISED)
            s += BOARD_EVERY_M
            k++
        }
    }

    /** A flat band along a segment: [off] to its right, [half] wide either side, [past] beyond each end. */
    private fun stripe(e0: Float, n0: Float, e1: Float, n1: Float, off: Float, half: Float, past: Float, color: Int) {
        val len = hypot(e1 - e0, n1 - n0)
        if (len < MIN_SEGMENT_M) return
        val ue = (e1 - e0) / len
        val un = (n1 - n0) / len
        val ae = e0 - ue * past + un * off; val an = n0 - un * past - ue * off
        val be = e1 + ue * past + un * off; val bn = n1 + un * past - ue * off
        // On a bridge the band lies on the deck, wherever along the piece it is.
        val ya = if (deck < 0) 0f else deckY(ae, an)
        val yb = if (deck < 0) 0f else deckY(be, bn)
        world(0, ae + un * half, an - ue * half, ya); world(1, be + un * half, bn - ue * half, yb)
        world(2, be - un * half, bn + ue * half, yb); world(3, ae - un * half, an + ue * half, ya)
        if (deck < 0) solid(4, color, FLAT) else onDeck(color)
    }

    private fun carX(e: Float, n: Float) = HoloScene.carX(e - pe, n - pn, sinH, cosH)

    private fun carZ(e: Float, n: Float) = HoloScene.carZ(e - pe, n - pn, sinH, cosH)

    private fun car(at: Int, x: Float, y: Float, z: Float) {
        polyIn[at * 3] = x; polyIn[at * 3 + 1] = y + lift(x, z); polyIn[at * 3 + 2] = z
    }

    private fun world(at: Int, e: Float, n: Float, y: Float) = car(at, carX(e, n), y, carZ(e, n))

    private fun grow(n: Int) {
        if (polyIn.size >= n * 3) return
        polyIn = FloatArray(n * 3)
        polyCam = FloatArray(n * 3)
        polyOut = FloatArray(n * 3 + 3)
    }

    /**
     * The polygon of [n] points in [polyIn], clipped, faded into the distance and put in the
     * frame. [depth] is [FLAT] for something lying on the ground (drawn first, in the order
     * given), [RAISED] to order it by where it stands (its corners' distance over the ground:
     * the camera looks down, so a tall wall's top would else read as nearer than a plate
     * standing in front of the wall, T-1131), or a distance to draw it with
     * something already drawn there. Answers the distance it was given, or -1 when nothing
     * was drawn (behind the camera, or lost in the haze).
     */
    private fun solid(n: Int, color: Int, depth: Float): Float {
        var nearest = Float.MAX_VALUE
        var sum = 0f
        for (k in 0 until n) {
            cam.view(polyIn[k * 3], polyIn[k * 3 + 1], polyIn[k * 3 + 2], polyCam, k * 3)
            nearest = min(nearest, polyCam[k * 3 + 2])
            sum += HoloCamera.overGround(polyCam[k * 3 + 2], polyIn[k * 3 + 1])
        }
        val fog = HoloCamera.fog(max(HoloCamera.NEAR, nearest))
        if (fog <= MIN_FOG) return -1f
        val m = HoloCamera.clipPolygon(polyCam, n, polyOut)
        if (m < 3) return -1f
        frame.beginPoly()
        for (k in 0 until m) {
            val z = polyOut[k * 3 + 2]
            frame.point(cam.screenX(polyOut[k * 3], z), cam.screenY(polyOut[k * 3 + 1], z))
        }
        val at = if (depth == RAISED) max(0f, sum / n) else depth
        val seen = if (hazy) mix(Color.WORLD_HAZE, color, min(1f, fog)) else color
        frame.endSolid(if (day > 0f && !glows) daylit(seen, day) else seen, at)
        return at
    }

    companion object {
        /** [solid]'s depth for the ground's own layer, and for "order me by my distance". */
        const val FLAT = HoloFrame.FLAT
        private const val RAISED = -1f

        private const val RADIUS = HoloCamera.FOG_END_M
        /** Small things are not seen from farther than this. */
        private const val NEAR_RADIUS = 140f
        private const val MARK_RADIUS = 110f
        // T-1182: paint is cut where another road's surface is. A line keeps off a road by this much
        // of its half width; is the same road carrying on when this near-parallel and within this
        // of its own offset; looks at this many near pieces and cuts at most this many times.
        private const val MARK_CUT_M = 0.3f
        // T-1191: a bend of 45 degrees (cos 0.71) carries on, two ways a metre apart are one road, and a
        // road that a fork leaves gives up its lines only this far from the fork's vertex.
        private const val MARK_PARALLEL = 0.65f
        private const val MARK_SAME_M = 0.8f
        private const val MARK_JOIN_M = 1.5f
        private const val MARK_MOUTH_M = 12f
        private const val GRID_CELL_M = 64f
        private const val GRID_MAX = 48
        private const val MARK_NEAR = 24
        private const val MARK_CUTS = 12
        private const val MIN_FOG = 0.04f
        private const val MIN_SEGMENT_M = 0.5f

        // T-1185. The view's limits, each raised only with the dense-city timing test (HoloWorldTest)
        // green at its new value: the frame's cost is the sum of them all, a city's worst case.
        private const val MAX_BUILDINGS = 2400
        /** An outline with more corners than this is thinned by [thin], the least significant first. */
        internal const val MAX_RING_CORNERS = 32
        /** Past [WINDOW_RANGE_M] and to here a wall's windows are one row a floor; past it, none. */
        private const val ROW_RANGE_M = 700f
        private const val ROW_PAD_M = 1f
        private const val MAX_ROWS = 2000
        /** Parts whose middles lie in an outline and cover this much of it are drawn instead of it. */
        private const val COVER_SHARE = 0.6
        /** The grid parts are found on, in degrees (about 110 m). */
        private const val COVER_CELL_DEG = 0.001
        private const val MIN_HEIGHT_M = 3f
        private const val MAX_HEIGHT_M = 400f
        private const val FLOOR_M = 3f
        private const val SILL_M = 1f
        private const val WINDOW_H_M = 1.4f
        private const val WINDOW_W_M = 1.3f
        private const val WINDOW_EVERY_M = 3f
        private const val WINDOW_RANGE_M = 140f
        private const val MAX_WINDOW_COLS = 12
        private const val MAX_WINDOWS = 1400
        private const val PITCH_MAX_RING = 8
        // T-1106. A front is looked for only near, where it can be told from a wall.
        private const val FRONT_RADIUS = 110f
        private const val FRONT_REACH_M = 45f
        private const val FRONT_STEP_M = 1f
        /** Wider than half of any road, with its pavement. */
        private const val MAX_FRONT_HALF_M = 30f
        private const val DOOR_WALL_M = 2.4f
        private const val DOOR_HALF = 0.5f
        private const val DOOR_M = 2.1f
        private const val DOOR_GAP = 0.3f
        private const val DOOR_DARK = 0.5f
        private const val SHOP_WALL_M = 4f
        private const val SHOP_INSET_M = 0.4f
        private const val SHOP_RISER_M = 0.45f
        private const val SHOP_GLASS_M = 2.6f
        private const val SHOP_TOP_M = 3.5f
        private const val SHOP_TEXT_M = 0.7f
        private const val SHOP_TEXT_MIN_M = 0.22f
        private const val SHOP_TEXT_PAD_M = 0.4f
        /** A letter's width for its height in the sign face, with its gap. */
        private const val SHOP_CHAR = 0.62f
        /** Shop names a frame writes: street signs and limits keep the rest of the frame's words. */
        private const val MAX_SHOP_SIGNS = 8
        private const val LINE_RANGE_M = 90f
        private const val LINE_DARK = 0.86f
        private const val PANEL_EVERY_M = 6f
        private const val MAX_WALL_LINES = 520
        private const val CROWN_NARROW = 0.72f
        private const val CROWN_TALL = 1.25f
        private const val FLECK_RADIUS = 36f
        private const val FLECK_STEP_M = 2.5f
        private const val FLECK_HALF = 0.16f
        private const val FLECK_CLEAR_M = 0.45f
        private const val FLECK_LIGHT = 1.22f
        private const val FLECK_DARK = 0.8f
        private const val MAX_FLECKS = 360
        private const val PITCH_MAX_M2 = 450f
        /** How dark a wall or a roof face turned from the sun is, as a share of its colour. */
        private const val WALL_DARK = 0.58f
        private const val ROOF_DARK = 0.72f
        // The sun stands in the south-west, where an afternoon drive has it.
        private const val SUN_E = -0.6f
        private const val SUN_N = -0.8f

        private const val MAX_PIECES = 2400

        /** The picked square of heights: [G] samples a side, [G_STEP_M] apart, the car in the middle. */
        private const val G_STEP_M = 50f
        private const val G = 35
        private const val G_HALF_M = (G - 1) * G_STEP_M / 2
        /** Land is drawn in patches this many samples a side. */
        private const val MESH = 2
        /** Heights are whole metres: a square that differs by less than this is level. */
        private const val LEVEL_M = 0.5f
        /** How fast a slope goes from half lit to fully lit or dark: a 1 in 8 slope facing the sun is fully lit. */
        private const val SHADE_GAIN = 4f
        /** A road's piece is no longer than this over ground with height; a whole number of dashes ([DASH_EVERY_M]). */
        const val CUT_M = 36f
        private const val MAX_CUTS = 40
        /** A piece: its two ends, half width, class, lanes, flags, a deck's height at each end, its distance along its way at each end, and its arrows' place. */
        private const val PIECE = 13
        private const val ARROW_SLOT = 12
        // A piece's flags, its eighth float.
        private const val ONE_WAY = 1
        private const val KERBED = 2
        private const val BRIDGE = 4
        /** With [BRIDGE]: a road climbing to a deck, drawn by the bridge's pass as a bank (T-1181). */
        private const val APPROACH = 8
        /** A road below tertiary: no edge or centre line, as such streets have none (T-1189). */
        private const val UNPAINTED = 16
        /** With [ONE_WAY]: mapped oneway=-1, travelled against the piece's own direction (T-1246). */
        private const val ONE_WAY_BACK = 64
        /**
         * A sign post nearer than this ahead of the car is not drawn: the lens sits behind and above
         * the car, so a post beside or behind it is a metre or two from the lens and its plate was
         * projected across the car and the road (T-1197, seen beside a real junction's photo).
         */
        private const val PASSED_M = 3f
        // T-1167. A deck clears what it crosses by this a layer, as a road bridge does.
        private const val DECK_M = 5.5f
        /** A lone span is lifted no more than this share of its length: 1 in 7, not a hump over a culvert. */
        private const val DECK_GRADE = 0.15f
        // T-1181. A deck is level; the road to each end climbs to it by this share of its length at most, a road's own grade.
        private const val APPROACH_GRADE = 0.05f
        /** A road lifted by less than this is drawn on the ground. */
        private const val APPROACH_MIN_M = 0.05f
        /** A bank's side is the ground's colour, a little darker. */
        internal const val BANK_DARK = 0.75f
        /** A slope facing away from the sun is this much of the ground's colour; one facing it as much brighter. */
        private const val LAND_DARK = 0.7f
        private const val PIER_EVERY_M = 30f
        private const val PIER_FIRST_M = 15f
        /** A deck is planned from this far past what is drawn: its roads climb to it from a long way off. */
        private const val DECK_AHEAD_M = 300f
        private const val DECK_CUT_M = 18f
        private const val DECK_THICK_M = 1f
        /** How far off a deck's edge still counts as on it. */
        private const val DECK_EDGE_M = 0.5f
        /** A car is on a bridge that runs within 45 degrees of its heading. */
        private const val ON_DECK_COS = 0.7f
        private const val PIER_MIN_M = 2.5f
        private const val PIER_HALF_M = 0.7f
        // The deck's side is in its own shade, and a pier under it darker still, so the rail reads.
        internal const val FASCIA_DARK = 0.62f
        // T-1181: a deck this far above the car, with an end less than this far ahead of it, is not
        // drawn; an edge this far inside another deck within this height of it is inside one deck.
        private const val OVERHEAD_MIN_M = 2.5f
        private const val OVERHEAD_AHEAD_M = 3f
        private const val SHARED_DECK_M = 1.5f
        private const val SHARED_IN_M = 0.3f
        private const val PIER_DARK = 0.5f
        // T-1184. A tunnel is not drawn from outside, its mouth is; from inside, its walls, roof and lights.
        /** A piece's flag, with [BRIDGE] never set: a road in a tunnel. */
        private const val TUNNEL = 32
        internal const val PORTAL_DARK = 0.8f
        internal const val TUBE_WALL_DARK = 0.45f
        internal const val TUBE_ROOF_DARK = 0.3f
        /** The dark of a portal's opening, over its wall. */
        private const val MOUTH_DARK = 0.12f
        private const val TUNNEL_H = 5f
        /** A portal's wall stands this much wider than the road each side, and this much over the opening. */
        private const val PORTAL_WALL_M = 1.2f
        private const val PORTAL_LINTEL_M = 0.9f
        private const val MAX_PORTALS = 8
        /** The most tunnel pieces looked at for ends (a tunnel is cut like any road), and how many are drawn as a tube. */
        private const val MAX_TUNNEL_PIECES = 96
        private const val MAX_TUBE_SOLIDS = 160
        /** The tube is drawn this far round the car, in sections this long (a long wall is fogged by its nearest corner). */
        private const val TUBE_RADIUS_M = 200f
        private const val TUBE_CUT_M = 40f
        /** The roof is drawn only this far ahead of [OVERHEAD_AHEAD_M]: the camera is higher than it, and a roof farther on would hide the road. */
        private const val TUBE_ROOF_M = 10f
        /** A building is over a tunnel if a point this far apart along it lies inside the building's outline. */
        private const val OVER_STEP_M = 6f
        private const val MAX_SHIELDS = 6
        private const val SHIELD_CHARS = 8
        // T-1183: a marker stands this far after a junction, then this far apart.
        private const val SHIELD_AFTER_M = 20f
        private const val SHIELD_EVERY_M = 1000f
        private const val SHIELD_APART_M = 80f
        // Larger than life, like the blades: a number has to be read from a car's length back.
        private const val SHIELD_H = 1.3f
        // A plate's corners as shares of its half width (across, from the middle) and of SHIELD_H (up).
        private val SHAPE_INTERSTATE = floatArrayOf(-1f, 0.35f, -1f, 1f, 1f, 1f, 1f, 0.35f, 0f, 0f)
        private val SHAPE_US = floatArrayOf(-0.6f, 0f, 0.6f, 0f, 1f, 0.5f, 0.6f, 1f, -0.6f, 1f, -1f, 0.5f)
        private val SHAPE_STATE = floatArrayOf(-0.5f, 0f, 0.5f, 0f, 1f, 0.3f, 1f, 0.7f, 0.5f, 1f, -0.5f, 1f, -1f, 0.7f, -1f, 0.3f)
        private val SHAPE_PLAIN = floatArrayOf(-1f, 0f, 1f, 0f, 1f, 1f, -1f, 1f)
        /** I, or two capitals, then the number: the rest is a plain plate (the table is tests/contracts/route-shields.json). */
        private val ROUTE = Regex("(I|[A-Z]{2})[ -]?(\\d{1,4}[A-Z]?)")
        // The guide board at a link's split: how many stand, a row's longest, how far out of the road,
        // how close two alike may stand, a row's height and the board's lowest edge; and the least share
        // of a street-name blade that must face a car for its words to show.
        private const val MAX_GUIDES = 4
        private const val GUIDE_CHARS = 28
        private const val GUIDE_OUT_M = 3f
        private const val GUIDE_APART_M = 60f
        private const val GUIDE_H = 0.8f
        private const val GUIDE_LOW_M = 2.4f
        private const val BLADE_FACE_MIN = 0.3f

        /** [text] in at most [n] characters, ended at a whole word: a sign reading half a word looks broken (T-1251). One word longer than [n] is cut. */
        internal fun whole(text: String, n: Int): String {
            if (text.length <= n) return text
            val cut = text.take(n + 1).lastIndexOf(' ')
            return if (cut > 0) text.take(cut).trimEnd() else text.take(n)
        }

        /** The network a route [ref] belongs to and the label its marker carries, by the table in tests/contracts/route-shields.json (T-1183). */
        internal fun routeMark(ref: String): Pair<String, String> {
            val first = ref.substringBefore(';').trim()
            // Too long for a marker: its number if it ends in one ("County Road 7" is a 7), else its whole words.
            val m = ROUTE.matchEntire(first)
                ?: return "generic" to (first.substringAfterLast(' ').takeIf { first.length > SHIELD_CHARS && it.firstOrNull()?.isDigit() == true } ?: whole(first, SHIELD_CHARS))
            return when (m.groupValues[1]) { "I" -> "interstate"; "US" -> "us"; else -> "state" } to m.groupValues[2]
        }
        /** Two ways out of a corner within this of each other's bearing are one arm. */
        private const val ARMS = 16
        private val ARM_RAD = (2 * PI / ARMS).toFloat()
        /** Streets with a pavement: the ones through a town, not a motorway or a trunk road. */
        private val TOWN = setOf("primary", "secondary", "tertiary", "unclassified", "residential", "living_street")
        private const val KERB_RADIUS = 180f
        private const val PAVE_HALF = 1f
        private const val KERB_HALF = 0.09f
        private const val MAX_BARS = 24
        private const val BAR_HALF_M = 0.3f
        /** A sign this far off the road's line stands beside it, by its own lane. */
        private const val BAR_BESIDE_M = 1f
        /** Street ends this close to a signal are one junction. */
        private const val JUNCTION_M = 1.5f
        private const val MAX_RAILS = 60
        private const val RAIL_LOW_M = 0.25f
        private const val RAIL_TOP_M = 0.85f
        private const val MAX_BLADES = 12
        private const val BLADE_RADIUS = 120f
        private const val BLADE_CHARS = 18
        private const val BLADE_OUT_M = 2.5f
        // Larger than life, like the signs.
        private const val BLADE_M = 3.6f
        private const val BLADE_H = 0.6f
        private const val BLADE_GAP = 0.1f
        /** Half a letter's width as a share of the plate's height: the label face's advance and tracking. */
        private const val BLADE_CHAR = 0.27f
        private const val WORDS_H = 0.5f
        private const val TEXT_RADIUS = 60f
        /** Text smaller than this share of the smallest label is a smudge: left out. */
        private const val TEXT_MIN = 0.3f
        /** How far under its middle a line of capitals' baseline lies, as a share of its height. */
        private const val TEXT_DROP = 0.35f
        private const val STOP_WORD = "STOP"
        private const val LENS_HALF = 0.13f
        private const val BOLLARD_M = 0.9f
        private const val BOLLARD_HALF = 0.14f
        private const val GATE_M = 1f
        private const val GATE_HALF = 1.6f
        private const val STREET = 0
        private const val MINOR = 1
        private const val PATH = 2
        private const val LANE_M = 3.4f
        /** Half an uncounted ramp: one lane and a shoulder either side. */
        private const val RAMP_HALF_M = 3f
        private const val MINOR_HALF = 1.7f
        private const val PATH_HALF = 0.9f
        private const val EDGE_IN_M = 0.3f
        private const val LINE_HALF = 0.08f

        // T-1235: a lane's turn arrow. [ARROW_LEN_M] long, its tip [ARROW_BACK_M] before the junction end; a shaft of
        // half width [ARROW_SHAFT_HALF], a head [ARROW_HEAD_M] long and [ARROW_HEAD_HALF] either side; a turn leaves
        // the shaft [ARROW_FORK_M] along it and goes sideways at most [ARROW_BRANCH_M] (and this share of the lane).
        private const val ARROW_LEN_M = 4f
        private const val ARROW_BACK_M = 5f
        private const val ARROW_SHAFT_HALF = 0.12f
        private const val ARROW_HEAD_M = 1.2f
        private const val ARROW_HEAD_HALF = 0.45f
        private const val ARROW_FORK_M = 1.6f
        private const val ARROW_BRANCH_M = 0.6f
        private const val ARROW_BRANCH_LANE = 0.18f
        private const val DIAG = 0.70710677f
        /** The most pieces whose arrows are drawn, the nearest first. */
        private const val MAX_ARROWS = 64
        private const val TURN_LEFT = 1
        private const val TURN_THROUGH = 2
        private const val TURN_RIGHT = 4
        private const val DASH_M = 3f
        private const val DASH_EVERY_M = 9f
        private const val MAX_DASHES = 420
        private const val ZEBRA_LONG_M = 3f
        private const val ZEBRA_EVERY_M = 1f
        private const val CROSSING_SNAP_M = 12f

        private const val MAX_AREAS = 70
        private const val MAX_AREA_RING = 64
        /** At most this many of the areas are land cover (T-1188), so a town's yards leave room for its lake. */
        private const val MAX_COVER = 24
        /** The bare ground reaches this far from the car: past the haze, to the horizon's edge. */
        private const val GROUND_HALF_M = 4 * RADIUS
        private const val MAX_BAYS = 220
        private const val MAX_BAY_RING = 8
        private const val MAX_TREES = 700
        private const val MAX_LAMPS = 70
        private const val MAX_SIGNALS = 24
        private const val MAX_CROSSINGS = 30
        private const val MAX_BARRIERS = 40

        /** A live closure's kind (T-1108): the house's word, not one a map pack carries. */
        const val CLOSURE = "closure"
        private const val MAX_CLOSURES = 6
        private const val CLOSURE_HALF_M = 3.2f
        private const val CLOSURE_RAILS = 3
        private const val CLOSURE_STRIPES = 6
        private const val CLOSURE_LOW_M = 0.5f
        private const val CLOSURE_STEP_M = 0.4f
        private const val CLOSURE_RAIL_M = 0.22f
        private const val MAX_POSTS = 80

        /** A post's kind is its place in this list; the cameras come last, [CAMERA] and after. */
        private val POSTS = listOf("stop", "give_way", "speed_sign", "sign", "speed_camera", "camera", "alpr", "bollard", "gate")
        private const val STOP = 0
        private const val GIVE_WAY = 1
        private const val SPEED_SIGN = 2
        private const val SIGN = 3
        private const val SPEED_CAMERA = 4
        private const val CAMERA = 5
        private const val ALPR = 6
        private const val BOLLARD = 7
        private const val GATE = 8
        // Larger than life: a real sign is 0.75 m across, a few pixels from the chase camera.
        private const val SIGN_M = 2.9f
        private const val SIGN_HALF_M = 0.6f
        private const val SPEED_CAMERA_M = 2.6f
        private const val CAMERA_M = 5f
        private const val MAX_CONES = 10
        private const val MAX_BOARDS = 30
        private const val ROW_EVERY_M = 9f
        private const val TRUNK_M = 2.4f
        private const val TRUNK_HALF = 0.16f
        private const val POLE_HALF = 0.07f
        private const val LAMP_M = 7f
        private const val SIGNAL_M = 3.2f
        private const val BOARD_M = 1.6f
        private const val BOARD_EVERY_M = 3f
        private const val POOL_M = 4.5f
        private const val POOL_SIDES = 8
        private const val POOL_ALPHA = 46
        /** Fainter than this a pool is not worth its polygon. */
        private const val POOL_MIN = 0.05f

        // T-1164. Under this the sun throws no shadow worth drawing: it would cross the street.
        private const val SHADOW_FROM_DEG = 2.0
        /** The longest shadow, in metres a metre of height. */
        internal const val SHADOW_MAX = 3f
        private const val SHADOW_MAX_M = 60f
        private const val SHADOW_MIN_M = 0.5f
        private const val SHADOW_RADIUS = 160f
        /** A shadow in full day, of 255. Low: two neighbours' shadows overlap. */
        internal const val SHADOW_ALPHA = 84f
        private const val FOOT_M = 1.2f
        internal const val FOOT_ALPHA = 40f
        /** The car's beam: metres ahead, half its width there and its strength of 255, band by band. */
        private val BEAM = floatArrayOf(2.5f, 1.3f, 110f, 9f, 2.6f, 64f, 17f, 3.8f, 26f, 27f, 4.8f, 0f)
        internal val BEAM_INK = mix(Color.WORLD_LAMP, Color.WORLD_SIGN_PLATE, 0.5f) and 0xFFFFFF

        /**
         * Metres of shadow a metre of height throws with the sun [elevationDeg] up: none under
         * [SHADOW_FROM_DEG], longer as it sinks, and no longer than [SHADOW_MAX].
         */
        internal fun shadowReach(elevationDeg: Double): Float =
            if (elevationDeg <= SHADOW_FROM_DEG) 0f else min(SHADOW_MAX, (1.0 / tan(Math.toRadians(elevationDeg))).toFloat())

        /** A shadow's dark at [alpha] of 255: the haze's own colour, so no new one is typed. */
        internal fun ink(alpha: Float): Int = (Color.WORLD_HAZE and 0xFFFFFF) or (alpha.toInt().coerceIn(0, 255) shl 24)
        /** A lamp's head by day, as a share of its lit colour. */
        private const val LAMP_OFF = 0.4f
        /** Windows start to light once it is this dark, and this many in eight have someone in. */
        private const val LIT_FROM = 0.5f
        /** The lit side is wholly the sun's once daylight is one part in this many. */
        private const val SUN_TURN = 4f
        private const val LIT_OF_8 = 3
        /** How far full day lifts a surface towards white: the palette is a night one. */
        const val DAY_LIFT = 0.36f

        private val COS8 = FloatArray(POOL_SIDES) { kotlin.math.cos(it * 2 * Math.PI / POOL_SIDES).toFloat() }
        private val SIN8 = FloatArray(POOL_SIDES) { kotlin.math.sin(it * 2 * Math.PI / POOL_SIDES).toFloat() }

        /** OSM's and Overture's words for a building, by what it is usually built like. */
        private val HOMES = setOf(
            "house", "residential", "apartments", "detached", "semidetached_house", "terrace", "bungalow", "dormitory",
            "cabin", "farm", "static_caravan", "houseboat", "hut",
        )
        private val TRADE = setOf("commercial", "retail", "office", "hotel", "supermarket", "kiosk", "entertainment")
        private val WORKS = setOf(
            "industrial", "warehouse", "garage", "garages", "shed", "hangar", "service", "barn", "farm_auxiliary",
            "carport", "roof", "construction", "agricultural", "transportation", "outbuilding",
        )
        private val CIVIC = setOf(
            "school", "church", "hospital", "university", "public", "civic", "college", "kindergarten", "cathedral",
            "chapel", "government", "train_station", "fire_station", "mosque", "temple", "synagogue", "education",
            "medical", "religious",
        )
        /** The roofs whose ridge stops short of the walls' ends, a slope on each of the four sides. */
        private val HIPPED = setOf("hipped", "half-hipped", "pyramidal")
        private val PITCHED = setOf("gabled", "hipped", "pyramidal", "half-hipped", "gambrel", "mansard", "saltbox", "skillion")
        private val DRIVES = setOf("service", "track", "pedestrian")
        /** The street classes that are lined down the middle where the map does not count their lanes. */
        private val MARKED = setOf("motorway", "trunk", "primary", "secondary", "tertiary")

        internal fun wallFor(kind: String): Int = when (kind) {
            in HOMES -> Color.WORLD_WALL_HOME
            in TRADE -> Color.WORLD_WALL_TRADE
            in WORKS -> Color.WORLD_WALL_WORKS
            in CIVIC -> Color.WORLD_WALL_CIVIC
            else -> Color.WORLD_WALL
        }

        private val FOOD = setOf("cafe", "restaurant", "fast_food", "bar", "pub", "bakery", "butcher", "ice_cream", "deli", "confectionery", "food_court")
        private val SERVICE = setOf("bank", "pharmacy", "clinic", "doctors", "dentist", "hotel", "post_office", "library", "townhall", "veterinary", "hospital", "school", "police")
        private val FUEL = setOf("fuel", "charging_station", "car_repair", "car_wash", "car", "tyres")

        /** The colour of the board over a business, by what it trades in. */
        internal fun fasciaFor(trade: String): Int = when (trade) {
            in FOOD -> Color.WORLD_FASCIA_FOOD
            in FUEL -> Color.WORLD_FASCIA_FUEL
            in SERVICE -> Color.WORLD_FASCIA_SERVICE
            else -> Color.WORLD_FASCIA_SHOP
        }

        /** Whether ([e], [n]) is inside the ring of [n0] corners. */
        private fun inside(r: FloatArray, n0: Int, e: Float, n: Float): Boolean {
            var hit = false
            var j = n0 - 1
            for (k in 0 until n0) {
                val ye = r[k * 2 + 1]
                val yj = r[j * 2 + 1]
                if ((ye > n) != (yj > n) && e < r[k * 2] + (n - ye) * (r[j * 2] - r[k * 2]) / (yj - ye)) hit = !hit
                j = k
            }
            return hit
        }

        private fun rgb(a: Int, r: Int, g: Int, b: Int) = android.graphics.Color.argb(a, r, g, b)

        /** [color] a little lighter or darker by [seed], so a street of one kind is not one colour. */
        internal fun vary(color: Int, seed: Int): Int = shade(color, 0.9f + ((seed * 31 + 7) and 7) * 0.026f)

        /** Two whole numbers to one whose low bits are fit to read: the same answer every run. */
        internal fun hash(a: Int, b: Int): Int {
            val h = ((a * 73856093) xor (b * 19349663)).let { (it xor (it ushr 13)) * 1_540_483_477 }  // MurmurHash2's multiplier
            return h xor (h ushr 15)
        }

        /** [color] in daylight [day] (0 to 1): each channel lifted towards white, alpha kept. */
        internal fun daylit(color: Int, day: Float): Int {
            val k = day * DAY_LIFT
            val r = (color shr 16) and 0xFF
            val g = (color shr 8) and 0xFF
            val b = color and 0xFF
            return rgb(color ushr 24, r + ((255 - r) * k).toInt(), g + ((255 - g) * k).toInt(), b + ((255 - b) * k).toInt())
        }

        /**
         * A slope of open ground, [lit] 0 (facing away from the sun) to 1: the bare ground's own
         * colour, darker or lighter. It was a blue-grey of its own, which covered the ground sheet
         * and made a suburb read as tarmac beside its photo (T-1199).
         */
        internal fun landTone(lit: Float): Int =
            shade(Color.WORLD_GROUND, LAND_DARK + (2f - 2f * LAND_DARK) * lit)

        internal fun shade(color: Int, k: Float): Int {
            val r = min(255, (((color shr 16) and 0xFF) * k).toInt())
            val g = min(255, (((color shr 8) and 0xFF) * k).toInt())
            val b = min(255, ((color and 0xFF) * k).toInt())
            return rgb(color ushr 24, r, g, b)
        }

        /** [a] at 0 to [b] at 1, keeping [b]'s alpha. */
        internal fun mix(a: Int, b: Int, t: Float): Int {
            val r = ((a shr 16) and 0xFF) + ((((b shr 16) and 0xFF) - ((a shr 16) and 0xFF)) * t).toInt()
            val g = ((a shr 8) and 0xFF) + ((((b shr 8) and 0xFF) - ((a shr 8) and 0xFF)) * t).toInt()
            val bl = (a and 0xFF) + (((b and 0xFF) - (a and 0xFF)) * t).toInt()
            return rgb(b ushr 24, r, g, bl)
        }

        /**
         * Takes [xy]'s [n] corners (east, north) down to at most [cap] in place, removing the one
         * whose triangle with its two neighbours has the least area, over and over (T-1185): a
         * point on a straight wall goes first, then the smallest bump, and the real corners stay.
         * Returns how many are kept; the ring's turn and its first corners' order are unchanged.
         * ponytail: O(n * (n - cap)) a ring, fine for the hundreds of corners a footprint has; a
         * heap if one ever has thousands.
         */
        internal fun thin(xy: FloatArray, n: Int, cap: Int): Int {
            var m = n
            while (m > cap && m > 3) {
                var worst = 0
                var least = Float.MAX_VALUE
                for (k in 0 until m) {
                    val p = (k + m - 1) % m
                    val q = (k + 1) % m
                    val tri = abs((xy[k * 2] - xy[p * 2]) * (xy[q * 2 + 1] - xy[p * 2 + 1]) - (xy[q * 2] - xy[p * 2]) * (xy[k * 2 + 1] - xy[p * 2 + 1]))
                    if (tri < least) { least = tri; worst = k }
                }
                System.arraycopy(xy, (worst + 1) * 2, xy, worst * 2, (m - worst - 1) * 2)
                m--
            }
            return m
        }

        private fun area(r: FloatArray, n: Int): Float {
            var a = 0f
            for (k in 0 until n) {
                val j = (k + 1) % n
                a += r[k * 2] * r[j * 2 + 1] - r[j * 2] * r[k * 2 + 1]
            }
            return a
        }

        private fun toPiece(x: Float, y: Float, x0: Float, y0: Float, x1: Float, y1: Float): Float {
            val dx = x1 - x0
            val dy = y1 - y0
            val l2 = dx * dx + dy * dy
            val t = if (l2 == 0f) 0f else (((x - x0) * dx + (y - y0) * dy) / l2).coerceIn(0f, 1f)
            return hypot(x - (x0 + dx * t), y - (y0 + dy * t))
        }

        private fun hypot(a: Float, b: Float): Float = sqrt(a * a + b * b)
    }
}
