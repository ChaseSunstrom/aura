package ai.jarvis.app.car.holo

import ai.jarvis.app.ui.theme.JarvisTokens.Color
import ai.jarvis.app.ui.theme.JarvisTokens.Motion
import ai.jarvis.app.ui.theme.JarvisTokens.Radius
import ai.jarvis.app.ui.theme.JarvisTokens.Space
import ai.jarvis.app.ui.theme.JarvisTokens.Tracking
import ai.jarvis.app.ui.JarvisUi
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import androidx.core.graphics.ColorUtils
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.round
import kotlin.math.sin

/**
 * The drive as a flat street map (T-1252): seen from straight above, the way the car is
 * heading at the top, the car's arrow in the lower part of the picture. Land, water and
 * green, building footprints, roads by class with their names along them, the route in the
 * accent. Drawn from the lists the [HoloScene] was given, so the pack, the house and the
 * route feed it exactly as they feed the 3D drive, which it stands in for while that is paused.
 *
 * What is ahead (T-1255): the part of the route already driven is dimmed, the next turn
 * carries an arrow round its corner, a closure and roadworks from the live list are amber
 * on their road, one-way streets carry arrows, a main road its number, a fuel station, a
 * charger and a car park their name, and a route to somewhere new is first shown whole with
 * north up before the drive comes back.
 *
 * The shapes are rebuilt only when the map under the car changed or the car has left what
 * was picked; a frame strokes ready paths and allocates nothing. Not thread-safe: drawn under
 * the same lock the scene's setters are called under.
 *
 * Labels keep their distance on the screen, not on the ground (T-1272): drawn back, at speed
 * or in an overview, the map carries as many names as the standing drive does, so a long
 * route's overview names its main roads. A route given before the first fix is shown whole
 * from where it starts, with no car, until there is one.
 *
 * Does not cover: footpaths, traffic (how fast a road is moving), places other than fuel,
 * charging and parking or any place's icon, a north-up drive, or pinch and pan. The map
 * beyond what the scene was given: a long route's overview is the route's line over bare
 * ground, with roads only near the car. A construction site's area: the live list carries
 * roadworks as lines only. Always the dark look; the time of day does not change it.
 */
class FlatMap {
    private var plane: GeoPlane? = null
    private var stamp = -1
    private var selE = Float.NaN
    private var selN = 0f
    private var selR = 0f
    private var selLow = -1
    /** How far the view was drawn back from the standing drive when the labels were placed: 1 there, 2 at half its scale. */
    private var selZ = 1f

    private val water = Path()
    private val park = Path()
    private val buildings = Path()
    private val roads = Array(CLASSES) { Path() }
    private val works = Path()
    private val closed = Path()

    // The route in the plane, kept: where the car is along it is asked every frame.
    private var traced: HoloRoute? = null
    private val route = Path()
    private val left = Path()
    private val bend = Path()
    private var rxs = FloatArray(0)
    private var rys = FloatArray(0)
    private var buf = FloatArray(0)
    private var routeN = 0
    private var hasRoute = false
    private var routeX0 = 0f
    private var routeX1 = 0f
    private var routeY0 = 0f
    private var routeY1 = 0f
    private var endX = 0f
    private var endY = 0f
    /** Metres along the route: the next turn (-1 for none), what is driven, and what [left] was cut at. */
    private var turnAt = -1f
    private var doneAt = 0f
    private var leftAt = Float.NaN
    private var goal: LatLon? = null
    private var shownAt = NEVER

    /** How much of the picture is the overview of a new route, 1 to 0; a test reads it. */
    internal var overview = 0f; private set

    /** Labels: the text, where its middle sits in the plane, the road's angle and length there, and what it is. */
    private val names = arrayOfNulls<String>(MAX_NAMES)
    private val nameX = FloatArray(MAX_NAMES)
    private val nameY = FloatArray(MAX_NAMES)
    private val nameDeg = FloatArray(MAX_NAMES)
    private val nameRoom = FloatArray(MAX_NAMES)
    private val nameWidth = FloatArray(MAX_NAMES)
    private val nameKind = IntArray(MAX_NAMES)
    /** How many labels were placed at the last pick; a test reads it. */
    internal var nameCount = 0; private set
    /** How many of them the last frame drew; a test reads it. */
    internal var shown = 0; private set
    /** Where each name the frame drew sits on the screen: left, right, top and bottom, four to a name. */
    private val drawnBox = FloatArray(MAX_NAMES * 4)

    /** One-way arrows: where in the plane, and the way each points (east is 0, south 90); a test reads them. */
    private val arrowX = FloatArray(MAX_ARROWS)
    private val arrowY = FloatArray(MAX_ARROWS)
    internal val arrowDeg = FloatArray(MAX_ARROWS)
    internal var arrowCount = 0; private set

    private var xs = FloatArray(64)
    private var ys = FloatArray(64)
    // The box round what [project] last filled [xs] and [ys] with.
    private var boxX0 = 0f
    private var boxX1 = 0f
    private var boxY0 = 0f
    private var boxY1 = 0f

    private var width = 0f
    private var height = 0f
    private var density = 1f
    private var cx = 0f
    private var cy = 0f
    private var midY = 0f
    private var areaW = 0f
    private var areaH = 0f
    private var ahead = 1f

    // Carried from frame to frame, so the map turns and zooms instead of jumping.
    private var lastMs = Long.MIN_VALUE
    private var lastX = 0f
    private var lastY = 0f
    private var heading = 0f
    private var speed = 0f

    // This frame's view: the plane's point (vx, vy) sits at (cx, ay), turned by rot.
    private var ay = 0f
    private var vx = 0f
    private var vy = 0f
    private var rot = 0f
    private var co = 1f
    private var si = 0f
    private var sx = 0f
    private var sy = 0f

    /** Pixels per metre as the last frame was drawn; a test reads it. */
    internal var scale = 0f; private set

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    /** Roadworks: square dashes, so a worked stretch reads as broken up and a closure as shut. */
    private val dash = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = Color.WARN
        pathEffect = DashPathEffect(floatArrayOf(WORKS_DASH_M, WORKS_DASH_M), 0f)
    }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.LABEL_FACE
        textAlign = Paint.Align.CENTER
        letterSpacing = Tracking.CONTROL
        strokeJoin = Paint.Join.ROUND
    }
    private val arrow = Path()
    /** Pointing east, one unit to the tip: a one-way street's arrow, and the head of the turn's. */
    private val chevron = Path().apply {
        moveTo(-0.6f, -1f)
        lineTo(0.6f, 0f)
        lineTo(-0.6f, 1f)
    }
    private val head = Path().apply {
        moveTo(1.5f, 0f)
        lineTo(-0.2f, 1.1f)
        lineTo(-0.2f, -1.1f)
        close()
    }

    /** A [width] by [height] px surface at [density] px per dp, the car placed within [area]. */
    fun resize(width: Float, height: Float, density: Float, area: Box) {
        this.width = width
        this.height = height
        this.density = density
        cx = (area.left + area.right) / 2
        cy = area.top + area.height * CAR_DOWN
        midY = (area.top + area.bottom) / 2
        areaW = area.width
        areaH = area.height
        ahead = max(1f, cy - area.top)
        text.textSize = NAME_DP * density
        arrow.rewind()
        arrow.moveTo(0f, -ARROW_DP * density)
        arrow.lineTo(ARROW_DP * 0.72f * density, ARROW_DP * 0.82f * density)
        arrow.lineTo(0f, ARROW_DP * 0.42f * density)
        arrow.lineTo(-ARROW_DP * 0.72f * density, ARROW_DP * 0.82f * density)
        arrow.close()
        selE = Float.NaN
    }

    /**
     * The map round [pose] at [timeMs] on the drive's clock. Before the first fix (no [pose]):
     * the scene's route whole, seen from where it starts, or the bare ground with no route.
     */
    fun draw(canvas: Canvas, scene: HoloScene, pose: DriveView.Pose?, timeMs: Long) {
        canvas.drawColor(Color.MAP_LAND)
        val lat: Double
        val lon: Double
        if (pose != null) {
            lat = pose.lat
            lon = pose.lon
        } else {
            val shape = scene.route?.shape
            if (shape == null || shape.size < 2) return
            lat = shape[0].lat
            lon = shape[0].lon
        }
        // The plane is this map's own: the scene's moves with each route, and a shape kept
        // across that move would be drawn somewhere else.
        var p = plane
        var moved = false
        if (p == null || hypot(p.east(lon), p.north(lat)) > REANCHOR_M) {
            p = GeoPlane(lat, lon)
            plane = p
            moved = true
        }
        val x = p.east(lon)
        val y = -p.north(lat)
        // Not eased from the route's start: the first fix would read as a leap at speed, and end the overview.
        if (pose != null) ease(x, y, pose.headingDeg, timeMs, moved)
        if (moved || scene.route !== traced) trace(p, scene.route, timeMs)
        // With no car to come back to, the whole route stays up; the fix finds it there and it leaves in its own time.
        if (pose == null) shownAt = max(shownAt, timeMs - OVERVIEW_MS)
        if (hasRoute) {
            val at = along(rxs, rys, routeN, x, y, doneAt, OFF_ROUTE_M)
            if (at >= 0f) doneAt = at
        }
        var age = if (hasRoute && shownAt != NEVER) timeMs - shownAt else -1L
        // Driving off ends the look at the whole route: what matters now is the road ahead.
        if (speed > OVERVIEW_SPEED && age >= 0L && age < OVERVIEW_MS) {
            shownAt = timeMs - OVERVIEW_MS
            age = OVERVIEW_MS
        }
        val f = overview(age)
        overview = f

        val span = NEAR_SPAN_M + (FAR_SPAN_M - NEAR_SPAN_M) * (speed / FAR_SPEED).coerceIn(0f, 1f)
        var s = ahead / span
        ay = cy
        vx = x
        vy = y
        rot = heading
        if (f > 0f) {
            val x0 = min(routeX0, x)
            val x1 = max(routeX1, x)
            val y0 = min(routeY0, y)
            val y1 = max(routeY1, y)
            // Never closer than the drive: a route that ends round the corner is no reason to zoom in.
            val fit = min(s, FIT * min(areaW / max(x1 - x0, 1f), areaH / max(y1 - y0, 1f)))
            s += (fit - s) * f
            ay += (midY - cy) * f
            vx += ((x0 + x1) / 2 - x) * f
            vy += ((y0 + y1) / 2 - y) * f
            // The short way round to north: from 350 it is 10 degrees on, not 350 back.
            rot = ((heading % 360f + 540f) % 360f - 180f) * (1f - f)
        }
        // An area with no room, or a route whose box is not a number: nothing to draw a map into.
        if (!(s > 0f)) return
        scale = s
        co = cos(-rot * PI.toFloat() / 180f)
        si = sin(-rot * PI.toFloat() / 180f)
        // Wider than the drive ever is (only a long route's overview): streets would be one smudge,
        // so the main roads alone, and theirs the only names.
        // ponytail: a name still needs a piece of road long enough to lie along, and a main road mapped
        // in short pieces has none at this zoom; join the pieces by name if such an overview reads bare.
        val low = if (s >= FINE_PX_PER_M) MINOR else if (s >= ahead / FAR_SPAN_M) STREET else MAJOR
        val need = hypot(max(cx, width - cx), max(ay, height - ay)) / s
        val z = ahead / NEAR_SPAN_M / s
        if (moved || stamp != scene.stamp || low != selLow || selE.isNaN() || hypot(vx - selE, vy - selN) + need > selR ||
            z > selZ * ZOOM_HOLD || z * ZOOM_HOLD < selZ
        ) {
            // In steps, and held past each: the labels do not reshuffle with every change of speed, and
            // the standing drive is always placed at 1, however it was reached.
            // ponytail: between two steps the spacing on the screen is up to a fifth off; place per frame if it shows.
            selZ = ZOOM_STEP.pow(round(ln(z) / ln(ZOOM_STEP)))
            pick(scene, p, vx, vy, need + MARGIN_M, low)
        }

        val k = (s / NEAR_PX_PER_M).coerceIn(THIN, THICK) * density
        val u = k / s
        canvas.save()
        canvas.translate(cx, ay)
        canvas.rotate(-rot)
        canvas.scale(s, s)
        canvas.translate(-vx, -vy)
        fill.color = Color.MAP_PARK
        canvas.drawPath(park, fill)
        fill.color = Color.MAP_WATER
        canvas.drawPath(water, fill)
        if (low == MINOR) {
            fill.color = Color.MAP_BUILDING
            canvas.drawPath(buildings, fill)
        }
        // Every edge first, then every surface: a junction is one surface, not three outlines.
        line.color = Color.MAP_CASING
        for (c in low until CLASSES) {
            line.strokeWidth = (ROAD_DP[c] + EDGE_DP) * u
            canvas.drawPath(roads[c], line)
        }
        for (c in low until CLASSES) {
            line.color = ROAD_INK[c]
            line.strokeWidth = ROAD_DP[c] * u
            canvas.drawPath(roads[c], line)
        }
        line.color = Color.TEXT_DIM
        line.strokeWidth = ONEWAY_LINE
        for (i in 0 until arrowCount) {
            canvas.save()
            canvas.translate(arrowX[i], arrowY[i])
            canvas.rotate(arrowDeg[i])
            canvas.scale(ONEWAY_DP * u, ONEWAY_DP * u)
            canvas.drawPath(chevron, line)
            canvas.restore()
        }
        if (hasRoute) route(canvas, u)
        // Over the route: a closed road on the way is the thing to see.
        line.color = Color.MAP_CASING
        line.strokeWidth = (WORKS_DP + EDGE_DP) * u
        canvas.drawPath(closed, line)
        line.color = Color.WARN
        line.strokeWidth = WORKS_DP * u
        canvas.drawPath(closed, line)
        dash.strokeWidth = WORKS_DP * u
        canvas.drawPath(works, dash)
        canvas.restore()

        shown = 0
        names(canvas, s)
        if (pose != null) car(canvas, x, y, timeMs)
    }

    private fun ease(x: Float, y: Float, headingDeg: Float, timeMs: Long, moved: Boolean) {
        val dt = if (lastMs == Long.MIN_VALUE) -1L else timeMs - lastMs
        if (dt == 0L) return
        if (moved || dt < 0 || dt > GAP_MS) {
            heading = headingDeg
            speed = 0f
        } else {
            // The short way round: from 350 to 10 is 20 degrees, not 340.
            val turn = ((headingDeg - heading) % 360f + 540f) % 360f - 180f
            heading += turn * min(1f, dt / HEADING_MS)
            speed += (hypot(x - lastX, y - lastY) * 1000f / dt - speed) * min(1f, dt / SPEED_MS)
        }
        lastMs = timeMs
        lastX = x
        lastY = y
    }

    /**
     * [r]'s line into the plane, once for each route: the whole line, not only what is near,
     * since a route is one piece and re-asked at each turn. A route that ends somewhere new,
     * given to a car that is standing, starts the overview; the same one asked again does not.
     */
    private fun trace(p: GeoPlane, r: HoloRoute?, timeMs: Long) {
        traced = r
        route.rewind()
        left.rewind()
        doneAt = 0f
        leftAt = Float.NaN
        turnAt = -1f
        val shape = r?.shape
        hasRoute = shape != null && shape.size >= 2
        if (r == null || shape == null || !hasRoute) {
            goal = null
            shownAt = NEVER
            return
        }
        val n = shape.size
        project(p, shape, 0f, 0f, Float.MAX_VALUE)
        if (rxs.size < n) {
            rxs = FloatArray(n * 2)
            rys = FloatArray(n * 2)
            buf = FloatArray(n * 4)
        }
        xs.copyInto(rxs, 0, 0, n)
        ys.copyInto(rys, 0, 0, n)
        routeN = n
        routeX0 = boxX0
        routeX1 = boxX1
        routeY0 = boxY0
        routeY1 = boxY1
        open(route, n)
        endX = xs[n - 1]
        endY = ys[n - 1]
        r.turnAt?.let { turnAt = along(rxs, rys, n, p.east(it.lon), -p.north(it.lat), 0f, TURN_SNAP_M) }
        if (newTrip(p, goal, shape[n - 1]) && speed <= OVERVIEW_SPEED) shownAt = timeMs
        goal = shape[n - 1]
    }

    /** The route: what is driven dimmed under what is left, the next turn's arrow, the end's ring. [u] is metres to a dp. */
    private fun route(canvas: Canvas, u: Float) {
        val w = ROUTE_DP * u
        if (leftAt.isNaN() || doneAt - leftAt >= TRIM_M) {
            leftAt = doneAt
            left.rewind()
            pairs(left, piece(rxs, rys, routeN, doneAt, Float.MAX_VALUE, buf))
        }
        line.color = ColorUtils.setAlphaComponent(Color.ACCENT, GLOW_ALPHA)
        line.strokeWidth = w * GLOW
        canvas.drawPath(left, line)
        line.color = Color.MAP_CASING
        line.strokeWidth = w + EDGE_DP * u
        canvas.drawPath(route, line)
        line.color = Color.ACCENT_DEEP
        line.strokeWidth = w
        canvas.drawPath(route, line)
        line.color = Color.ACCENT
        canvas.drawPath(left, line)
        if (turnAt >= 0f && doneAt < turnAt) {
            val m = piece(rxs, rys, routeN, turnAt - TURN_BACK_DP * u, turnAt + TURN_FWD_DP * u, buf)
            if (m >= 2) {
                bend.rewind()
                pairs(bend, m)
                line.color = Color.TEXT_BRIGHT
                line.strokeWidth = w * TURN
                canvas.drawPath(bend, line)
                canvas.save()
                canvas.translate(buf[m * 2 - 2], buf[m * 2 - 1])
                canvas.rotate(bearing(buf, m))
                canvas.scale(w, w)
                line.color = Color.MAP_CASING
                line.strokeWidth = EDGE_DP / ROUTE_DP
                canvas.drawPath(head, line)
                fill.color = Color.TEXT_BRIGHT
                canvas.drawPath(head, fill)
                canvas.restore()
            }
        }
        fill.color = Color.TEXT_BRIGHT
        canvas.drawCircle(endX, endY, w * 0.9f, fill)
        fill.color = Color.ACCENT
        canvas.drawCircle(endX, endY, w * 0.5f, fill)
    }

    /** ([px], [py]) of the plane on the screen, into [sx] and [sy]. */
    private fun screen(px: Float, py: Float) {
        val dx = px - vx
        val dy = py - vy
        sx = cx + scale * (dx * co - dy * si)
        sy = ay + scale * (dx * si + dy * co)
    }

    private fun names(canvas: Canvas, s: Float) {
        val lift = text.textSize * BASELINE
        val pad = Space.TIGHT * density
        val round = Radius.MD * density
        val halfH = text.textSize / 2 + pad
        var drawn = 0
        for (i in 0 until nameCount) {
            // A name longer than its road at this zoom would run off the end of it.
            if (nameRoom[i] * s < nameWidth[i]) continue
            screen(nameX[i], nameY[i])
            // A centred name reaches half its width either side of its point: it is left out only when
            // wholly off the picture, and otherwise moved in whole, so a place at the edge is not cut.
            val centred = nameRoom[i] == ANYWHERE
            val halfW = nameWidth[i] / 2 + pad
            if (sy < 0f || sy > height) continue
            if (if (centred) sx + halfW < 0f || sx - halfW > width else sx < 0f || sx > width) continue
            val name = names[i] ?: continue
            if (centred) {
                val at = clampInside(sx, halfW, width)
                // Moved in onto a name already drawn, it gives way, as it would have at its placing.
                // ponytail: only a moved name is checked, and only against the names drawn before it; a
                // name drawn after it that it sits on is not moved aside. Add when two such names show.
                if (at != sx) {
                    if (clashes(drawn, at, halfW, halfH)) continue
                    sx = at
                }
            }
            shown++
            drawnBox[drawn * 4] = sx - halfW
            drawnBox[drawn * 4 + 1] = sx + halfW
            drawnBox[drawn * 4 + 2] = sy - halfH
            drawnBox[drawn * 4 + 3] = sy + halfH
            drawn++
            val kind = nameKind[i]
            // Never upside down: a name reads left to right whichever way the map is turned.
            // A place's name and a road's number stand level, as on a sign.
            var deg = if (kind == NAME) ((nameDeg[i] - rot) % 180f + 180f) % 180f else 0f
            if (deg > 90f) deg -= 180f
            canvas.save()
            canvas.rotate(deg, sx, sy)
            if (kind == PLATE) {
                val hw = nameWidth[i] / 2 + pad
                val hh = text.textSize / 2 + pad / 2
                fill.color = Color.MAP_CASING
                canvas.drawRoundRect(sx - hw, sy - hh, sx + hw, sy + hh, round, round, fill)
                line.color = Color.TEXT_DIM
                line.strokeWidth = Space.HAIRLINE * density
                canvas.drawRoundRect(sx - hw, sy - hh, sx + hw, sy + hh, round, round, line)
            } else {
                text.style = Paint.Style.STROKE
                text.strokeWidth = HALO_DP * density
                text.color = Color.MAP_CASING
                canvas.drawText(name, sx, sy + lift, text)
            }
            text.style = Paint.Style.FILL
            text.color = if (kind == PLACE) Color.TEXT_DIM else Color.TEXT_BRIGHT
            canvas.drawText(name, sx, sy + lift, text)
            canvas.restore()
        }
    }

    /** Whether a name [halfW] wide and [halfH] high at ([x], [sy]) sits on one of the first [n] drawn this frame ([drawnBox]). */
    private fun clashes(n: Int, x: Float, halfW: Float, halfH: Float): Boolean {
        for (j in 0 until n) {
            val b = j * 4
            if (x - halfW < drawnBox[b + 1] && x + halfW > drawnBox[b] &&
                sy - halfH < drawnBox[b + 3] && sy + halfH > drawnBox[b + 2]) return true
        }
        return false
    }

    /** The car where it is in the view: at its place in the drive, wherever that falls in an overview. */
    private fun car(canvas: Canvas, x: Float, y: Float, timeMs: Long) {
        screen(x, y)
        // A ring that leaves the car and fades: the map is alive while the car stands still.
        val beat = (timeMs % PULSE_MS).toFloat() / PULSE_MS
        line.color = ColorUtils.setAlphaComponent(Color.ACCENT, ((1f - beat) * RING_ALPHA).toInt())
        line.strokeWidth = density
        canvas.drawCircle(sx, sy, (ARROW_DP + PULSE_DP * beat) * density, line)
        fill.color = ColorUtils.setAlphaComponent(Color.ACCENT, DISC_ALPHA)
        canvas.drawCircle(sx, sy, ARROW_DP * DISC * density, fill)
        canvas.save()
        canvas.translate(sx, sy)
        canvas.rotate(heading - rot)
        line.color = Color.MAP_CASING
        line.strokeWidth = EDGE_DP * density
        canvas.drawPath(arrow, line)
        fill.color = Color.ACCENT
        canvas.drawPath(arrow, fill)
        canvas.restore()
    }

    /** Rebuild every shape within [r] metres of ([x], [y]), of the classes from [low] up; [MINOR] adds buildings, one-way arrows and places. */
    private fun pick(scene: HoloScene, p: GeoPlane, x: Float, y: Float, r: Float, low: Int) {
        stamp = scene.stamp
        selE = x
        selN = y
        selR = r
        selLow = low
        val fine = low == MINOR
        water.rewind()
        park.rewind()
        buildings.rewind()
        works.rewind()
        closed.rewind()
        for (path in roads) path.rewind()
        nameCount = 0
        arrowCount = 0

        val details = scene.details
        for (i in details.indices) {
            val d = details[i]
            val into = when (d.kind) {
                "water" -> water
                "green", "wood", "field", "yard" -> park
                "roadworks" -> works
                else -> continue
            }
            if (!project(p, d.pts, x, y, r)) continue
            if (into === works) open(into, d.pts.size) else ring(into, d.pts.size)
        }
        val live = scene.live
        for (i in live.indices) {
            val d = live[i]
            val into = when (d.kind) {
                HoloWorld.CLOSURE -> closed
                "roadworks" -> works
                else -> continue
            }
            if (d.pts.size >= 2 && project(p, d.pts, x, y, r)) open(into, d.pts.size)
        }
        val houses = scene.buildingList
        if (fine) {
            for (i in houses.indices) {
                val ring = houses[i].ring
                if (ring.size >= 3 && project(p, ring, x, y, r)) ring(buildings, ring.size)
            }
        }
        val list = scene.roadList
        // The bigger roads first, so where names crowd it is the main road that keeps its own.
        for (want in MAJOR downTo low) {
            for (i in list.indices) {
                val road = list[i]
                if (cls(road.kind) != want || road.line.size < 2 || !project(p, road.line, x, y, r)) continue
                val n = road.line.size
                open(roads[want], n)
                var length = 0f
                for (j in 1 until n) length += hypot(xs[j] - xs[j - 1], ys[j] - ys[j - 1])
                if (length < NAME_MIN_M) continue
                // At the middle of a short road, and every so often along a long one, so the
                // street the car is on is named wherever it is on it.
                val step = NAME_EVERY_M * selZ
                val mid = min(length / 2, step / 2)
                val named = want != MINOR && road.name.isNotBlank()
                if (named) label(HoloWorld.whole(road.name.trim(), NAME_CHARS).uppercase(), n, length, mid, NAME)
                // The number between the names; on a road with no name, where the name would be.
                shield(road.kind, road.ref)?.let { label(it, n, length, if (named) mid + step / 2 else mid, PLATE) }
                if (fine && oneWay(road.kind, road.oneway, length)) {
                    every(n, min(length / 2, ONEWAY_EVERY_M / 2), ONEWAY_EVERY_M) { mx, my, deg ->
                        if (arrowCount < MAX_ARROWS && hypot(mx - selE, my - selN) <= selR) {
                            arrowX[arrowCount] = mx
                            arrowY[arrowCount] = my
                            arrowDeg[arrowCount++] = if (road.oneway < 0) deg + 180f else deg
                        }
                    }
                }
            }
        }
        if (!fine) return
        // Places last: one gives way to a street's name, never the other way round.
        // The fuel, charging and parking first, then the shops and services (T-1277), which
        // give way to them through the same gap, and are no more than SHOPS_MAX in a view.
        var shops = 0
        for (landmarks in booleanArrayOf(true, false)) {
            for (i in houses.indices) {
                val b = houses[i]
                if (isLandmark(b.look.trade) != landmarks) continue
                val label = placeName(b.look.sign, b.look.trade) ?: continue
                if (!landmarks && shops == SHOPS_MAX) break
                if (!project(p, b.ring, x, y, r)) continue
                val before = nameCount
                place(label, text.measureText(label), ANYWHERE, (boxX0 + boxX1) / 2, (boxY0 + boxY1) / 2, 0f, PLACE)
                if (!landmarks && nameCount > before) shops++
            }
        }
        for (i in details.indices) {
            val d = details[i]
            // A car park to drive to, not the three spaces outside a shop.
            if (d.kind != "parking" || !project(p, d.pts, x, y, r) || min(boxX1 - boxX0, boxY1 - boxY0) < PARKING_MIN_M) continue
            place(PARKING, text.measureText(PARKING), ANYWHERE, (boxX0 + boxX1) / 2, (boxY0 + boxY1) / 2, 0f, PLATE)
        }
    }

    /**
     * [pts] into [xs] and [ys], east and south of the plane's origin in metres; true when
     * their bounding box reaches within [r] of ([x], [y]). The box, not the points: a long
     * straight piece crosses the picture with both its ends outside it.
     */
    private fun project(p: GeoPlane, pts: List<LatLon>, x: Float, y: Float, r: Float): Boolean {
        val n = pts.size
        if (xs.size < n) {
            xs = FloatArray(n * 2)
            ys = FloatArray(n * 2)
        }
        var x0 = Float.MAX_VALUE
        var x1 = -Float.MAX_VALUE
        var y0 = Float.MAX_VALUE
        var y1 = -Float.MAX_VALUE
        for (i in 0 until n) {
            val pt = pts[i]
            val px = p.east(pt.lon)
            val py = -p.north(pt.lat)
            xs[i] = px
            ys[i] = py
            if (px < x0) x0 = px
            if (px > x1) x1 = px
            if (py < y0) y0 = py
            if (py > y1) y1 = py
        }
        boxX0 = x0
        boxX1 = x1
        boxY0 = y0
        boxY1 = y1
        return x0 <= x + r && x1 >= x - r && y0 <= y + r && y1 >= y - r
    }

    private fun open(into: Path, n: Int) {
        into.moveTo(xs[0], ys[0])
        for (j in 1 until n) into.lineTo(xs[j], ys[j])
    }

    private fun ring(into: Path, n: Int) {
        open(into, n)
        into.close()
    }

    /** The [n] points [piece] left in [buf], as a line. */
    private fun pairs(into: Path, n: Int) {
        if (n < 2) return
        into.moveTo(buf[0], buf[1])
        for (j in 1 until n) into.lineTo(buf[j * 2], buf[j * 2 + 1])
    }

    /** [at] with the point and the bearing there, [first] metres along the [n] points in [xs]/[ys] and every [step] after. */
    private inline fun every(n: Int, first: Float, step: Float, at: (Float, Float, Float) -> Unit) {
        var want = first
        var gone = 0f
        for (j in 1 until n) {
            val d = hypot(xs[j] - xs[j - 1], ys[j] - ys[j - 1])
            while (d > 0f && want <= gone + d) {
                val t = (want - gone) / d
                at(xs[j - 1] + (xs[j] - xs[j - 1]) * t, ys[j - 1] + (ys[j] - ys[j - 1]) * t,
                    atan2(ys[j] - ys[j - 1], xs[j] - xs[j - 1]) * 180f / PI.toFloat())
                want += step
            }
            gone += d
        }
    }

    /** [label] along the road in [xs]/[ys], from [first] metres in and every [NAME_EVERY_M] of the standing drive's after. */
    private fun label(label: String, n: Int, length: Float, first: Float, kind: Int) {
        val wide = text.measureText(label)
        every(n, first, NAME_EVERY_M * selZ) { mx, my, deg -> place(label, wide, length, mx, my, deg, kind) }
    }

    /** A label, unless one placed before it is in its way ([givesWay]): the first placed keeps its place. */
    private fun place(label: String, wide: Float, room: Float, mx: Float, my: Float, deg: Float, kind: Int) {
        if (nameCount == MAX_NAMES || hypot(mx - selE, my - selN) > selR) return
        for (i in 0 until nameCount) {
            if (givesWay(hypot(nameX[i] - mx, nameY[i] - my) / selZ, names[i] == label)) return
        }
        names[nameCount] = label
        nameX[nameCount] = mx
        nameY[nameCount] = my
        nameDeg[nameCount] = deg
        nameRoom[nameCount] = room
        nameWidth[nameCount] = wide
        nameKind[nameCount] = kind
        nameCount++
    }

    /** The labels placed at the last pick, in the order they took their places; a test reads them. */
    internal fun labels(): List<String> = names.take(nameCount).filterNotNull()

    companion object {
        const val MINOR = 0
        const val STREET = 1
        const val MAJOR = 2
        private const val CLASSES = 3

        /** A road's class on the map from its OSM `highway`; -1 for what is not drawn (footpaths). */
        internal fun cls(kind: String): Int = when {
            // A slip road is drawn a class down: an interchange is its roads, not one blob of ramps.
            kind.endsWith("_link") -> STREET
            else -> whole(kind)
        }

        private fun whole(kind: String): Int = when (kind) {
            "motorway", "trunk", "primary" -> MAJOR
            "secondary", "tertiary", "unclassified", "residential", "living_street", "road" -> STREET
            "service", "track", "pedestrian" -> MINOR
            else -> -1
        }

        /**
         * How far along the [n]-point line in [xs]/[ys] the point nearest ([x], [y]) is, in
         * metres from its start and never before [from]; -1 when the line is further off than
         * [limit], or [from] is past its end. Where the line passes the point twice (out and
         * back along one street) the earlier pass wins unless the later is clearly nearer, so
         * a car on the way out is not taken to be on the way back.
         */
        internal fun along(xs: FloatArray, ys: FloatArray, n: Int, x: Float, y: Float, from: Float, limit: Float): Float {
            var best = -1f
            var off = limit
            var start = 0f
            for (j in 1 until n) {
                val dx = xs[j] - xs[j - 1]
                val dy = ys[j] - ys[j - 1]
                val d = hypot(dx, dy)
                if (d > 0f && start + d >= from) {
                    val t = (((x - xs[j - 1]) * dx + (y - ys[j - 1]) * dy) / (d * d)).coerceIn(max(0f, (from - start) / d), 1f)
                    val o = hypot(x - xs[j - 1] - dx * t, y - ys[j - 1] - dy * t)
                    if (o <= off - (if (best < 0f) 0f else AGAIN_M)) {
                        best = start + d * t
                        off = o
                    }
                }
                start += d
            }
            return best
        }

        /**
         * The part of the [n]-point line in [xs]/[ys] between [from] and [to] metres along
         * it, into [out] as x, y pairs; the number of points. Cut at the line's own ends and
         * at what [out] holds; none when [from] is past the end.
         */
        internal fun piece(xs: FloatArray, ys: FloatArray, n: Int, from: Float, to: Float, out: FloatArray): Int {
            val a = max(0f, from)
            var count = 0
            var start = 0f
            for (j in 1 until n) {
                val dx = xs[j] - xs[j - 1]
                val dy = ys[j] - ys[j - 1]
                val d = hypot(dx, dy)
                if (d > 0f && start + d > a && start < to) {
                    if (count == 0) {
                        val t = (a - start) / d
                        out[0] = xs[j - 1] + dx * t
                        out[1] = ys[j - 1] + dy * t
                        count = 1
                    }
                    if (count * 2 + 2 > out.size) return count
                    val t = min(1f, (to - start) / d)
                    out[count * 2] = xs[j - 1] + dx * t
                    out[count * 2 + 1] = ys[j - 1] + dy * t
                    count++
                }
                start += d
            }
            return count
        }

        /** The way the last of the [n] points in [pts] (x, y pairs) is reached, in degrees: east 0, south 90. */
        internal fun bearing(pts: FloatArray, n: Int): Float =
            if (n < 2) 0f else atan2(pts[n * 2 - 1] - pts[n * 2 - 3], pts[n * 2 - 2] - pts[n * 2 - 4]) * 180f / PI.toFloat()

        /** How much of the picture is the overview, [ageMs] after a new route was shown: all of it for [OVERVIEW_MS], eased out over [BACK_MS], none after or before. */
        internal fun overview(ageMs: Long): Float = when {
            ageMs < 0L || ageMs >= OVERVIEW_MS + BACK_MS -> 0f
            ageMs <= OVERVIEW_MS -> 1f
            else -> {
                val t = (ageMs - OVERVIEW_MS).toFloat() / BACK_MS
                1f - t * t * (3f - 2f * t)
            }
        }

        /** Whether a route ending at [new] goes somewhere other than the one that ended at [old]: the same route is asked again at each turn, and ends a few metres off. */
        internal fun newTrip(p: GeoPlane, old: LatLon?, new: LatLon): Boolean =
            old == null || hypot(p.east(new.lon) - p.east(old.lon), p.north(new.lat) - p.north(old.lat)) > NEW_TRIP_M

        /**
         * Whether a road of [kind], [length] metres long, carries one-way arrows. A street
         * does; a motorway or trunk carriageway and their slip roads do not (each only ever
         * goes one way, and its other half is drawn beside it), nor does a driveway.
         */
        internal fun oneWay(kind: String, oneway: Int, length: Float): Boolean =
            oneway != 0 && cls(kind) >= STREET && !kind.startsWith("motorway") && !kind.startsWith("trunk") && length >= ONEWAY_MIN_M

        /**
         * The number a road of [kind] carries on the map from its OSM `ref`: main roads only,
         * the first of several, whole (the 3D sign's [HoloWorld.routeMark] drops the letters
         * its shield's shape stands for; a plain plate has no shape to say them).
         */
        internal fun shield(kind: String, ref: String?): String? =
            if (cls(kind) != MAJOR || ref == null) null
            else HoloWorld.whole(ref.substringBefore(';').trim(), SHIELD_CHARS).uppercase().ifEmpty { null }

        /** What a driver goes to the wheel for: placed before the shops and services, which give way to it (T-1277). */
        internal fun isLandmark(trade: String): Boolean = trade == "fuel" || trade == "charging_station" || trade == "parking"

        /** The name a business of [trade] goes by on the map, or null for one a driver does not look for from the wheel. */
        internal fun placeName(sign: String, trade: String): String? {
            val plain = when (trade) {
                "fuel" -> "FUEL"
                "charging_station" -> "CHARGING"
                "parking" -> PARKING
                // Shops, food, health, lodging and a few services (T-1277): the OSM `shop`/`amenity` the pack keeps.
                "supermarket" -> "SUPERMARKET"
                "convenience" -> "SHOP"
                "restaurant", "fast_food" -> "FOOD"
                "cafe" -> "CAFE"
                "pharmacy" -> "PHARMACY"
                "hospital" -> "HOSPITAL"
                "clinic", "doctors" -> "CLINIC"
                "hotel" -> "HOTEL"
                "motel" -> "MOTEL"
                "bank" -> "BANK"
                "school" -> "SCHOOL"
                "post_office" -> "POST"
                else -> return null
            }
            return sign.trim().ifEmpty { null }?.let { HoloWorld.whole(it, NAME_CHARS).uppercase() } ?: plain
        }

        /**
         * Whether a label gives way to one already placed [d] metres from it; [same] when the two
         * read the same. Metres as the standing drive draws them: the caller divides by how far
         * the view is drawn back, so the gap is the same on the screen at every zoom.
         */
        internal fun givesWay(d: Float, same: Boolean): Boolean = d < NAME_GAP_M || (same && d < SAME_NAME_GAP_M)

        /**
         * The centre x that keeps a centred name [half] px either side of it wholly inside a picture
         * [width] px wide: moved in from the edge it is near, and left where it is in the middle. A
         * name wider than the picture is centred, the best there is.
         */
        internal fun clampInside(x: Float, half: Float, width: Float): Float =
            if (2 * half >= width) width / 2 else x.coerceIn(half, width - half)

        private val ROAD_INK = intArrayOf(Color.MAP_ROAD_MINOR, Color.MAP_ROAD, Color.MAP_ROAD_MAJOR)
        /** Road widths on the screen at the closest zoom, by class: a map's widths, not the road's own. */
        private val ROAD_DP = floatArrayOf(5f, 10f, 14f)
        private const val EDGE_DP = 2f
        private const val ROUTE_DP = 8f
        private const val GLOW = 2.4f
        private const val GLOW_ALPHA = 46
        private const val THIN = 0.55f
        private const val THICK = 1.2f

        /** How far down the drive's area the car sits: most of the picture is the road ahead. */
        private const val CAR_DOWN = 0.72f
        /** Metres from the car to the top of the area: standing still, and at [FAR_SPEED] m/s and over. */
        private const val NEAR_SPAN_M = 190f
        private const val FAR_SPAN_M = 520f
        private const val FAR_SPEED = 30f
        /** The zoom the widths are typed for (a 720 px high car screen, standing still). */
        private const val NEAR_PX_PER_M = 2.6f
        /** Wider than this, buildings and driveways are clutter and are left out. */
        private const val FINE_PX_PER_M = 1.3f
        private const val MARGIN_M = 160f
        private const val REANCHOR_M = 4000f
        private const val GAP_MS = 2000L
        private const val HEADING_MS = 350f
        private const val SPEED_MS = 2500f

        /** How long a new route is shown whole, and how long the way back to the drive takes. */
        internal const val OVERVIEW_MS = Motion.Dur.TOAST.toLong()
        internal const val BACK_MS = Motion.Dur.ENTER.toLong()
        private const val NEVER = Long.MIN_VALUE
        /** Faster than this (m/s) the car is driving, not being shown its route. */
        private const val OVERVIEW_SPEED = 3f
        /** The share of the area the whole route may fill: the rest is air round it. */
        private const val FIT = 0.7f
        private const val NEW_TRIP_M = 50f

        /** Further from the route than this, the car has left it and nothing more of it is driven. */
        private const val OFF_ROUTE_M = 60f
        private const val TURN_SNAP_M = 30f
        /** A later pass of the route must be this much nearer than an earlier one to be where the car is. */
        private const val AGAIN_M = 3f
        private const val TRIM_M = 1f
        /** The turn's arrow: how far before the corner it starts, how far round it reaches, its width in the route's. */
        private const val TURN_BACK_DP = 28f
        private const val TURN_FWD_DP = 22f
        private const val TURN = 0.7f

        private const val WORKS_DP = 6f
        private const val WORKS_DASH_M = 6f

        private const val MAX_ARROWS = 96
        private const val ONEWAY_MIN_M = 30f
        private const val ONEWAY_EVERY_M = 90f
        /** A one-way arrow's half height, and its line's width in that. */
        private const val ONEWAY_DP = 4f
        private const val ONEWAY_LINE = 0.45f

        private const val MAX_NAMES = 64
        /** Shop and service names in one view: a street's few, not every door on it. */
        private const val SHOPS_MAX = 10
        private const val NAME_CHARS = 22
        private const val NAME_DP = 13f
        private const val HALO_DP = 4f
        private const val BASELINE = 0.36f
        private const val NAME_MIN_M = 40f
        private const val NAME_GAP_M = 70f
        private const val SAME_NAME_GAP_M = 180f
        private const val NAME_EVERY_M = 240f
        /** Labels are placed again when the view is drawn back or in by a step, and not before it is most of the way to the next. */
        private const val ZOOM_STEP = 1.25f
        private const val ZOOM_HOLD = 1.18f
        /** What a label is: a street's name along it, a place's name, a road's number or a car park's P on a plate. */
        private const val NAME = 0
        private const val PLACE = 1
        private const val PLATE = 2
        /** The room of a name with no road to lie along: it is centred on its point, and is no limit. */
        private const val ANYWHERE = Float.MAX_VALUE
        private const val SHIELD_CHARS = 8
        private const val PARKING = "P"
        private const val PARKING_MIN_M = 20f

        private const val ARROW_DP = 15f
        private const val DISC = 1.5f
        private const val DISC_ALPHA = 40
        private const val RING_ALPHA = 150
        private const val PULSE_DP = 26f
        private const val PULSE_MS = 2400L
    }
}
