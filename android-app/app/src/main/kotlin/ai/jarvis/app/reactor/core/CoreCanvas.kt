package ai.jarvis.app.reactor.core

import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RadialGradient
import android.graphics.Shader
import ai.jarvis.app.reactor.core.CoreEngine.Flow
import ai.jarvis.app.reactor.core.CoreEngine.RINGS
import ai.jarvis.app.reactor.core.CoreEngine.Segment
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sign
import kotlin.math.sin

/**
 * The core drawn on an Android [Canvas]: `aura-web/src/lib/ds/core/canvas2d.ts`, the
 * console's own Canvas2D drawing, line for line (T-0712). Canvas2D's 'lighter' is
 * [PorterDuff.Mode.ADD]; its CSS pixels are dp, so [draw] scales by the density first and the
 * reference's board widths mean the same here as there.
 *
 * Painter's order stands in for a depth buffer, as there: the back half of the shell, the ring
 * segments far to near (their dark faces hide what is behind them), the front half, the flow,
 * the core and the shock rings.
 *
 * Does not cover per-pixel depth (as canvas2d.ts), or the WebGL core's bloom: the console
 * falls back to this same drawing without WebGL, so the phone matches the console's fallback
 * exactly and its GPU picture closely. The shell's points are batched into [LEVELS] alpha
 * levels, each drawn at its level's size, where canvas2d.ts sizes each point by its own alpha.
 */
class CoreCanvas {
    private class Face {
        var near = FloatArray(ARC * 4)
        var far = FloatArray(ARC * 4)
        var depth = 0.0
        var lit = 1.0
    }

    private var buf = FloatArray(0)
    private var screen = FloatArray(0)
    private val levels = Array(LEVELS) { FloatArray(0) }
    private val counts = IntArray(LEVELS)
    private val faces = RINGS.flatMap { r -> List(r.seg) { Face() } }
    private val order = faces.toMutableList()

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val dots = Paint().apply { strokeCap = Paint.Cap.SQUARE }
    private val lighter = PorterDuffXfermode(PorterDuff.Mode.ADD)
    private val path = Path()

    private val white = CoreEngine.PALETTE.getValue("white")
    private val spark = CoreEngine.PALETTE.getValue("spark")
    private val halo = CoreEngine.PALETTE.getValue("halo")
    private val hot = CoreEngine.PALETTE.getValue("hot")
    private val face = CoreEngine.PALETTE.getValue("face")

    // The view: x right, y up, z toward the viewer; tilted, then seen from CAMERA.
    private var cx = 0.0
    private var cy = 0.0
    private var s = 1.0
    private var ox = 0.0
    private var oy = 0.0
    private var oz = 0.0
    private var os = 1.0

    private fun project(x: Double, y: Double, z: Double) {
        val y1 = y * TC - z * TS
        val z1 = y * TS + z * TC
        val k = CoreEngine.CAMERA / (CoreEngine.CAMERA - z1)
        ox = cx + x * s * k
        oy = cy - y1 * s * k
        oz = z1
        os = k
    }

    // A point on ring ri, in the ring's plane at angle th, radius r, depth z: turned a about X,
    // then b about Y (three's 'YXZ'), scaled by the pulse.
    private var ca = 1.0
    private var sa = 0.0
    private var cb = 1.0
    private var sb = 0.0
    private var pulse = 1.0

    private fun onRing(th: Double, r: Double, z: Double) {
        val x0 = cos(th) * r * pulse
        val y0 = sin(th) * r * pulse
        val z0 = z * pulse
        val y1 = y0 * ca - z0 * sa
        val z1 = y0 * sa + z0 * ca
        project(x0 * cb + z1 * sb, y1, z1 * cb - x0 * sb)
    }

    private fun pose(core: Core, ri: Int) {
        val p = CoreEngine.ringPose(core, ri)
        ca = cos(p.a)
        sa = sin(p.a)
        cb = cos(p.b)
        sb = sin(p.b)
        pulse = p.pulse
    }

    private var px = 0f
    private var py = 0f

    /** A point part way along a face's arc: [base] 0 is the outer arc, ARC the inner. */
    private fun along(pts: FloatArray, base: Int, idx: Double) {
        val o = min(ARC - 2, floor(idx).toInt())
        val t = (idx - o).toFloat()
        val i = (base + o) * 2
        px = pts[i] + (pts[i + 2] - pts[i]) * t
        py = pts[i + 1] + (pts[i + 3] - pts[i + 1]) * t
    }

    private fun poly(pts: FloatArray): Path {
        path.rewind()
        path.moveTo(pts[0], pts[1])
        var i = 2
        while (i < pts.size) {
            path.lineTo(pts[i], pts[i + 1])
            i += 2
        }
        path.close()
        return path
    }

    /** Draw [core] filling [widthPx] x [heightPx], the shell's first [shell].n points. */
    fun draw(canvas: Canvas, core: Core, shell: Shell, widthPx: Float, heightPx: Float, density: Float) {
        val w = max(1.0, (widthPx / density).toDouble())
        val h = max(1.0, (heightPx / density).toDouble())
        if (buf.size < shell.n * 4) {
            buf = FloatArray(shell.n * 4)
            screen = FloatArray(shell.n * 4)
            for (l in 0 until LEVELS) levels[l] = FloatArray(shell.n * 2)
        }
        cx = w / 2
        cy = h / 2
        s = min(w, h) * CoreEngine.SPREAD
        val sc = (w / CoreEngine.BOARD_WIDTH).toFloat()
        canvas.save()
        canvas.scale(density, density)

        CoreEngine.shellInto(core, shell, buf)
        for (i in 0 until shell.n) {
            project(buf[i * 4].toDouble(), buf[i * 4 + 1].toDouble(), buf[i * 4 + 2].toDouble())
            screen[i * 4] = ox.toFloat()
            screen[i * 4 + 1] = oy.toFloat()
            screen[i * 4 + 2] = oz.toFloat()
            // Depth-shaded: the far side faint and fine, as the console's (T-0749).
            val depth = min(1.0, max(0.0, (oz + 1.1) / 2.2))
            screen[i * 4 + 3] = (buf[i * 4 + 3] * (0.06 + 0.94 * depth)).toFloat()
        }
        shellHalf(canvas, core, shell, front = false, sc)
        rings(canvas, core, sc)
        shellHalf(canvas, core, shell, front = true, sc)
        flow(canvas, core, sc)
        heart(canvas, core, sc)
        canvas.restore()
    }

    private fun shellHalf(canvas: Canvas, core: Core, sh: Shell, front: Boolean, sc: Float) {
        counts.fill(0)
        for (i in 0 until sh.n) {
            if ((screen[i * 4 + 2] >= 0) != front || sh.hot[i]) continue
            val level = min(LEVELS - 1, floor(screen[i * 4 + 3] * LEVELS).toInt())
            val at = counts[level]
            levels[level][at] = screen[i * 4]
            levels[level][at + 1] = screen[i * 4 + 1]
            counts[level] = at + 2
        }
        dots.xfermode = lighter
        for (level in 0 until LEVELS) {
            if (counts[level] == 0) continue
            val a = (level + 0.5) / LEVELS
            dots.color = argb(core.look.color, a * 0.9)
            dots.strokeWidth = ((0.4 + 1.5 * a) * sc).toFloat()
            canvas.drawPoints(levels[level], 0, counts[level], dots)
        }
        fill.xfermode = lighter
        for (i in 0 until sh.n) {
            if (!sh.hot[i] || (screen[i * 4 + 2] >= 0) != front) continue
            fill.color = argb(hot, screen[i * 4 + 3].toDouble())
            square(canvas, screen[i * 4], screen[i * 4 + 1], 2.4f * sc)
        }
    }

    private fun square(canvas: Canvas, x: Float, y: Float, size: Float) =
        canvas.drawRect(x - size / 2, y - size / 2, x + size / 2, y + size / 2, fill)

    private fun rings(canvas: Canvas, core: Core, sc: Float) {
        var f = 0
        RINGS.forEachIndexed { ri, r ->
            pose(core, ri)
            val half = r.depth / 2
            val span = PI * 2 / r.seg
            val arc = (span - r.gap) / 2
            val r0 = r.r - r.inner
            val r1 = r.r + r.outer
            for (k in 0 until r.seg) {
                val fc = faces[f++]
                val mid = core.spinA * r.dir + k * span
                var zFront = 0.0
                var zBack = 0.0
                for (side in 0 until 2) {
                    val pts = if (side == 1) fc.far else fc.near
                    val z = if (side == 1) -half else half
                    for (j in 0 until ARC) {
                        onRing(mid - arc + (j.toDouble() / (ARC - 1)) * arc * 2, r1, z)
                        pts[j * 2] = ox.toFloat()
                        pts[j * 2 + 1] = oy.toFloat()
                        val zo = oz
                        onRing(mid + arc - (j.toDouble() / (ARC - 1)) * arc * 2, r0, z)
                        pts[ARC * 2 + j * 2] = ox.toFloat()
                        pts[ARC * 2 + j * 2 + 1] = oy.toFloat()
                        if (side == 1) zBack += zo + oz else zFront += zo + oz
                    }
                }
                // Whichever face is nearer the viewer is the lit one.
                if (zBack > zFront) {
                    val t = fc.near
                    fc.near = fc.far
                    fc.far = t
                }
                fc.depth = (zFront + zBack) / (ARC * 4)
                fc.lit = CoreEngine.segmentLit(core, k)
            }
        }
        order.sortBy { it.depth }
        val color = core.look.color
        stroke.strokeWidth = max(1f, 1.1f * sc)
        for (fc in order) {
            fill.xfermode = null
            fill.color = argb(face, 1.0)
            canvas.drawPath(poly(fc.far), fill)
            canvas.drawPath(poly(fc.near), fill)
            stroke.xfermode = lighter
            stroke.color = argb(color, 0.25 * fc.lit)
            canvas.drawPath(poly(fc.far), stroke)
            stroke.color = argb(color, min(1.0, 0.85 * fc.lit))
            canvas.drawPath(poly(fc.near), stroke)
            // The windings: short strokes across the lit face, outer arc to inner.
            stroke.color = argb(color, 0.3 * fc.lit)
            path.rewind()
            for (wnd in 1..Segment.WINDINGS) {
                val u = wnd.toDouble() / (Segment.WINDINGS + 1)
                along(fc.near, 0, u * (ARC - 1))
                path.moveTo(px, py)
                // The inner arc runs the other way round.
                along(fc.near, ARC, (1 - u) * (ARC - 1))
                path.lineTo(px, py)
            }
            canvas.drawPath(path, stroke)
            // The LED: the outer arc's middle, a point of the spark colour.
            val m = (ARC / 2) * 2
            fill.xfermode = lighter
            fill.color = argb(spark, min(1.0, 0.9 * fc.lit))
            square(canvas, fc.near[m], fc.near[m + 1], 2.2f * sc)
        }
    }

    private fun flow(canvas: Canvas, core: Core, sc: Float) {
        val color = core.look.color
        stroke.xfermode = lighter
        fill.xfermode = lighter
        RINGS.forEachIndexed { ri, r ->
            pose(core, ri)
            val rr = r.r + r.outer + Flow.LIFT
            // The orbit: a faint full circle the flow runs on.
            stroke.strokeWidth = max(1f, 0.8f * sc)
            stroke.color = argb(color, 0.12)
            path.rewind()
            for (j in 0..ORBIT) {
                onRing(j.toDouble() / ORBIT * PI * 2, rr, 0.0)
                if (j == 0) path.moveTo(ox.toFloat(), oy.toFloat()) else path.lineTo(ox.toFloat(), oy.toFloat())
            }
            canvas.drawPath(path, stroke)
            val back = sign(r.dir) * Flow.TRAIL
            stroke.color = argb(color, 0.35)
            path.rewind()
            for (i in 0 until Flow.DOTS) {
                val th = CoreEngine.flowAngle(core, ri, i)
                onRing(th - back, rr, 0.0)
                path.moveTo(ox.toFloat(), oy.toFloat())
                onRing(th, rr, 0.0)
                path.lineTo(ox.toFloat(), oy.toFloat())
            }
            canvas.drawPath(path, stroke)
            for (i in 0 until Flow.DOTS) {
                onRing(CoreEngine.flowAngle(core, ri, i), rr, 0.0)
                fill.color = argb(spark, 0.45 + 0.4 * max(0.0, oz))
                square(canvas, ox.toFloat(), oy.toFloat(), (1.8 * sc * os).toFloat())
            }
            // The comet: a head and a fading tail, running against the flow.
            val head = CoreEngine.cometAngle(core, ri)
            for (j in 0 until Flow.COMET) {
                onRing(head + j * 0.03 * sign(r.dir), rr, 0.0)
                fill.color = argb(white, 0.9 * (1 - j.toDouble() / Flow.COMET))
                square(canvas, ox.toFloat(), oy.toFloat(), ((2.6 - 1.6 * j / Flow.COMET) * sc).toFloat())
            }
            if (ri == 0) {
                // The struts: ring 0 held to the core, between its segments.
                stroke.color = argb(color, 0.45)
                path.rewind()
                val span = PI * 2 / r.seg
                for (st in 0 until 4) {
                    val th = core.spinA * r.dir + span / 2 + st * PI / 2
                    onRing(th, CoreEngine.STRUT_IN, 0.0)
                    path.moveTo(ox.toFloat(), oy.toFloat())
                    onRing(th, r.r - r.inner, 0.0)
                    path.lineTo(ox.toFloat(), oy.toFloat())
                }
                canvas.drawPath(path, stroke)
            }
        }
    }

    private fun heart(canvas: Canvas, core: Core, sc: Float) {
        val color = core.look.color
        val g = CoreEngine.coreGlow(core)
        val r0 = (g.radius * s).toFloat()
        val glow = min(1.0, g.glow)
        val x = cx.toFloat()
        val y = cy.toFloat()
        fill.xfermode = lighter
        // A shader is multiplied by the paint's own alpha, which the faces left low:
        // Canvas2D's gradients carry their alpha in their stops alone.
        fill.alpha = 255
        fill.shader = RadialGradient(
            x, y, max(r0 * 4, 0.01f),
            intArrayOf(argb(white, glow), argb(halo, 0.55 * glow), argb(color, 0.2 * min(1.5, g.glow)), argb(color, 0.0)),
            floatArrayOf(0f, 0.22f, 0.5f, 1f),
            Shader.TileMode.CLAMP,
        )
        canvas.drawRect(x - r0 * 4, y - r0 * 4, x + r0 * 4, y + r0 * 4, fill)
        fill.shader = RadialGradient(
            x, y, max(r0, 0.01f),
            intArrayOf(argb(white, 1.0), argb(spark, 0.8 * glow), argb(color, 0.0)),
            floatArrayOf(0f, 0.7f, 1f),
            Shader.TileMode.CLAMP,
        )
        canvas.drawRect(x - r0, y - r0, x + r0, y + r0, fill)
        fill.shader = null
        // The wire sphere: three great circles round the core, turning.
        stroke.xfermode = lighter
        stroke.strokeWidth = max(1f, 0.8f * sc)
        stroke.color = argb(halo, 0.35)
        val rw = r0 * 1.35f
        for (i in 0 until 3) {
            val turn = i * PI / 3
            val ry = rw * abs(cos(core.spinA * 0.7 + turn)).toFloat()
            canvas.save()
            canvas.rotate(Math.toDegrees(turn).toFloat(), x, y)
            canvas.drawOval(x - rw, y - ry, x + rw, y + ry, stroke)
            canvas.restore()
        }
        for (f in CoreEngine.shocks(core, core.t)) {
            val ring = CoreEngine.shockRing(f)
            stroke.strokeWidth = max(1f, (ring.width * sc).toFloat())
            stroke.color = argb(color, ring.alpha)
            canvas.drawCircle(x, y, (ring.radius * s).toFloat(), stroke)
        }
    }

    companion object {
        /** Alpha levels the shell's points are batched into: one paint per level, not per point. */
        const val LEVELS = 8
        /** Points along each arc of a segment face. */
        private const val ARC = 5
        /** Steps round an orbit. */
        private const val ORBIT = 48
        private val TC = cos(CoreEngine.TILT)
        private val TS = sin(CoreEngine.TILT)

        /** Colour [c] (0..255 each, a token's) at alpha [a], as `paint` writes `#rrggbbaa`. */
        fun argb(c: DoubleArray, a: Double): Int {
            fun b(v: Double) = min(255.0, max(0.0, v)).roundToInt()
            return (b(a * 255) shl 24) or (b(c[0]) shl 16) or (b(c[1]) shl 8) or b(c[2])
        }
    }
}
