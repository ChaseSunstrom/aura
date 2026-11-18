package ai.aura.app.car.holo

import ai.aura.app.ui.theme.JarvisTokens
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan
import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.math.tan

/*
 * The hologram drive (T-0613), ported from the board's `startMap` (docs/design/v2/Car.dc.html):
 * a chase camera behind and above the car, a wireframe city in fog, the road, the route
 * ribbon with its turn chevrons, the car itself, and two street names.
 *
 * Everything here is plain Kotlin with no Android types, so it is tested on the JVM; the
 * painter only replays a [HoloFrame] onto a canvas. The numbers below are the scene's own
 * (metres, the camera's pitch, the board's alphas and stroke widths), not UI sizes: the UI
 * sizes it needs (the label clamp) come from JarvisTokens.
 */

/** A latitude and longitude in degrees. */
data class LatLon(val lat: Double, val lon: Double)

/**
 * Metres east and north of an origin, on a flat plane. Within a few kilometres of the
 * origin the error is centimetres; the scene re-anchors on every route, and a route is
 * re-requested at every turn. Does not handle the antimeridian or the poles.
 */
class GeoPlane(val lat0: Double, val lon0: Double) {
    private val perLon = M_PER_DEG * cos(lat0 * PI / 180.0)

    fun east(lon: Double): Float = ((lon - lon0) * perLon).toFloat()
    fun north(lat: Double): Float = ((lat - lat0) * M_PER_DEG).toFloat()
    fun lon(east: Float): Double = lon0 + east / perLon
    fun lat(north: Float): Double = lat0 + north / M_PER_DEG

    companion object {
        /** The mean earth radius's metres per degree. */
        const val M_PER_DEG = 6_371_008.8 * PI / 180.0
    }
}

/**
 * The ground's height over one 0.01 degree cell (T-1105): [n] by [n] whole metres above sea
 * level, rows south to north, each west to east, the first and last on the cell's edges, so
 * two neighbours agree along the edge they share. The cell's south-west corner is
 * ([cx] / [PER_DEG], [cy] / [PER_DEG]) degrees. `tests/contracts/map-pack.json` `pack.ground`.
 */
class HoloGroundCell(val cx: Int, val cy: Int, val n: Int, val h: ShortArray) {
    companion object {
        const val PER_DEG = 100

        /** The most samples a side that is read; the house writes 17. */
        const val MAX_N = 65
    }
}

/** What the route endpoint said, in the shape the scene draws. */
class HoloRoute(
    val shape: List<LatLon>,
    val street: String,
    val turnAt: LatLon? = null,
    val turnStreet: String? = null,
)

/**
 * What the map knows of how a building looks (T-1060): its wall and roof [colour]s as ARGB (0
 * is "the map does not say", which is most buildings), the roof's OSM shape, where its walls
 * start above the ground, its OSM kind ("house", "retail"; blank for a plain `yes`) and
 * whether it is one part of a building drawn in parts.
 */
class HoloLook(
    val colour: Int = 0,
    val roofColour: Int = 0,
    val roofShape: String = "",
    val minM: Float = 0f,
    val kind: String = "",
    val part: Boolean = false,
    /** The name of the business in it and its trade ("bakery", "fuel"), blank for none (T-1106). */
    val sign: String = "",
    val trade: String = "",
    /** T-1185: how tall the roof itself stands (0: not mapped), the way its slope faces in degrees from north (-1: not mapped), and `along` or `across` the longest wall for its ridge. */
    val roofM: Float = 0f,
    val roofDir: Float = -1f,
    val roofOrient: String = "",
) {
    companion object {
        val NONE = HoloLook()
    }
}

/** A footprint (closed or not), its height (`null` is "the map does not know") and its [look]. */
class HoloBuilding(val ring: List<LatLon>, val heightM: Float?, val look: HoloLook = HoloLook.NONE)

/**
 * A road or path as the map draws it, point by point, its OSM `highway` class, its lanes
 * (0: not said) and [oneway]: 1 along [line], -1 against it, 0 for both ways or not said.
 * [name] is the street's, blank where the map has none: a junction's name blades read it (T-1104).
 * T-1167: [ref] is the route number as OSM writes it (`;` between several), null where the map
 * has none; [bridge] and [tunnel] are the tags, [layer] the level (-5..5, 0 on the ground or not said).
 */
class HoloRoad(
    val line: List<LatLon>, val kind: String, val lanes: Int = 0, val oneway: Int = 0, val name: String = "",
    val ref: String? = null, val bridge: Boolean = false, val tunnel: Boolean = false, val layer: Int = 0,
    // T-1183: what a guide sign says where a link leaves (map-pack.json `roadTags`); null where the map says nothing.
    val destination: String? = null, val destinationRef: String? = null, val junctionRef: String? = null,
    val trafficSign: String? = null,
    // T-1235: what each lane may do at its end, left to right (map-pack.json `turnLanes`): [turnLanes] is the one-way
    // road's, [turnLanesForward] is for the line's end and [turnLanesBackward] for its start on a two-way road.
    val turnLanes: String? = null, val turnLanesForward: String? = null, val turnLanesBackward: String? = null,
)

/**
 * Something on or beside the street (T-1060): [kind] is one of [POINTS] (one point), [LINES]
 * (a line) or [AREAS] (a closed ring), the kinds `tests/contracts/map-pack.json` names.
 */
class HoloDetail(val kind: String, val pts: List<LatLon>, val text: String = "") {
    companion object {
        val POINTS = setOf(
            "tree", "lamp", "signal", "crossing",
            "stop", "give_way", "speed_sign", "sign", "speed_camera", "camera", "alpr", // T-1073
            "bollard", "gate", // T-1104
        )
        val LINES = setOf("tree_row", "roadworks", "barrier")

        /** What a sign says is a few characters on a plate; the house sends no more (T-1104). */
        const val TEXT_MAX = 24
        val AREAS = setOf("parking", "bay", "construction", "water", "green", "wood", "field", "yard", "paved") // field, yard, paved: T-1188

        /** [pts] as a detail of [kind], or `null` for a kind this app does not draw or one in the wrong shape. */
        fun of(kind: String, pts: List<LatLon>, text: String = ""): HoloDetail? = when {
            kind in POINTS && pts.size == 1 -> HoloDetail(kind, pts, text.trim().take(TEXT_MAX))
            kind in LINES && pts.size >= 2 -> HoloDetail(kind, pts)
            kind in AREAS && pts.size >= 4 -> HoloDetail(kind, pts)
            else -> null
        }
    }
}

/** Which colour a drawn thing takes; the painter maps each to a token. */
object Ink {
    const val ACCENT = 0
    const val BRIGHT = 1
    const val WARN = 2
    const val DANGER = 3
    const val LIFT = 4
    /** The route's soft band and its bright line: the hologram's ACCENT and BRIGHT, a pack's own route colour (T-0871). */
    const val ROUTE = 5
    const val ROUTE_CORE = 6
}

/**
 * The board's camera: [f] is a fixed share of the width, so the same scene frames the
 * same on every head unit, and the pitch puts the horizon a fifth of the focal length
 * above the middle.
 */
class HoloCamera {
    var f = 0f; private set
    var cx = 0f; private set
    var cy = 0f; private set

    /** Board canvas pixels (drawn at 2x a 600-tall layout) to surface pixels. */
    var unit = 1f; private set
    var labelMinPx = 0f; private set
    var labelMaxPx = 0f; private set

    /**
     * [width] and [height] are the area the drive is framed in (the host's visible area),
     * centred on [centerX], [centerY]; [density] is the car's pixels per dp.
     */
    fun resize(width: Float, height: Float, density: Float, centerX: Float = width / 2, centerY: Float = height / 2) {
        f = width * FOCAL
        cx = centerX
        cy = centerY
        val scale = min(1f, height / density / CarZones.DESIGN_HEIGHT_DP)
        unit = density * scale / 2
        labelMinPx = JarvisTokens.Size.CAR_LABEL_MIN * density
        labelMaxPx = JarvisTokens.Size.CAR_LABEL_MAX * density
    }

    /** Car-frame (x right, y up, z ahead, metres) to camera space, into [out] at [at]. */
    fun view(x: Float, y: Float, z: Float, out: FloatArray, at: Int = 0) {
        val dy = y - CAM_Y
        val dz = z - CAM_Z
        out[at] = x - CAM_X
        out[at + 1] = dy * COS_P + dz * SIN_P
        out[at + 2] = dz * COS_P - dy * SIN_P
    }

    fun screenX(xc: Float, zc: Float): Float = cx + f * xc / zc
    fun screenY(yc: Float, zc: Float): Float = cy - f * yc / zc

    /** Where flat ground meets the sky: the limit of the ground as it goes away. */
    fun horizonY(): Float = cy - f * tan(PITCH)

    companion object {
        const val FOCAL = 0.42f
        /** Nothing nearer than this is drawn: a point at the lens projects to infinity. */
        const val NEAR = 0.6f
        // Higher and steeper than the board's first camera (4.6 m, -10 m, 0.2 rad): the car
        // is looked down on from behind, with the road ahead of it filling the frame (the
        // owner, 2026-11-11: "more similar to the Tesla camera angle").
        const val CAM_Y = 6.4f
        const val CAM_Z = -11.5f
        const val PITCH = 0.34f
        /**
         * Straight behind the car, which keeps to its lane [CAM_X] right of the road's line.
         * The camera stood on that line, so the car was seen from its left (the owner,
         * 2026-11-12: "slightly to the left... look at the car from the back").
         */
        const val CAM_X = 1.6f
        private val COS_P = cos(PITCH)
        private val SIN_P = sin(PITCH)

        /**
         * The camera-space distance [zc] of a point [y] metres up, as if it stood on the ground
         * under itself. The camera looks down, so height reads as nearness ([view]); things are
         * ordered by where they stand, not by how tall they are (T-1131).
         */
        fun overGround(zc: Float, y: Float): Float = zc + y * SIN_P

        /**
         * 1 up close, 0 by [FOG_END_M], falling in a straight line from an eleventh of it. It ended
         * at 228 m, then 384 m (T-1073), until the owner asked for "more render distance on the
         * map to completely fill the scene" (T-1103).
         */
        fun fog(zc: Float): Float = (1.1f - zc / (FOG_END_M / 1.1f)).coerceIn(0f, 1f)

        /** Where the fog is total: nothing further is drawn, so the world is picked out to here. */
        const val FOG_END_M = 1600f

        /**
         * Clip a camera-space segment to the near plane into [out] (x0 y0 z0 x1 y1 z1).
         * False when it is wholly behind; an end behind is moved along the segment to the
         * plane, not dropped, so a road that starts under the camera still starts at the
         * bottom of the screen.
         */
        fun clipSegment(
            ax: Float, ay: Float, az: Float,
            bx: Float, by: Float, bz: Float,
            out: FloatArray,
        ): Boolean {
            if (az < NEAR && bz < NEAR) return false
            out[0] = ax; out[1] = ay; out[2] = az
            out[3] = bx; out[4] = by; out[5] = bz
            if (az < NEAR) cut(bx, by, bz, ax, ay, az, out, 0)
            else if (bz < NEAR) cut(ax, ay, az, bx, by, bz, out, 3)
            return true
        }

        /** The point on in→out where it crosses the near plane, into [dst] at [at]. */
        private fun cut(ix: Float, iy: Float, iz: Float, ox: Float, oy: Float, oz: Float, dst: FloatArray, at: Int) {
            val k = (NEAR - iz) / (oz - iz)
            dst[at] = ix + (ox - ix) * k
            dst[at + 1] = iy + (oy - iy) * k
            dst[at + 2] = NEAR
        }

        /**
         * Sutherland–Hodgman against the near plane alone: [n] camera-space xyz triples
         * from [src] into [dst], which needs room for n + 1. Returns the count, or 0 when
         * fewer than three are left.
         */
        fun clipPolygon(src: FloatArray, n: Int, dst: FloatArray): Int {
            var count = 0
            for (i in 0 until n) {
                val a = i * 3
                val b = ((i + 1) % n) * 3
                val aIn = src[a + 2] >= NEAR
                val bIn = src[b + 2] >= NEAR
                if (aIn) {
                    dst[count * 3] = src[a]; dst[count * 3 + 1] = src[a + 1]; dst[count * 3 + 2] = src[a + 2]
                    count++
                }
                if (aIn != bIn) {
                    if (aIn) cut(src[a], src[a + 1], src[a + 2], src[b], src[b + 1], src[b + 2], dst, count * 3)
                    else cut(src[b], src[b + 1], src[b + 2], src[a], src[a + 1], src[a + 2], dst, count * 3)
                    count++
                }
            }
            return if (count < 3) 0 else count
        }
    }
}

/**
 * One frame's draw list in surface pixels, reused frame after frame: the arrays grow when
 * a frame needs more than any before it and are never shrunk or reallocated otherwise, so
 * a steady drive allocates nothing per frame.
 *
 * Lines are [LINE_STRIDE] floats (x0 y0 x1 y1 alpha width) plus an [Ink]. Polygons are runs
 * of [pts] with a fill and a stroke alpha (0 is "none"). Everything but the solids and the
 * labels is drawn additively, so its order does not matter; the labels go last, over it all.
 */
class HoloFrame {
    var horizonY = 0f

    var lineCount = 0; private set
    var lines = FloatArray(LINE_STRIDE * 512); private set
    var lineInk = IntArray(512); private set

    var ptCount = 0; private set
    var pts = FloatArray(2 * 2048); private set
    var polyCount = 0; private set
    var polyStart = IntArray(256); private set
    var polyLen = IntArray(256); private set
    var polyFill = FloatArray(256); private set
    var polyFillInk = IntArray(256); private set
    var polyStroke = FloatArray(256); private set
    var polyStrokeInk = IntArray(256); private set
    var polyWidth = FloatArray(256); private set

    var labelCount = 0; private set
    val labelText = arrayOfNulls<String>(MAX_LABELS)
    val labelX = FloatArray(MAX_LABELS)
    val labelY = FloatArray(MAX_LABELS)
    val labelPx = FloatArray(MAX_LABELS)
    val labelAlpha = FloatArray(MAX_LABELS)
    val labelInk = IntArray(MAX_LABELS)

    // What the signs say (T-1104): plain text on a plate the solid layer drew, in a world colour.
    // Each is painted straight after its plate ([solidSign], [signNext]), so whatever is painted
    // over the plate is painted over its words too (T-1120).
    var signCount = 0; private set
    val signText = arrayOfNulls<String>(MAX_SIGNS)
    val signX = FloatArray(MAX_SIGNS)
    val signY = FloatArray(MAX_SIGNS)
    val signPx = FloatArray(MAX_SIGNS)
    val signColor = IntArray(MAX_SIGNS)
    /** The next sign on the same plate, or -1. */
    val signNext = IntArray(MAX_SIGNS)

    var glowCount = 0; private set
    val glowX = FloatArray(MAX_GLOWS)
    val glowY = FloatArray(MAX_GLOWS)
    val glowR = FloatArray(MAX_GLOWS)
    val glowAlpha = FloatArray(MAX_GLOWS)
    val glowInk = IntArray(MAX_GLOWS)

    // The solid layer (T-1060): opaque polygons under all the light, drawn in [solidOrder].
    var solidCount = 0; private set
    var solidStart = IntArray(1024); private set
    var solidLen = IntArray(1024); private set
    var solidColor = IntArray(1024); private set
    var solidOrder = IntArray(1024); private set
    /** The last sign written on this solid, or -1: the head of a chain through [signNext]. */
    var solidSign = IntArray(1024); private set
    private var solidDepth = FloatArray(1024)
    private val bucket = IntArray(DEPTH_BUCKETS + 1)

    private var openStart = 0

    fun clear() {
        lineCount = 0; ptCount = 0; polyCount = 0; labelCount = 0; glowCount = 0; solidCount = 0; signCount = 0
    }

    /**
     * Closes the polygon begun with [beginPoly] as a solid of ARGB [color], [depth] metres
     * from the camera; [FLAT] for one lying on the ground, which goes under everything else
     * in the order given.
     */
    fun endSolid(color: Int, depth: Float) {
        if (solidCount == solidStart.size) {
            val n = solidStart.size * 2
            solidStart = solidStart.copyOf(n); solidLen = solidLen.copyOf(n)
            solidColor = solidColor.copyOf(n); solidDepth = solidDepth.copyOf(n)
            solidOrder = IntArray(n); solidSign = solidSign.copyOf(n)
        }
        solidSign[solidCount] = -1
        solidStart[solidCount] = openStart
        solidLen[solidCount] = ptCount - openStart
        solidColor[solidCount] = color
        solidDepth[solidCount++] = depth
    }

    /**
     * Sets [solidOrder]: the farthest first, so a nearer wall is painted over a farther one,
     * and solids at one distance in the order they were given (a wall, then its windows).
     * A counting sort over half-metre steps: no allocation, and a frame's worth in one pass.
     *
     * Does not cover two solids that pass through each other: each has one distance.
     */
    fun order() {
        bucket.fill(0)
        for (i in 0 until solidCount) bucket[slot(solidDepth[i]) + 1]++
        for (b in 1..DEPTH_BUCKETS) bucket[b] += bucket[b - 1]
        for (i in 0 until solidCount) solidOrder[bucket[slot(solidDepth[i])]++] = i
    }

    private fun slot(depth: Float): Int =
        DEPTH_BUCKETS - 1 - (depth * DEPTH_STEPS_PER_M).toInt().coerceIn(0, DEPTH_BUCKETS - 1)

    fun line(x0: Float, y0: Float, x1: Float, y1: Float, alpha: Float, width: Float, ink: Int) {
        if (lineCount == lineInk.size) {
            lines = lines.copyOf(lines.size * 2)
            lineInk = lineInk.copyOf(lineInk.size * 2)
        }
        val o = lineCount * LINE_STRIDE
        lines[o] = x0; lines[o + 1] = y0; lines[o + 2] = x1; lines[o + 3] = y1
        lines[o + 4] = alpha; lines[o + 5] = width
        lineInk[lineCount++] = ink
    }

    fun beginPoly() {
        openStart = ptCount
    }

    fun point(x: Float, y: Float) {
        if (ptCount * 2 + 2 > pts.size) pts = pts.copyOf(pts.size * 2)
        pts[ptCount * 2] = x
        pts[ptCount * 2 + 1] = y
        ptCount++
    }

    fun endPoly(fill: Float, fillInk: Int, stroke: Float, strokeInk: Int, width: Float) {
        if (polyCount == polyStart.size) {
            val n = polyStart.size * 2
            polyStart = polyStart.copyOf(n); polyLen = polyLen.copyOf(n)
            polyFill = polyFill.copyOf(n); polyFillInk = polyFillInk.copyOf(n)
            polyStroke = polyStroke.copyOf(n); polyStrokeInk = polyStrokeInk.copyOf(n)
            polyWidth = polyWidth.copyOf(n)
        }
        polyStart[polyCount] = openStart
        polyLen[polyCount] = ptCount - openStart
        polyFill[polyCount] = fill; polyFillInk[polyCount] = fillInk
        polyStroke[polyCount] = stroke; polyStrokeInk[polyCount] = strokeInk
        polyWidth[polyCount] = width
        polyCount++
    }

    fun label(text: String, x: Float, y: Float, px: Float, alpha: Float, ink: Int) {
        if (labelCount == MAX_LABELS) return
        labelText[labelCount] = text
        labelX[labelCount] = x; labelY[labelCount] = y
        labelPx[labelCount] = px; labelAlpha[labelCount] = alpha
        labelInk[labelCount++] = ink
    }

    /**
     * [text] centred on [x] with its baseline at [y], [px] tall, in [color], on the solid closed
     * last (its plate); past [MAX_SIGNS], or with no solid to be on, it is left out.
     *
     * Does not cut the words where something nearer covers half the plate any better than the
     * plate is cut: both are painted whole, then painted over.
     */
    fun sign(text: String, x: Float, y: Float, px: Float, color: Int) {
        if (signCount == MAX_SIGNS || solidCount == 0) return
        signNext[signCount] = solidSign[solidCount - 1]
        solidSign[solidCount - 1] = signCount
        signText[signCount] = text
        signX[signCount] = x; signY[signCount] = y
        signPx[signCount] = px
        signColor[signCount++] = color
    }

    /** A radial glow fading from [alpha] at the centre to nothing at [r]. */
    fun glow(x: Float, y: Float, r: Float, alpha: Float, ink: Int) {
        if (glowCount == MAX_GLOWS) return
        glowX[glowCount] = x; glowY[glowCount] = y; glowR[glowCount] = r
        glowAlpha[glowCount] = alpha
        glowInk[glowCount++] = ink
    }

    companion object {
        const val LINE_STRIDE = 6
        /** The current street and the next turn: the only names on the drive. */
        const val MAX_LABELS = 2
        /** Signs near enough to read at once; more would be a wall of words. */
        const val MAX_SIGNS = 24
        /** The car's glow and its two tail lights. */
        const val MAX_GLOWS = 3
        /** A solid on the ground: farther than anything standing, so under it. */
        const val FLAT = Float.MAX_VALUE
        // Out to the fog's end and a little past (T-1185): beyond 450 m, as it was, every far solid
        // shared one step and kept the order it was given, a far tower painted over a near one.
        private const val DEPTH_BUCKETS = 3300
        private const val DEPTH_STEPS_PER_M = 2f
    }
}

/**
 * The drive around a pose: the route, the buildings near it and the car, built into a
 * [HoloFrame] each frame.
 *
 * Only the route is known about roads: the endpoint returns the route's shape and nothing
 * else, so the road drawn is the road the route takes, and no cross street is drawn. Other
 * vehicles are never drawn: no source reports them, and a made-up car on a real road is a
 * lie a driver might brake for.
 */
class HoloScene {
    /** The street the car is on, for when there is no route (the map match's). */
    var street: String = ""

    /** +1 when the next turn is to the right, -1 left, 0 with no turn. */
    var turnSign = 0; private set

    private var plane: GeoPlane? = null

    // The route in the plane: vertices and the distance along it to each.
    private var re = FloatArray(0)
    private var rn = FloatArray(0)
    private var rs = FloatArray(0)
    private var rCount = 0
    private var turnS = -1f
    private var turnE = 0f
    private var turnN = 0f
    private var turnStreet: String? = null

    private var buildings: List<HoloBuilding> = emptyList()
    private var selCount = 0
    private val selRing = Array(MAX_BUILDINGS) { FloatArray(2 * 8) }
    private val selLen = IntArray(MAX_BUILDINGS)
    private val selH = FloatArray(MAX_BUILDINGS)
    private val selCcw = BooleanArray(MAX_BUILDINGS)
    private var selE = Float.NaN
    private var selN = Float.NaN

    /**
     * The place drawn solid, in its own shapes and colours (T-1060), when [real]: under every
     * look, the space drive's too, which is the owner's and only changes the sky and the light.
     * Off is the board's wireframe city, kept as the way back until the solid one has been
     * driven; nothing in the app turns it off.
     */
    val world = HoloWorld()
    var real = true
        set(value) {
            if (field != value) selE = Float.NaN
            field = value
        }

    private var roads: List<HoloRoad> = emptyList()

    // What the flat map (T-1252) reads: the same lists the setters took, and a count that
    // moves with each, so it redraws its shapes only when the map under it changed.
    internal var stamp = 0; private set
    internal var route: HoloRoute? = null; private set
    internal var details: List<HoloDetail> = emptyList(); private set
    internal var live: List<HoloDetail> = emptyList(); private set
    internal val roadList: List<HoloRoad> get() = roads
    internal val buildingList: List<HoloBuilding> get() = buildings
    /** The road pieces near the car, [PIECE] floats each: both ends, half the width, the alpha. */
    private var pieces = FloatArray(0)
    private var pieceCount = 0

    // Per frame.
    private lateinit var frame: HoloFrame
    private lateinit var cam: HoloCamera
    private var pe = 0f
    private var pn = 0f
    private var sinH = 0f
    private var cosH = 1f
    private var carS = 0f

    // The chase: carried from frame to frame, so the camera follows the car instead of being
    // bolted to it. All of it snaps on a first frame or after a gap ([CHASE_SNAP_MS]).
    private var chaseMs = Long.MIN_VALUE

    /**
     * The wall clock, in UTC milliseconds, that lights the scene (T-1107); 0 for none, and
     * the scene is the night it was before it had one. UTC and the car's own place, so the
     * phone's time zone and summer time do not enter; a phone with the wrong time has the wrong sky.
     */
    var utcMs = 0L
    private var sunMs = 0L
    /** 0 in the dark to 1 in full day, as the last frame was lit. */
    val daylight: Float get() = world.day
    private var camH = 0f
    private var prevH = 0f
    private var prevE = 0f
    private var prevN = 0f
    private var yawRate = 0f
    private var speed = 0f
    private var accel = 0f
    private var bodyYaw = 0f
    private var steer = 0f
    private var bodySin = 0f
    private var bodyCos = 1f
    private var bodyOn = false

    /** The heading the camera looks along, degrees from north in [0, 360). */
    val camHeadingDeg: Float get() = ((camH * 180f / PI.toFloat()) % 360f + 360f) % 360f

    /** How far the car is seen turned from the camera's heading, right positive. */
    val bodyYawDeg: Float get() = bodyYaw * 180f / PI.toFloat()

    /** The front wheels' angle, right positive, at most [MAX_STEER_DEG] either way. */
    val steerDeg: Float get() = steer * 180f / PI.toFloat()

    /** How far the wheels have rolled, radians in one turn. */
    var rollRad = 0f; private set

    /** 0 on a straight road to 1 in a turn: how strongly the turn's line is drawn. */
    var turnLine = 0f; private set

    /** 0 holding speed to 1 braking hard: how much brighter the tail lights are. */
    var brake = 0f; private set

    /**
     * How far the light left behind the rear wheels reaches, metres (T-0999): what the car
     * covers in [TRAIL_S], nothing below walking pace, [TRAIL_MAX_M] at most. From the smoothed
     * speed, so a pose that jumps lengthens it for a moment.
     */
    val trailM: Float get() = if (speed < MOVING_MPS) 0f else (speed * TRAIL_S).coerceAtMost(TRAIL_MAX_M)

    // Scratch, grown on demand and reused.
    private val va = FloatArray(3)
    private val vb = FloatArray(3)
    private val seg = FloatArray(6)
    private var polyIn = FloatArray(3 * 64)
    private var polyCam = FloatArray(3 * 64)
    private var polyOut = FloatArray(3 * 65)
    private var stripRight = FloatArray(2 * 64)
    private var ptE = 0f
    private var ptN = 0f
    private val wheelSin = FloatArray(WHEEL_SIDES) { sin(it * 2 * PI.toFloat() / WHEEL_SIDES) }
    private val wheelCos = FloatArray(WHEEL_SIDES) { cos(it * 2 * PI.toFloat() / WHEEL_SIDES) }

    /** A new route replaces the old one whole, ribbon and all; `null` clears it. */
    fun setRoute(route: HoloRoute?) {
        this.route = route
        stamp++
        rCount = 0
        turnS = -1f
        turnSign = 0
        turnStreet = null
        if (route == null || route.shape.size < 2) return
        street = route.street
        val p = GeoPlane(route.shape[0].lat, route.shape[0].lon)
        plane = p
        selE = Float.NaN
        val n = route.shape.size
        if (re.size < n) {
            re = FloatArray(n); rn = FloatArray(n); rs = FloatArray(n)
        }
        for (pt in route.shape) {
            val e = p.east(pt.lon)
            val no = p.north(pt.lat)
            if (rCount > 0) {
                val d = hypot(e - re[rCount - 1], no - rn[rCount - 1])
                // A repeated point is a zero-length segment with no direction.
                if (d < MIN_SEGMENT_M) continue
                rs[rCount] = rs[rCount - 1] + d
            } else {
                rs[0] = 0f
            }
            re[rCount] = e
            rn[rCount] = no
            rCount++
        }
        if (rCount < 2) {
            rCount = 0
            return
        }
        val at = route.turnAt ?: return
        val te = p.east(at.lon)
        val tn = p.north(at.lat)
        var best = Float.MAX_VALUE
        var bi = -1
        for (i in 0 until rCount) {
            val d = hypot(re[i] - te, rn[i] - tn)
            if (d < best) { best = d; bi = i }
        }
        // The last vertex is the destination, not a turn.
        if (bi < 0 || bi == rCount - 1) return
        turnS = rs[bi]
        turnE = re[bi]
        turnN = rn[bi]
        turnStreet = route.turnStreet
        pointAt(turnS - TURN_PROBE_M, 0f)
        val inE = turnE - ptE
        val inN = turnN - ptN
        pointAt(turnS + TURN_PROBE_M, 0f)
        val outE = ptE - turnE
        val outN = ptN - turnN
        // ponytail: a slight or straight-on maneuver takes the side it leans to; a U-turn's
        // side is a coin toss. The instruction text says which, if that ever matters.
        turnSign = if (inE * outN - inN * outE > 0f) -1 else 1
    }

    /** Footprints near the drive; picked over again every [RESELECT_M] the car moves. */
    fun setBuildings(list: List<HoloBuilding>) {
        buildings = list
        stamp++
        world.setBuildings(list)
        selE = Float.NaN
    }

    /** What stands beside the streets (T-1060); picked over again with the buildings. */
    fun setDetails(list: List<HoloDetail>) {
        world.setDetails(list)
        details = list
        stamp++
        selE = Float.NaN
    }

    /** Roadworks and closures under way today (T-1108), from the house; empty when it says none. */
    fun setLive(list: List<HoloDetail>) {
        world.setLive(list)
        live = list
        stamp++
        selE = Float.NaN
    }

    /** The ground's heights near the drive (T-1105); with none the place is drawn flat. */
    fun setGround(list: List<HoloGroundCell>) {
        world.setGround(list)
        selE = Float.NaN
    }

    /** The roads and paths near the drive; picked over again with the buildings. */
    fun setRoads(list: List<HoloRoad>) {
        roads = list
        stamp++
        world.setRoads(list)
        selE = Float.NaN
    }

    /** One frame of the drive with the car at [lat], [lon] heading [headingDeg] from north. */
    fun build(frame: HoloFrame, cam: HoloCamera, lat: Double, lon: Double, headingDeg: Float, timeMs: Long) {
        this.frame = frame
        this.cam = cam
        frame.clear()
        var p = plane ?: GeoPlane(lat, lon).also { plane = it }
        pe = p.east(lon)
        pn = p.north(lat)
        // With no route to re-anchor on, a long drive would leave the flat plane's origin far
        // behind, and the plane bends with distance from it.
        if (rCount < 2 && hypot(pe, pn) > REANCHOR_M) {
            p = GeoPlane(lat, lon).also { plane = it }
            pe = 0f
            pn = 0f
            selE = Float.NaN
            chaseMs = Long.MIN_VALUE // the plane moved under the car: no speed can be read off it
        }
        chase(headingDeg * PI.toFloat() / 180f, timeMs)
        sinH = sin(camH)
        cosH = cos(camH)
        frame.horizonY = cam.horizonY()
        val t = timeMs.toFloat()
        // The sun moves a quarter of a degree a minute: once a minute is as often as it shows.
        if (utcMs == 0L) {
            if (sunMs != 0L) { world.light(null); sunMs = 0L }
        } else if (sunMs == 0L || abs(utcMs - sunMs) >= SUN_EVERY_MS) {
            val sun = HoloLight.sun(utcMs, lat, lon)
            world.light(sun.elevationDeg, sun.azimuthDeg)
            sunMs = utcMs
        }

        if (selE.isNaN() || hypot(pe - selE, pn - selN) > RESELECT_M) {
            if (real) {
                selE = pe
                selN = pn
                world.select(p, pe, pn)
            } else {
                select(p)
                selectRoads(p)
            }
        }
        // The real streets replace the board's grid and its one road; with no map of the
        // place (no pack, no house) the board's are all there is to show.
        val paved = real && world.paved
        if (real) world.build(frame, cam, pe, pn, sinH, cosH)
        if (!paved) grid()
        if (!real) roadNet()
        if (rCount >= 2) {
            locate()
            if (!paved) road()
            ribbon(t)
        }
        if (!real) {
            val scanH = (t * 0.01f) % SCAN_PERIOD_M
            for (i in 0 until selCount) building(i, scanH)
        }
        labels()
        car(t)
        frame.order()
    }

    /**
     * One step of the chase to the car heading [h] (radians) at [timeMs]. The camera's
     * heading eases after the car's, so in a turn the car is seen to turn and the world
     * swings round after it; the turn rate, the speed and its change are read off successive
     * poses and smoothed, because a pose carries neither.
     *
     * Does not cover reversing (the car is taken to move the way it points) or a pose that
     * jumps: a jump reads as speed for a moment, which only spins the wheels.
     */
    private fun chase(h: Float, timeMs: Long) {
        val dtMs = timeMs - chaseMs
        if (chaseMs == Long.MIN_VALUE || dtMs <= 0L || dtMs > CHASE_SNAP_MS) {
            camH = h
            yawRate = 0f
            speed = 0f
            accel = 0f
        } else {
            val dt = dtMs / 1000f
            val k = 1f - exp(-dt / RATE_TAU_S)
            val v = hypot(pe - prevE, pn - prevN) / dt
            val was = speed
            speed += (v - speed) * k
            accel += ((speed - was) / dt - accel) * k
            // A standing phone's heading wanders; it is not the car turning.
            val turning = if (v >= MOVING_MPS) turn(h - prevH) / dt else 0f
            yawRate += (turning - yawRate) * k
            rollRad = (rollRad + v * dt / WHEEL_R) % (2f * PI.toFloat())
            camH += turn(h - camH) * (1f - exp(-dt / CAM_TAU_S))
        }
        chaseMs = timeMs
        prevH = h
        prevE = pe
        prevN = pn
        bodyYaw = turn(h - camH).coerceIn(-MAX_BODY_YAW, MAX_BODY_YAW)
        bodySin = sin(bodyYaw)
        bodyCos = cos(bodyYaw)
        val maxSteer = MAX_STEER_DEG * PI.toFloat() / 180f
        steer = if (speed < MOVING_MPS) 0f else atan(WHEELBASE * yawRate / speed).coerceIn(-maxSteer, maxSteer)
        turnLine = if (speed < MOVING_MPS) 0f else ((abs(yawRate) - TURN_LINE_FROM) / TURN_LINE_OVER).coerceIn(0f, 1f)
        brake = ((-accel - BRAKE_FROM) / BRAKE_OVER).coerceIn(0f, 1f)
    }

    // --- what is drawn -----------------------------------------------------------------

    private fun grid() {
        val ge = pe + GRID_AHEAD * sinH
        val gn = pn + GRID_AHEAD * cosH
        var e = floor((ge - GRID_HALF) / GRID_M) * GRID_M
        while (e <= ge + GRID_HALF) {
            worldLine(e, gn - GRID_HALF, e, gn + GRID_HALF, 0f, 0.06f, 1.5f, Ink.ACCENT)
            e += GRID_M
        }
        var n = floor((gn - GRID_HALF) / GRID_M) * GRID_M
        while (n <= gn + GRID_HALF) {
            worldLine(ge - GRID_HALF, n, ge + GRID_HALF, n, 0f, 0.07f, 1.5f, Ink.ACCENT)
            n += GRID_M
        }
    }

    /** Where the car is along the route: the nearest point on any segment. */
    private fun locate() {
        var best = Float.MAX_VALUE
        for (i in 0 until rCount - 1) {
            val de = re[i + 1] - re[i]
            val dn = rn[i + 1] - rn[i]
            val len = rs[i + 1] - rs[i]
            val k = (((pe - re[i]) * de + (pn - rn[i]) * dn) / (len * len)).coerceIn(0f, 1f)
            val d = hypot(re[i] + de * k - pe, rn[i] + dn * k - pn)
            if (d < best) {
                best = d
                carS = rs[i] + len * k
            }
        }
    }

    private fun road() {
        val s0 = carS - BEHIND_M
        val s1 = carS + AHEAD_M
        // The road's surface: the left edge out, the right edge back, one polygon.
        var left = 0
        var right = 0
        walk(s0, s1, -ROADW) { e, n -> left = putCar(polyIn(left + 1), left, e, n) }
        walk(s0, s1, ROADW) { e, n ->
            if (stripRight.size < (right + 1) * 2) stripRight = stripRight.copyOf(stripRight.size * 2)
            stripRight[right * 2] = e
            stripRight[right * 2 + 1] = n
            right++
        }
        var n = left
        val buf = polyIn(left + right)
        for (i in right - 1 downTo 0) n = putCar(buf, n, stripRight[i * 2], stripRight[i * 2 + 1])
        poly(n, 0.035f, Ink.ACCENT, 0f, Ink.ACCENT, 0f)

        // Its edges, as lines so each fades with its own distance.
        for (i in 1 until left) carLine(polyIn, i - 1, i, 0.55f, 3f, Ink.ACCENT)
        for (i in left + 1 until n) carLine(polyIn, i - 1, i, 0.55f, 3f, Ink.ACCENT)

        // Lane dashes, anchored to the road so they stream past as it does.
        val end = rs[rCount - 1]
        var s = ceil(s0 / DASH_EVERY) * DASH_EVERY
        while (s < s1 && s < end) {
            val e1 = min(s + DASH_M, end)
            for (side in 0..1) {
                val off = if (side == 0) -DASH_X else DASH_X
                pointAt(s, off)
                val ae = ptE
                val an = ptN
                pointAt(e1, off)
                worldLine(ae, an, ptE, ptN, 0f, 0.55f, 2.4f, Ink.BRIGHT)
            }
            s += DASH_EVERY
        }
    }

    private fun ribbon(t: Float) {
        val end = if (turnS > carS) min(turnS + RIBBON_PAST_TURN, carS + AHEAD_M) else carS + AHEAD_M
        var count = 0
        // Over ground with height the route's long straights are cut as the roads are
        // (HoloWorld.CUT_M), or the ribbon would run through a rise the road climbs.
        var le = Float.NaN
        var ln = 0f
        walk(carS + RIBBON_START, end, LANE_X) { e, n ->
            // The way the route runs here: over a bridge it lies on the deck, under one on the ground (T-1167).
            val he = if (le.isNaN()) sinH else e - le
            val hn = if (le.isNaN()) cosH else n - ln
            if (real && world.raised && !le.isNaN()) {
                val parts = min(RIBBON_MAX_CUTS, ceil(hypot(e - le, n - ln) / HoloWorld.CUT_M).toInt())
                for (c in 1 until parts) count = putCar(polyIn(count + 1), count, le + (e - le) * c / parts, ln + (n - ln) * c / parts, he, hn)
            }
            count = putCar(polyIn(count + 1), count, e, n, he, hn)
            le = e
            ln = n
        }
        // The board's two passes: a wide soft band, then the bright line on it. Not fogged:
        // the way to go is the one thing that should not fade.
        for (pass in 0..1) for (i in 1 until count) {
            if (pass == 0) carLine(polyIn, i - 1, i, 0.22f, 46f, Ink.ROUTE, fogged = false)
            else carLine(polyIn, i - 1, i, 0.95f, 6f, Ink.ROUTE_CORE, fogged = false)
        }

        if (turnS < carS || turnSign == 0) return
        val tx = carX(turnE - pe, turnN - pn, sinH, cosH)
        val tz = carZ(turnE - pe, turnN - pn, sinH, cosH)
        if (tz <= 2f) return
        val bob = sin(t * 0.004f) * 0.4f
        val y0 = CHEVRON_Y + bob + rise(turnE, turnN)
        for (c in 0..2) {
            val x0 = tx + turnSign * (1f + c * 2.2f)
            val tip = x0 + turnSign * 1.4f
            val alpha = 0.95f - c * 0.25f
            line(x0, y0 + 1.4f, tz, tip, y0, tz, alpha, 7f, Ink.WARN)
            line(tip, y0, tz, x0, y0 - 1.4f, tz, alpha, 7f, Ink.WARN)
        }
    }

    private fun labels() {
        var tz = Float.NaN
        if (turnS >= carS && turnStreet != null && rCount >= 2) {
            tz = carZ(turnE - pe, turnN - pn, sinH, cosH)
        }
        // The street ahead on the left, short of the turn so it reads as this street.
        val sz = if (tz.isNaN() || tz <= 0f) STREET_AHEAD else (tz - STREET_CLEAR).coerceIn(STREET_MIN, STREET_AHEAD)
        if (street.isNotBlank()) label(street, -ROADW - 2f, LABEL_Y + riseCar(-ROADW - 2f, sz), sz, 1.1f, Ink.BRIGHT)
        val next = turnStreet
        if (!tz.isNaN() && tz > TURN_LABEL_MIN_Z && !next.isNullOrBlank()) {
            pointAt(turnS + TURN_LABEL_ALONG, 0f)
            val de = ptE - pe
            val dn = ptN - pn
            label(next, carX(de, dn, sinH, cosH), LABEL_Y + rise(ptE, ptN), carZ(de, dn, sinH, cosH), 1.4f, Ink.WARN)
        }
    }

    private fun label(text: String, x: Float, y: Float, z: Float, size: Float, ink: Int) {
        cam.view(x, y, z, va)
        if (va[2] < LABEL_NEAR) return
        val alpha = HoloCamera.fog(va[2])
        if (alpha < LABEL_FOG_MIN) return
        val px = (size * cam.f / va[2]).coerceIn(cam.labelMinPx, cam.labelMaxPx)
        frame.label(text, cam.screenX(va[0], va[2]), cam.screenY(va[1], va[2]), px, alpha, ink)
    }

    /**
     * Picks the nearest [MAX_BUILDINGS] footprints and moves them into the plane. Runs
     * every [RESELECT_M], not per frame: it sorts and may allocate.
     */
    private fun select(p: GeoPlane) {
        selE = pe
        selN = pn
        selCount = 0
        if (buildings.isEmpty()) return
        val dist = FloatArray(buildings.size) { i ->
            val first = buildings[i].ring.firstOrNull()
            if (first == null) Float.MAX_VALUE else hypot(p.east(first.lon) - pe, p.north(first.lat) - pn)
        }
        for (i in buildings.indices.sortedBy { dist[it] }) {
            if (selCount == MAX_BUILDINGS || dist[i] > SELECT_RADIUS) break
            val b = buildings[i]
            var n = b.ring.size
            // A closed ring repeats its first point.
            if (n > 1 && b.ring[0] == b.ring[n - 1]) n--
            if (n < 3) continue
            // ponytail: a footprint with more corners than MAX_RING is thinned by stride;
            // simplify it properly if detailed ones look wrong.
            val stride = (n + MAX_RING - 1) / MAX_RING
            val kept = (n + stride - 1) / stride
            if (selRing[selCount].size < kept * 2) selRing[selCount] = FloatArray(kept * 2)
            val ring = selRing[selCount]
            var area = 0f
            for (k in 0 until kept) {
                val pt = b.ring[k * stride]
                ring[k * 2] = p.east(pt.lon)
                ring[k * 2 + 1] = p.north(pt.lat)
            }
            for (k in 0 until kept) {
                val j = (k + 1) % kept
                area += ring[k * 2] * ring[j * 2 + 1] - ring[j * 2] * ring[k * 2 + 1]
            }
            if (area == 0f) continue
            selLen[selCount] = kept
            selCcw[selCount] = area > 0f
            selH[selCount] = (b.heightM ?: DEFAULT_HEIGHT_M).coerceIn(MIN_HEIGHT_M, MAX_HEIGHT_M)
            selCount++
        }
    }

    /**
     * Every piece of road within [SELECT_RADIUS] of the car, the nearest [MAX_PIECES], moved
     * into the plane. By the distance to the piece, not its ends: a country road's nodes can be
     * kilometres apart. Runs with [select], not per frame: it sorts and allocates.
     */
    private fun selectRoads(p: GeoPlane) {
        pieceCount = 0
        if (roads.isEmpty()) return
        val near = ArrayList<FloatArray>()
        for (r in roads) {
            val half = halfWidth(r.kind)
            val alpha = if (half > 0f) STREET_ALPHA else if (r.kind == "service") SERVICE_ALPHA else PATH_ALPHA
            for (k in 1 until r.line.size) {
                val e0 = p.east(r.line[k - 1].lon)
                val n0 = p.north(r.line[k - 1].lat)
                val e1 = p.east(r.line[k].lon)
                val n1 = p.north(r.line[k].lat)
                val d = toPiece(pe, pn, e0, n0, e1, n1)
                if (d <= SELECT_RADIUS) near += floatArrayOf(e0, n0, e1, n1, half, alpha, d)
            }
        }
        near.sortBy { it[PIECE] }
        pieceCount = min(near.size, MAX_PIECES)
        if (pieces.size < pieceCount * PIECE) pieces = FloatArray(pieceCount * PIECE)
        for (i in 0 until pieceCount) near[i].copyInto(pieces, i * PIECE, 0, PIECE)
    }

    /** The roads on the ground: a street as its two edges, a driveway or a path as one line. */
    private fun roadNet() {
        for (i in 0 until pieceCount) {
            val o = i * PIECE
            val e0 = pieces[o]; val n0 = pieces[o + 1]
            val e1 = pieces[o + 2]; val n1 = pieces[o + 3]
            val half = pieces[o + 4]
            val alpha = pieces[o + 5]
            if (half == 0f) {
                worldLine(e0, n0, e1, n1, 0f, alpha, PATH_WIDTH, Ink.ACCENT)
                continue
            }
            val len = hypot(e1 - e0, n1 - n0)
            if (len < MIN_SEGMENT_M) continue
            val ox = (n1 - n0) / len * half
            val oy = -(e1 - e0) / len * half
            worldLine(e0 + ox, n0 + oy, e1 + ox, n1 + oy, 0f, alpha, STREET_WIDTH, Ink.ACCENT)
            worldLine(e0 - ox, n0 - oy, e1 - ox, n1 - oy, 0f, alpha, STREET_WIDTH, Ink.ACCENT)
        }
    }

    private fun building(i: Int, scanH: Float) {
        val ring = selRing[i]
        val n = selLen[i]
        val h = selH[i]
        // The nearest corner sets the fog for the whole building, as on the board.
        var nearest = Float.MAX_VALUE
        for (k in 0 until n) {
            cam.view(carX(ring[k * 2] - pe, ring[k * 2 + 1] - pn, sinH, cosH), 0f,
                carZ(ring[k * 2] - pe, ring[k * 2 + 1] - pn, sinH, cosH), va)
            nearest = min(nearest, va[2])
        }
        val fg = HoloCamera.fog(max(HoloCamera.NEAR, nearest))
        if (fg <= 0f) return
        // A building wholly behind the camera: everything would be clipped away anyway.
        var anyAhead = false
        for (k in 0 until n) {
            cam.view(carX(ring[k * 2] - pe, ring[k * 2 + 1] - pn, sinH, cosH), h,
                carZ(ring[k * 2] - pe, ring[k * 2 + 1] - pn, sinH, cosH), va)
            if (va[2] >= HoloCamera.NEAR) { anyAhead = true; break }
        }
        if (!anyAhead && nearest < HoloCamera.NEAR) return

        val camE = pe + HoloCamera.CAM_Z * sinH + HoloCamera.CAM_X * cosH
        val camN = pn + HoloCamera.CAM_Z * cosH - HoloCamera.CAM_X * sinH
        val floors = nearest < FLOOR_RANGE_M
        for (k in 0 until n) {
            val j = (k + 1) % n
            val ae = ring[k * 2]; val an = ring[k * 2 + 1]
            val be = ring[j * 2]; val bn = ring[j * 2 + 1]
            val de = be - ae
            val dn = bn - an
            // Outward is right of the edge for an anticlockwise ring, left for a clockwise.
            val ox = if (selCcw[i]) dn else -dn
            val oy = if (selCcw[i]) -de else de
            // Only walls facing the camera: a back wall would double the front one's light.
            if (ox * (camE - (ae + be) / 2) + oy * (camN - (an + bn) / 2) <= 0f) continue
            val len = hypot(de, dn)
            val facing = kotlin.math.abs((ox * sinH + oy * cosH) / len) > FACING
            var c = putWorld(polyIn(4), 0, ae, an, 0f)
            c = putWorld(polyIn, c, be, bn, 0f)
            c = putWorld(polyIn, c, be, bn, h)
            c = putWorld(polyIn, c, ae, an, h)
            poly(c, (if (facing) 0.05f else 0.08f) * fg, Ink.ACCENT, 0f, Ink.ACCENT, 0f)
            if (floors) {
                var fl = FLOOR_M
                while (fl < h) {
                    worldLine(ae, an, be, bn, fl, 0.1f * fg, 1f, Ink.ACCENT, fogged = false)
                    fl += FLOOR_M
                }
            }
        }
        var c = 0
        for (k in 0 until n) c = putWorld(polyIn(n), c, ring[k * 2], ring[k * 2 + 1], h)
        poly(c, 0.1f * fg, Ink.ACCENT, 0.7f * fg, Ink.BRIGHT, 2f)
        for (k in 0 until n) {
            val e = ring[k * 2]
            val no = ring[k * 2 + 1]
            worldEdge(e, no, h, 0.45f * fg, 1.6f)
        }
        if (scanH < h) {
            c = 0
            for (k in 0 until n) c = putWorld(polyIn(n), c, ring[k * 2], ring[k * 2 + 1], scanH)
            poly(c, 0f, Ink.ACCENT, 0.5f * fg, Ink.BRIGHT, 2f)
        }
    }

    /** The car as the board draws it, in the right-hand lane, with no made-up sway. */
    private fun car(t: Float) {
        turnArc()
        // Everything of the car is turned about its rear axle by what the camera has yet to
        // catch up with: this is what makes a turn something the car does on screen.
        bodyOn = true
        bodyView(LANE_X, 0f, 2.4f)
        frame.glow(cam.screenX(va[0], va[2]), cam.screenY(va[1], va[2]), 220f * cam.unit, 0.45f, Ink.LIFT)
        // The headlights' reach on the road, a faint wedge from the nose.
        var b = put(polyIn(4), 0, LANE_X - 0.8f, 0.02f, 4.6f)
        b = put(polyIn, b, LANE_X + 0.8f, 0.02f, 4.6f)
        b = put(polyIn, b, LANE_X + 3.4f, 0.02f, 4.6f + BEAM_M)
        b = put(polyIn, b, LANE_X - 3.4f, 0.02f, 4.6f + BEAM_M)
        poly(b, 0.07f, Ink.BRIGHT, 0f, Ink.BRIGHT, 0f)
        box(-1f, 1f, 0.35f, 1.05f, 0f, 4.6f, 0.05f)
        box(-0.85f, 0.85f, 1.05f, 1.75f, 0.9f, 3.2f, 0.18f)
        for (wx in 0..1) for (wz in 0..1) {
            val x = LANE_X + if (wx == 0) -1f else 1f
            val z = if (wz == 0) 0.8f else 3.8f
            // The front pair turns with the steering; all four roll.
            val ws = if (wz == 1) sin(steer) else 0f
            val wc = if (wz == 1) cos(steer) else 1f
            var c = 0
            val buf = polyIn(WHEEL_SIDES)
            for (k in 0 until WHEEL_SIDES) {
                val along = wheelCos[k] * WHEEL_R
                c = put(buf, c, x + along * ws, WHEEL_R + wheelSin[k] * WHEEL_R, z + along * wc)
            }
            poly(c, 0f, Ink.BRIGHT, 0.75f, Ink.BRIGHT, 1.8f)
            for (spoke in 0..1) {
                val a = rollRad + spoke * PI.toFloat() / 2
                val along = cos(a) * WHEEL_R
                val up = sin(a) * WHEEL_R
                bodyLine(
                    x + along * ws, WHEEL_R + up, z + along * wc,
                    x - along * ws, WHEEL_R - up, z - along * wc, 0.55f, 1.2f, Ink.BRIGHT,
                )
            }
        }
        // The tail: two lights and the bar between them, brighter while the car slows.
        val lit = 0.6f + 0.4f * brake
        for (side in 0..1) {
            bodyView(LANE_X + if (side == 0) -0.75f else 0.75f, 0.85f, -0.02f)
            frame.glow(cam.screenX(va[0], va[2]), cam.screenY(va[1], va[2]), (34f + 22f * brake) * cam.unit, lit, Ink.DANGER)
        }
        bodyLine(LANE_X - 0.75f, 0.85f, -0.02f, LANE_X + 0.75f, 0.85f, -0.02f, 0.35f + 0.6f * brake, 1.6f + 1.4f * brake, Ink.DANGER)
        // T-0999. Dashes of light on the road behind each rear wheel: longer the faster the car
        // goes, fading away from it, and sliding back as the wheels roll.
        val trail = trailM
        if (trail > 0f) {
            val slid = rollRad * WHEEL_R % TRAIL_GAP_M
            for (side in 0..1) {
                val x = LANE_X + if (side == 0) -1f else 1f
                var near = slid
                while (near < trail) {
                    val far = (near + TRAIL_GAP_M * 0.6f).coerceAtMost(trail)
                    val fade = 1f - near / trail
                    bodyLine(x, 0.03f, -near, x, 0.03f, -far, 0.55f * fade, 1f + 1.4f * fade, Ink.ACCENT)
                    near += TRAIL_GAP_M
                }
            }
        }
        val sb = (t * 0.002f) % 2.2f
        var c = put(polyIn(4), 0, LANE_X - 1f, sb, 0f)
        c = put(polyIn, c, LANE_X + 1f, sb, 0f)
        c = put(polyIn, c, LANE_X + 1f, sb, 4.6f)
        c = put(polyIn, c, LANE_X - 1f, sb, 4.6f)
        poly(c, 0f, Ink.BRIGHT, 0.5f, Ink.BRIGHT, 1.4f)
        bodyOn = false
    }

    /**
     * The line of the turn being made: the two tracks the front wheels are on, as arcs of the
     * turning circle from the nose, fading with distance and absent on a straight road
     * ([turnLine]). It is where the car is going by its own motion, not the route's advice.
     */
    private fun turnArc() {
        if (turnLine <= 0f) return
        val radius = speed / yawRate // signed: positive turns right
        bodyOn = true
        for (side in 0..1) {
            val off = if (side == 0) -TRACK_X else TRACK_X
            var x0 = LANE_X + off
            var z0 = AXLE_Z
            for (i in 1..ARC_STEPS) {
                val a = i * ARC_STEP_M / radius
                // A circle about the point [radius] to the side of the rear axle.
                val x1 = LANE_X + radius - (radius - off) * cos(a)
                val z1 = AXLE_Z + (radius - off) * sin(a)
                if (z1 >= NOSE_Z) {
                    val fade = turnLine * 0.8f * (1f - (i - 1f) / ARC_STEPS)
                    bodyLine(x0, 0.03f, z0, x1, 0.03f, z1, fade, 2.2f, Ink.LIFT)
                }
                x0 = x1
                z0 = z1
            }
        }
        bodyOn = false
    }

    /** A car-frame point of the car's body, turned by the chase, to camera space in [va]. */
    private fun bodyView(x: Float, y: Float, z: Float) {
        cam.view(bodyX(x, z), y, bodyZ(x, z), va)
    }

    private fun bodyX(x: Float, z: Float): Float = LANE_X + (x - LANE_X) * bodyCos + (z - AXLE_Z) * bodySin
    private fun bodyZ(x: Float, z: Float): Float = AXLE_Z - (x - LANE_X) * bodySin + (z - AXLE_Z) * bodyCos

    private fun bodyLine(
        x0: Float, y0: Float, z0: Float, x1: Float, y1: Float, z1: Float, alpha: Float, width: Float, ink: Int,
    ) = line(bodyX(x0, z0), y0, bodyZ(x0, z0), bodyX(x1, z1), y1, bodyZ(x1, z1), alpha, width, ink, fogged = false)

    private fun box(x0: Float, x1: Float, y0: Float, y1: Float, z0: Float, z1: Float, ins: Float) {
        val l = LANE_X + x0
        val r = LANE_X + x1
        // The back face, rising to the inset top.
        var c = put(polyIn(4), 0, l, y0, z0)
        c = put(polyIn, c, r, y0, z0)
        c = put(polyIn, c, r - ins, y1, z0 + ins * 1.6f)
        c = put(polyIn, c, l + ins, y1, z0 + ins * 1.6f)
        poly(c, 0.14f, Ink.BRIGHT, 0.95f, Ink.BRIGHT, 2.2f)
        c = put(polyIn, 0, l + ins, y1, z0 + ins * 1.6f)
        c = put(polyIn, c, r - ins, y1, z0 + ins * 1.6f)
        c = put(polyIn, c, r - ins, y1, z1 - ins * 0.8f)
        c = put(polyIn, c, l + ins, y1, z1 - ins * 0.8f)
        poly(c, 0.2f, Ink.BRIGHT, 0.95f, Ink.BRIGHT, 2.2f)
        bodyLine(l, y0, z0, l + ins, y1, z0 + ins * 1.6f, 0.8f, 1.8f, Ink.BRIGHT)
        bodyLine(r, y0, z0, r - ins, y1, z0 + ins * 1.6f, 0.8f, 1.8f, Ink.BRIGHT)
        bodyLine(r, y0, z1, r - ins, y1, z1 - ins * 0.8f, 0.8f, 1.8f, Ink.BRIGHT)
        bodyLine(l, y0, z1, l + ins, y1, z1 - ins * 0.8f, 0.8f, 1.8f, Ink.BRIGHT)
    }

    // --- geometry ----------------------------------------------------------------------

    /** The route at [s] metres along it, [off] metres to the right, into [ptE], [ptN]. */
    private fun pointAt(s: Float, off: Float) {
        val i = segmentAt(s)
        val len = rs[i + 1] - rs[i]
        val k = ((s - rs[i]) / len).coerceIn(0f, 1f)
        val ue = (re[i + 1] - re[i]) / len
        val un = (rn[i + 1] - rn[i]) / len
        ptE = re[i] + ue * len * k + un * off
        ptN = rn[i] + un * len * k - ue * off
    }

    private fun segmentAt(s: Float): Int {
        var lo = 0
        var hi = rCount - 2
        while (lo < hi) {
            val mid = (lo + hi + 1) ushr 1
            if (rs[mid] <= s) lo = mid else hi = mid - 1
        }
        return lo
    }

    /**
     * The route from [s0] to [s1] offset [off] to the right, point by point: the two ends
     * where they fall, and every vertex between them pushed out along the average of its two
     * segments' normals (mitred, but no further than [MAX_MITER] times, so a hairpin does not
     * throw a spike across the screen).
     */
    private inline fun walk(s0: Float, s1: Float, off: Float, emit: (Float, Float) -> Unit) {
        val a = max(0f, s0)
        val b = min(rs[rCount - 1], s1)
        if (b <= a) return
        pointAt(a, off)
        emit(ptE, ptN)
        for (i in 0 until rCount) {
            if (rs[i] <= a) continue
            if (rs[i] >= b) break
            vertexOffset(i, off)
            emit(ptE, ptN)
        }
        pointAt(b, off)
        emit(ptE, ptN)
    }

    private fun vertexOffset(i: Int, off: Float) {
        var nx = 0f
        var ny = 0f
        if (i > 0) {
            val len = rs[i] - rs[i - 1]
            nx += (rn[i] - rn[i - 1]) / len
            ny -= (re[i] - re[i - 1]) / len
        }
        if (i < rCount - 1) {
            val len = rs[i + 1] - rs[i]
            nx += (rn[i + 1] - rn[i]) / len
            ny -= (re[i + 1] - re[i]) / len
        }
        val l = hypot(nx, ny)
        if (l < 1e-4f) {
            ptE = re[i]; ptN = rn[i]
            return
        }
        // |n1 + n2| = 2 cos(half the angle), so the mitre length is 2 / |n1 + n2|.
        val miter = if (i > 0 && i < rCount - 1) min(MAX_MITER, 2f / l) else 1f
        ptE = re[i] + nx / l * off * miter
        ptN = rn[i] + ny / l * off * miter
    }

    /** [polyIn] with room for [n] points (xyz), grown if it has to be. */
    private fun polyIn(n: Int): FloatArray {
        if (polyIn.size < n * 3) {
            val size = max(n * 3, polyIn.size * 2)
            polyIn = polyIn.copyOf(size)
            polyCam = FloatArray(size)
            polyOut = FloatArray(size + 3)
        }
        return polyIn
    }

    private fun put(buf: FloatArray, at: Int, x: Float, y: Float, z: Float): Int {
        // While the car is being drawn ([bodyOn]) its points are turned by the chase.
        buf[at * 3] = if (bodyOn) bodyX(x, z) else x
        buf[at * 3 + 1] = y
        buf[at * 3 + 2] = if (bodyOn) bodyZ(x, z) else z
        return at + 1
    }

    /** A point of the plane on its ground, or with a way ([he], [hn]) on the bridge deck that runs that way. */
    private fun putCar(buf: FloatArray, at: Int, e: Float, n: Float, he: Float = Float.NaN, hn: Float = 0f): Int = put(
        buf, at, carX(e - pe, n - pn, sinH, cosH),
        if (he.isNaN() || !real) rise(e, n) else world.riseOn(e, n, he, hn), carZ(e - pe, n - pn, sinH, cosH),
    )

    /** How far the ground at the plane's ([e], [n]) stands above the car's (T-1105); 0 on the board. */
    private fun rise(e: Float, n: Float): Float = if (real) world.rise(e, n) else 0f

    /** [rise] at a point of the car's frame. */
    private fun riseCar(x: Float, z: Float): Float = rise(pe + x * cosH + z * sinH, pn - x * sinH + z * cosH)

    private fun putWorld(buf: FloatArray, at: Int, e: Float, n: Float, y: Float): Int =
        put(buf, at, carX(e - pe, n - pn, sinH, cosH), y, carZ(e - pe, n - pn, sinH, cosH))

    /** A car-frame polygon of [n] points from [polyIn], clipped, into the frame. */
    private fun poly(n: Int, fill: Float, fillInk: Int, stroke: Float, strokeInk: Int, width: Float) {
        if (n < 3) return
        for (k in 0 until n) cam.view(polyIn[k * 3], polyIn[k * 3 + 1], polyIn[k * 3 + 2], polyCam, k * 3)
        val m = HoloCamera.clipPolygon(polyCam, n, polyOut)
        if (m == 0) return
        frame.beginPoly()
        for (k in 0 until m) {
            val z = polyOut[k * 3 + 2]
            frame.point(cam.screenX(polyOut[k * 3], z), cam.screenY(polyOut[k * 3 + 1], z))
        }
        frame.endPoly(fill, fillInk, stroke, strokeInk, width * cam.unit)
    }

    private fun carLine(buf: FloatArray, a: Int, b: Int, alpha: Float, width: Float, ink: Int, fogged: Boolean = true) =
        line(buf[a * 3], buf[a * 3 + 1], buf[a * 3 + 2], buf[b * 3], buf[b * 3 + 1], buf[b * 3 + 2], alpha, width, ink, fogged)

    private fun worldLine(
        e0: Float, n0: Float, e1: Float, n1: Float, y: Float,
        alpha: Float, width: Float, ink: Int, fogged: Boolean = true,
    ) {
        val d0e = e0 - pe; val d0n = n0 - pn
        val d1e = e1 - pe; val d1n = n1 - pn
        line(
            carX(d0e, d0n, sinH, cosH), y, carZ(d0e, d0n, sinH, cosH),
            carX(d1e, d1n, sinH, cosH), y, carZ(d1e, d1n, sinH, cosH),
            alpha, width, ink, fogged,
        )
    }

    private fun worldEdge(e: Float, n: Float, h: Float, alpha: Float, width: Float) {
        val x = carX(e - pe, n - pn, sinH, cosH)
        val z = carZ(e - pe, n - pn, sinH, cosH)
        line(x, 0f, z, x, h, z, alpha, width, Ink.ACCENT, fogged = false)
    }

    /** A car-frame segment, clipped and fogged by its nearer end, into the frame. */
    private fun line(
        x0: Float, y0: Float, z0: Float, x1: Float, y1: Float, z1: Float,
        alpha: Float, width: Float, ink: Int, fogged: Boolean = true,
    ) {
        cam.view(x0, y0, z0, va)
        cam.view(x1, y1, z1, vb)
        if (!HoloCamera.clipSegment(va[0], va[1], va[2], vb[0], vb[1], vb[2], seg)) return
        val a = if (fogged) alpha * HoloCamera.fog(min(seg[2], seg[5])) else alpha
        if (a <= 0f) return
        frame.line(
            cam.screenX(seg[0], seg[2]), cam.screenY(seg[1], seg[2]),
            cam.screenX(seg[3], seg[5]), cam.screenY(seg[4], seg[5]),
            a, width * cam.unit, ink,
        )
    }

    companion object {
        /** A footprint the map gives no height: about three storeys. */
        const val DEFAULT_HEIGHT_M = 9f
        private const val MIN_HEIGHT_M = 3f
        private const val MAX_HEIGHT_M = 400f

        /** World east/north offset to the car's frame: x right of the heading. */
        fun carX(de: Float, dn: Float, sinH: Float, cosH: Float): Float = de * cosH - dn * sinH

        /** World east/north offset to the car's frame: z along the heading. */
        fun carZ(de: Float, dn: Float, sinH: Float, cosH: Float): Float = de * sinH + dn * cosH

        // The board's road, in metres.
        private const val ROADW = 6.5f
        private const val LANE = 3.4f
        private const val DASH_X = LANE / 2 + 0.1f
        private const val DASH_M = 3.5f
        private const val DASH_EVERY = 8f
        /** The car and its ribbon keep to the right-hand lane. */
        private const val LANE_X = HoloCamera.CAM_X

        /** How much road is drawn behind and ahead of the car: the fog thins it long before it ends. */
        private const val BEHIND_M = 8f
        private const val AHEAD_M = 480f
        private const val RIBBON_START = 2f
        private const val RIBBON_PAST_TURN = 60f

        /** The most pieces one straight of the route is cut into over ground with height. */
        private const val RIBBON_MAX_CUTS = 40
        private const val MIN_SEGMENT_M = 0.5f
        private const val REANCHOR_M = 5_000f
        private const val SUN_EVERY_MS = 60_000L
        private const val MAX_MITER = 2f
        /** How far either side of the turn its direction is read, past any kink at the corner. */
        private const val TURN_PROBE_M = 10f
        private const val CHEVRON_Y = 5.5f

        private const val GRID_M = 6f
        private const val GRID_HALF = 120f
        private const val GRID_AHEAD = 100f

        private const val LABEL_Y = 0.3f
        /** A name nearer than this is under the bonnet; fainter than this is unreadable. */
        private const val LABEL_NEAR = 3f
        private const val LABEL_FOG_MIN = 0.05f
        private const val STREET_AHEAD = 26f
        private const val STREET_MIN = 8f
        private const val STREET_CLEAR = 10f
        private const val TURN_LABEL_MIN_Z = 6f
        private const val TURN_LABEL_ALONG = 14f

        /**
         * At most this many footprints, the nearest within [SELECT_RADIUS], picked again
         * every [RESELECT_M]: a city block can hold hundreds, and each is a dozen strokes.
         */
        private const val MAX_BUILDINGS = 120
        private const val MAX_RING = 24
        private const val SELECT_RADIUS = 250f
        private const val RESELECT_M = 20f
        private const val FLOOR_M = 3f
        private const val FLOOR_RANGE_M = 80f
        private const val SCAN_PERIOD_M = 40f
        /** A wall within this of square to the heading is a front or back, not a side. */
        private const val FACING = 0.7f

        /** The road network (T-0980): at most this many pieces, each [PIECE] floats. */
        private const val MAX_PIECES = 800
        private const val PIECE = 6
        private const val STREET_ALPHA = 0.4f
        private const val SERVICE_ALPHA = 0.28f
        private const val PATH_ALPHA = 0.18f
        private const val STREET_WIDTH = 1.6f
        private const val PATH_WIDTH = 1.2f

        /** Half a street's width by its OSM class; 0 for a driveway, an aisle or a path, drawn as one line. */
        internal fun halfWidth(kind: String): Float = when (kind.removeSuffix("_link")) {
            "motorway", "trunk" -> 7f
            "primary", "secondary" -> 5f
            "tertiary", "unclassified", "residential", "living_street", "road", "busway" -> 4f
            else -> 0f
        }

        /** How far ([x], [y]) is from the piece ([x0], [y0])–([x1], [y1]). */
        private fun toPiece(x: Float, y: Float, x0: Float, y0: Float, x1: Float, y1: Float): Float {
            val dx = x1 - x0
            val dy = y1 - y0
            val l2 = dx * dx + dy * dy
            val t = if (l2 == 0f) 0f else (((x - x0) * dx + (y - y0) * dy) / l2).coerceIn(0f, 1f)
            return hypot(x - (x0 + t * dx), y - (y0 + t * dy))
        }

        private const val WHEEL_SIDES = 12
        private const val WHEEL_R = 0.38f

        // The chase. Time constants in seconds: the camera is most of the way round in a
        // second, slow enough that a turn is seen and quick enough that the road ahead is
        // never off the side of the screen for long.
        private const val CAM_TAU_S = 0.45f
        private const val RATE_TAU_S = 0.25f
        private const val CHASE_SNAP_MS = 1_000L
        /** Slower than this the car is standing: nothing steers and no line is drawn. */
        private const val MOVING_MPS = 1.2f
        // T-0999. The trail is this long in time, so its length is the speed; capped at a few car lengths.
        private const val TRAIL_S = 0.35f
        const val TRAIL_MAX_M = 14f
        private const val TRAIL_GAP_M = 1.6f
        /** The car is never seen turned further than this from the camera (about 40 degrees). */
        private const val MAX_BODY_YAW = 0.7f
        const val MAX_STEER_DEG = 32f
        private const val WHEELBASE = 3f
        /** The rear axle and the nose, metres ahead of the car's tail. */
        private const val AXLE_Z = 0.8f
        private const val NOSE_Z = 4.6f
        private const val TRACK_X = 0.9f
        /** The turn's line starts at this turn rate (rad/s, about 3 degrees a second) and is full [TURN_LINE_OVER] later. */
        private const val TURN_LINE_FROM = 0.05f
        private const val TURN_LINE_OVER = 0.1f
        private const val ARC_STEPS = 14
        private const val ARC_STEP_M = 2f
        /** Slowing faster than this (m/s2) is braking; full brightness [BRAKE_OVER] beyond it. */
        private const val BRAKE_FROM = 0.6f
        private const val BRAKE_OVER = 1.8f
        private const val BEAM_M = 14f

        /** An angle difference brought to the short way round, in (-pi, pi]. */
        private fun turn(d: Float): Float {
            val two = 2f * PI.toFloat()
            var a = d % two
            if (a > PI.toFloat()) a -= two
            if (a <= -PI.toFloat()) a += two
            return a
        }

        private fun hypot(a: Float, b: Float): Float = sqrt(a * a + b * b)
    }
}
