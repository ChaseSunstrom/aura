package ai.aura.app.car.holo

import ai.aura.app.ui.JarvisUi
import ai.aura.app.ui.theme.JarvisTokens.Color
import ai.aura.app.ui.theme.JarvisTokens.Tracking
import android.graphics.BlendMode
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.Shader
import androidx.core.graphics.ColorUtils
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * Replays a [HoloFrame] onto a canvas the way Car.dc.html's `startMap` paints its own:
 * the sky gradient, the horizon band, then every line, footprint and glow added as light
 * (`lighter`), then the two street names over a dark backing.
 *
 * Nothing is allocated per frame: the paints, the path, the glow shaders and their matrix
 * are made once, and the gradients that depend on the surface's size are remade only in
 * [resize]. That matters because this runs on every vsync the drive is on screen.
 *
 * Does not cover a canvas that cannot blend additively; every hardware canvas on the
 * app's minimum API can.
 */
class HoloPainter(val look: MapLook = MapLook.HOLOGRAM) {
    private val inks = look.inks
    private val stars = if (look.style == MapLook.SPACE_DRIVE) StarSky(look) else null
    private var width = 0f
    private var height = 0f
    private var unit = 1f

    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        blendMode = BlendMode.PLUS
    }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        blendMode = BlendMode.PLUS
    }
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
        blendMode = BlendMode.PLUS
    }
    /** The solid world (T-1060): opaque, over what is behind it, never added as light. */
    private val solid = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val path = Path()

    private val sky = Paint()
    /** The daylight the sky was last made for, in [SKY_STEPS]ths; -1 before the first. */
    private var skyStep = -1
    private var horizonAt = 0f
    private val daySky = Paint()
    private val dayGround = Paint()
    private val horizon = Paint().apply { blendMode = BlendMode.PLUS }
    private val vignetteX = Paint()
    private val vignetteY = Paint()

    /** One unit-radius gradient per ink, moved and scaled into place by [glowMatrix]. */
    private val glowShaders = Array(inks.size) {
        RadialGradient(0f, 0f, 1f, inks[it], ColorUtils.setAlphaComponent(inks[it], 0), Shader.TileMode.CLAMP)
    }
    private val glowMatrix = Matrix()
    private val glow = Paint().apply { blendMode = BlendMode.PLUS }

    private val backing = Paint()
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.LABEL_FACE
        textAlign = Paint.Align.CENTER
        letterSpacing = Tracking.CONTROL
    }

    fun resize(width: Float, height: Float, horizonY: Float, unit: Float) {
        this.width = width
        this.height = height
        this.unit = unit
        // The board's sky: dark at the top, a lit band where the city meets it, dark again.
        // The space drive's sky is its pack's, down to the horizon (T-0871).
        stars?.resize(width, horizonY, unit)
        sky.shader = stars?.shader(height, horizonY) ?: LinearGradient(
            0f, 0f, 0f, height,
            look.sky,
            floatArrayOf(0f, 0.36f, 0.45f, 0.6f, 1f),
            Shader.TileMode.CLAMP,
        )
        horizonAt = horizonY
        val step = max(0, skyStep)
        skyStep = -1
        light(step.toFloat() / SKY_STEPS)
        val glowInk = inks[Ink.ACCENT]
        horizon.shader = LinearGradient(
            0f, horizonY - HORIZON_ABOVE * unit, 0f, horizonY + HORIZON_BELOW * unit,
            intArrayOf(clear(glowInk), ColorUtils.setAlphaComponent(glowInk, a(0.18f)), clear(glowInk)),
            floatArrayOf(0f, 0.6f, 1f),
            Shader.TileMode.CLAMP,
        )
        // Darker at the edges, where the zones sit, so the words are never on bright city.
        vignetteX.shader = LinearGradient(
            0f, 0f, width, 0f,
            intArrayOf(shade(0.55f), clear(Color.BG), clear(Color.BG), shade(0.6f)),
            floatArrayOf(0f, 0.26f, 0.6f, 1f),
            Shader.TileMode.CLAMP,
        )
        vignetteY.shader = LinearGradient(
            0f, height, 0f, 0f,
            intArrayOf(shade(0.6f), clear(Color.BG)),
            floatArrayOf(0f, 0.3f),
            Shader.TileMode.CLAMP,
        )
    }

    /**
     * Light the sky for [day] (0 in the dark, 1 in full day; T-1107). The look's own sky is
     * the night one; daylight is laid over it down to the horizon (a blue over a pale horizon,
     * the horizon warm while the sun is on it), hiding the stars as it comes, and the ground
     * under the horizon is lifted as the world standing on it is. Remade only when the light
     * has moved a step, which is minutes apart.
     */
    fun light(day: Float) {
        val step = (day.coerceIn(0f, 1f) * SKY_STEPS).roundToInt()
        if (step == skyStep || height <= 0f) return
        skyStep = step
        val d = step.toFloat() / SKY_STEPS
        val low = HoloWorld.mix(Color.WORLD_SKY_DAY_HORIZON, Color.WORLD_SKY_DUSK, 4f * d * (1f - d) * DUSK_GLOW)
        daySky.shader = LinearGradient(
            0f, 0f, 0f, horizonAt,
            intArrayOf(Color.WORLD_SKY_DAY, HoloWorld.mix(Color.WORLD_SKY_DAY, low, 0.5f), low),
            floatArrayOf(0f, 0.7f, 1f),
            Shader.TileMode.CLAMP,
        )
        daySky.alpha = a(d)
        dayGround.color = ColorUtils.setAlphaComponent(Color.PAPER, a(d * HoloWorld.DAY_LIFT))
    }

    /** The sky alone: what shows before the first fix, and under everything after it. */
    fun background(canvas: Canvas, horizonY: Float, timeMs: Long = 0L) {
        canvas.drawRect(0f, 0f, width, height, sky)
        stars?.draw(canvas, timeMs)
        if (skyStep > 0) {
            canvas.drawRect(0f, 0f, width, horizonY, daySky)
            canvas.drawRect(0f, horizonY, width, height, dayGround)
        }
        canvas.drawRect(0f, horizonY - HORIZON_ABOVE * unit, width, horizonY + HORIZON_BELOW * unit, horizon)
    }

    fun paint(canvas: Canvas, f: HoloFrame, timeMs: Long = 0L) {
        background(canvas, f.horizonY, timeMs)
        label.clearShadowLayer()
        for (i in 0 until f.solidCount) {
            val p = f.solidOrder[i]
            path.rewind()
            val start = f.solidStart[p]
            for (k in 0 until f.solidLen[p]) {
                val x = f.pts[2 * (start + k)]
                val y = f.pts[2 * (start + k) + 1]
                if (k == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            path.close()
            solid.color = f.solidColor[p]
            canvas.drawPath(path, solid)
            // What a sign says (T-1104): flat on its plate, with no glow and no backing of its
            // own, and in the plate's place in the order, so a nearer wall hides both (T-1120).
            var s = f.solidSign[p]
            while (s >= 0) {
                label.textSize = f.signPx[s]
                label.color = f.signColor[s]
                f.signText[s]?.let { canvas.drawText(it, f.signX[s], f.signY[s], label) }
                s = f.signNext[s]
            }
        }
        for (i in 0 until f.lineCount) {
            val o = i * HoloFrame.LINE_STRIDE
            line.color = ColorUtils.setAlphaComponent(inks[f.lineInk[i]], a(f.lines[o + 4]))
            line.strokeWidth = f.lines[o + 5]
            canvas.drawLine(f.lines[o], f.lines[o + 1], f.lines[o + 2], f.lines[o + 3], line)
        }
        for (p in 0 until f.polyCount) {
            path.rewind()
            val start = f.polyStart[p]
            for (k in 0 until f.polyLen[p]) {
                val x = f.pts[2 * (start + k)]
                val y = f.pts[2 * (start + k) + 1]
                if (k == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            path.close()
            if (f.polyFill[p] > 0f) {
                fill.color = ColorUtils.setAlphaComponent(inks[f.polyFillInk[p]], a(f.polyFill[p]))
                canvas.drawPath(path, fill)
            }
            if (f.polyStroke[p] > 0f) {
                stroke.color = ColorUtils.setAlphaComponent(inks[f.polyStrokeInk[p]], a(f.polyStroke[p]))
                stroke.strokeWidth = f.polyWidth[p]
                canvas.drawPath(path, stroke)
            }
        }
        for (g in 0 until f.glowCount) {
            val r = f.glowR[g]
            glowMatrix.setScale(r, r)
            glowMatrix.postTranslate(f.glowX[g], f.glowY[g])
            val shader = glowShaders[f.glowInk[g]]
            shader.setLocalMatrix(glowMatrix)
            glow.shader = shader
            glow.alpha = a(f.glowAlpha[g])
            canvas.drawRect(f.glowX[g] - r, f.glowY[g] - r, f.glowX[g] + r, f.glowY[g] + r, glow)
        }
        for (l in 0 until f.labelCount) {
            val text = f.labelText[l] ?: continue
            val px = f.labelPx[l]
            val alpha = f.labelAlpha[l]
            val ink = inks[f.labelInk[l]]
            label.textSize = px
            val w = label.measureText(text)
            val x = f.labelX[l]
            val y = f.labelY[l]
            // Upright, on a dark plate, so a name reads over any amount of city behind it.
            backing.color = ColorUtils.setAlphaComponent(Color.BG, a(0.55f * alpha))
            canvas.drawRect(x - w / 2 - px * 0.4f, y - px * 1.25f, x + w / 2 + px * 0.4f, y + px * 0.15f, backing)
            label.color = ColorUtils.setAlphaComponent(ink, a(alpha))
            label.setShadowLayer(LABEL_GLOW * unit, 0f, 0f, ColorUtils.setAlphaComponent(ink, a(0.8f * alpha)))
            canvas.drawText(text, x, y - px * 0.2f, label)
        }
    }

    /**
     * The edge shading, last over the scene and under the zones, at [strength]: 1 as the night
     * drive has it; the flat map fades it by day (T-1574), where on a light map it read as dirt.
     */
    fun vignette(canvas: Canvas, strength: Float = 1f) {
        vignetteX.alpha = a(strength)
        vignetteY.alpha = a(strength)
        canvas.drawRect(0f, 0f, width, height, vignetteX)
        canvas.drawRect(0f, 0f, width, height, vignetteY)
    }

    private fun shade(alpha: Float) = ColorUtils.setAlphaComponent(Color.BG, a(alpha))

    private fun clear(color: Int) = ColorUtils.setAlphaComponent(color, 0)

    private fun a(alpha: Float) = (alpha.coerceIn(0f, 1f) * 255f + 0.5f).toInt()

    companion object {
        /** The board's horizon band, in its pixels either side of the horizon. */
        private const val HORIZON_ABOVE = 140f
        private const val HORIZON_BELOW = 80f
        /** The sky is remade when the daylight has moved one of this many steps. */
        private const val SKY_STEPS = 48
        /** How far the horizon goes to the dusk colour with the sun on it. */
        private const val DUSK_GLOW = 0.75f

        /** The board's label glow, `shadowBlur = 12`, in its pixels. */
        private const val LABEL_GLOW = 12f
    }
}
