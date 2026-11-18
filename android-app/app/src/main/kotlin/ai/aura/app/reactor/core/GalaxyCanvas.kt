package ai.aura.app.reactor.core

import android.graphics.BlendMode
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RuntimeShader
import android.graphics.Shader
import kotlin.math.max
import kotlin.math.min

/**
 * AURA's galaxy on an [android.graphics.Canvas] (T-0871): the draw list of [GalaxyFrame], in the
 * same order and with the same blends as the console's drawers (`galaxy-draw.ts`): additive
 * ([BlendMode.PLUS], the console's `lighter`) for every run but the dust lanes' source-over.
 *
 * The soft blobs (nebulae, knots, the bulge) are an AGSL [RuntimeShader], the console's fragment
 * falloff `a * max(0, 1 - d / r)`. On a software canvas (a bitmap, the goldens), where AGSL
 * will not build, or with [useShader] off, a radial gradient from the colour to clear draws the same falloff, as the
 * console's Canvas2D drawer does: the two agree to the antialiasing.
 *
 * Does not cover a lost surface or a frame budget: one star is one draw call, so the phone's
 * 1,200 stars cost what [CoreCanvas]'s 1,500 shell points cost (T-0712's budget).
 */
class GalaxyCanvas(useShader: Boolean = true) {
    private val frame = GalaxyFrame()
    private val list = GalaxyDrawList()
    private val glow: RuntimeShader? =
        if (useShader) runCatching { RuntimeShader(GLOW) }.getOrNull() else null
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.BUTT
    }
    private val blob = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }

    /** The fallback's gradients, one per colour at unit radius, moved by [unitMatrix]: no allocation a frame. */
    private val unitGlow = HashMap<Int, RadialGradient>()
    private val unitMatrix = Matrix()

    /** Whether the AGSL glow built: false under Robolectric and on a device that refuses it. */
    val shaded get() = glow != null

    /** Draw [g] at ([x], [y]) of a [widthPx] by [heightPx] surface, its disc [scale] of the board's. */
    fun draw(canvas: Canvas, g: Galaxy, widthPx: Float, heightPx: Float, x: Float = 0.5f, y: Float = 0.5f, scale: Float = 1f) {
        frame.build(g, list, widthPx, heightPx, x, y, scale)
        val d = list.data
        for (b in 0 until list.batches) {
            val mode = if (list.batchOver[b]) BlendMode.SRC_OVER else BlendMode.PLUS
            fill.blendMode = mode
            stroke.blendMode = mode
            blob.blendMode = mode
            val start = list.batchStart[b]
            for (i in start until start + list.batchCount[b]) {
                val o = i * GalaxyDrawList.STRIDE
                val x0 = d[o]
                val y0 = d[o + 1]
                val w = d[o + 4]
                val a0 = d[o + 9]
                when (d[o + 5].toInt()) {
                    GalaxyDrawList.LINE -> line(canvas, d, o, x0, y0, w, a0)
                    GalaxyDrawList.BLOB -> blob(canvas, d, o, x0, y0, w, a0)
                    else -> {
                        fill.color = argb(d, o, a0)
                        canvas.drawRect(x0 - w, y0 - w, x0 + w, y0 + w, fill)
                    }
                }
            }
        }
    }

    private fun line(canvas: Canvas, d: FloatArray, o: Int, x0: Float, y0: Float, w: Float, a0: Float) {
        val a1 = d[o + 10]
        val x1 = d[o + 2]
        val y1 = d[o + 3]
        stroke.strokeWidth = w
        if (a0 == a1) {
            stroke.shader = null
            stroke.color = argb(d, o, a0)
        } else {
            // A fading beam or spike: four a frame, so a gradient each is cheap.
            stroke.shader = LinearGradient(x0, y0, x1, y1, argb(d, o, a0), argb(d, o, a1), Shader.TileMode.CLAMP)
            // A paint's own alpha multiplies its shader (T-0712): the last line's colour left one.
            stroke.alpha = OPAQUE
        }
        canvas.drawLine(x0, y0, x1, y1, stroke)
    }

    private fun blob(canvas: Canvas, d: FloatArray, o: Int, x: Float, y: Float, r: Float, a: Float) {
        if (r <= 0f || a <= 0f) return
        // A software canvas (a bitmap, Robolectric's goldens) throws on a RuntimeShader.
        val shader = if (canvas.isHardwareAccelerated) glow else null
        if (shader != null) {
            shader.setFloatUniform("centre", x, y)
            shader.setFloatUniform("radius", r)
            shader.setFloatUniform("tone", clamp(d[o + 6]), clamp(d[o + 7]), clamp(d[o + 8]), clamp(a))
            blob.shader = shader
            blob.alpha = OPAQUE
        } else {
            val key = argb(d, o, 1f)
            // A state's tint tweens the colours, so every frame of a change is new keys: bounded.
            if (unitGlow.size > MAX_GLOWS && key !in unitGlow) unitGlow.clear()
            val unit = unitGlow.getOrPut(key) {
                RadialGradient(0f, 0f, 1f, key, argb(d, o, 0f), Shader.TileMode.CLAMP)
            }
            unitMatrix.setScale(r, r)
            unitMatrix.postTranslate(x, y)
            unit.setLocalMatrix(unitMatrix)
            blob.shader = unit
            blob.alpha = (clamp(a) * OPAQUE).toInt()
        }
        canvas.drawCircle(x, y, r, blob)
    }

    private companion object {
        /** Paint alpha's full scale. */
        const val OPAQUE = 255

        /** The fallback's cached gradients before it starts again (a few frames of a tween). */
        const val MAX_GLOWS = 256

        /** The console's blob fragment (`galaxy-draw.ts`), premultiplied: the tone carries the alpha in w. */
        const val GLOW = """
uniform float2 centre;
uniform float radius;
uniform half4 tone;
half4 main(float2 p) {
    half a = tone.a * half(max(0.0, 1.0 - length(p - centre) / max(radius, 0.001)));
    return half4(tone.rgb * a, a);
}
"""

        fun clamp(v: Float) = min(1f, max(0f, v))

        /** The list's colour at [o] (0..1 each) at alpha [a], clamped: an additive run can ask for more than one. */
        fun argb(d: FloatArray, o: Int, a: Float): Int =
            Color.argb(clamp(a), clamp(d[o + 6]), clamp(d[o + 7]), clamp(d[o + 8]))
    }
}
