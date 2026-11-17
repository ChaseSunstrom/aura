package ai.jarvis.app.assist.island

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisTokens.Material.Floating
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader

/**
 * The core alone at the bottom as an orb (T-0968; the owner, 2026-11-10: "clean up the orb make
 * it look nicer and more depth and like an actual orb not just a circle"). A sphere lit from the
 * top left in the worn theme's light: its soft glow and the shadow it casts, its body (her space
 * under AURA, the island's ground otherwise) shading to its unlit side, then, over the core, a
 * rim and a specular highlight. Built once at unit size and drawn scaled, so a frame of the grow
 * allocates nothing. Does not cover the core inside it (the hosted reactor) or its motion.
 */
internal class IslandOrb(private val space: IslandSpace?) {
    /** The body's radius as a share of the glow's. */
    val body = Floating.ORB_DP.toFloat() / Floating.GLOW_DP

    private val ground = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = ActiveTheme.tint(Floating.FILL, "bg.panel", "bg.deep") }
    private val accent = ActiveTheme.tint(Floating.EDGE, "accent")
    private val shade = ActiveTheme.tint(Floating.ORB_SHADE, "bg.deep", "bg")
    private val rim = ActiveTheme.tint(Floating.ORB_RIM, "accent")
    private val light = ActiveTheme.tint(Floating.ORB_LIGHT, "text")

    // At unit size: 1 is the body's radius.
    private val halo = radial(0f, 0f, 1f / body, intArrayOf(accent, accent, clear(accent)), floatArrayOf(0f, HALO_FROM, 1f))
    private val shadow = radial(0f, 0f, 1f, intArrayOf(shade, clear(shade)), floatArrayOf(0f, 1f))
    private val lit = radial(LIGHT_X, LIGHT_Y, LIT_REACH, intArrayOf(accent, clear(accent), shade), LIT_STOPS)
    private val edge = radial(0f, 0f, 1f, intArrayOf(clear(rim), clear(rim), rim), RIM_STOPS)
    private val gleam = radial(0f, 0f, 1f, intArrayOf(light, clear(light)), floatArrayOf(0f, 1f))
    private val rect = RectF()

    /** The glow, the shadow and the shaded body, under the core, centred on ([cx], [cy]) in a glow [glow] px across its radius. */
    fun under(canvas: Canvas, cx: Float, cy: Float, glow: Float) {
        val r = glow * body
        canvas.save()
        canvas.translate(cx, cy)
        canvas.scale(r, r)
        canvas.drawCircle(0f, 0f, 1f / body, halo)
        canvas.save()
        canvas.translate(0f, SHADOW_DROP)
        canvas.scale(SHADOW_W, SHADOW_H)
        canvas.drawCircle(0f, 0f, 1f, shadow)
        canvas.restore()
        canvas.restore()
        rect.set(cx - r, cy - r, cx + r, cy + r)
        if (space != null) space.draw(canvas, rect, r) else canvas.drawCircle(cx, cy, r, ground)
        canvas.save()
        canvas.translate(cx, cy)
        canvas.scale(r, r)
        canvas.drawCircle(0f, 0f, 1f, lit)
        canvas.restore()
    }

    /** The rim and the highlight, over the core: the glass in front of it. */
    fun over(canvas: Canvas, cx: Float, cy: Float, glow: Float) {
        val r = glow * body
        canvas.save()
        canvas.translate(cx, cy)
        canvas.scale(r, r)
        canvas.drawCircle(0f, 0f, 1f, edge)
        canvas.translate(LIGHT_X, LIGHT_Y)
        canvas.rotate(GLEAM_TILT)
        canvas.scale(GLEAM_W, GLEAM_H)
        canvas.drawCircle(0f, 0f, 1f, gleam)
        canvas.restore()
    }

    private companion object {
        /** Where the light comes from, in body radii from its centre: the top left. */
        const val LIGHT_X = -0.38f
        const val LIGHT_Y = -0.46f
        /** How far from the light the shading runs to its darkest, in body radii. */
        const val LIT_REACH = 1.65f
        val LIT_STOPS = floatArrayOf(0f, 0.35f, 0.9f)
        /** The rim: clear to most of the way out, then the edge catching the light. */
        val RIM_STOPS = floatArrayOf(0f, 0.86f, 1f)
        /** The glow begins just inside the body's edge, as a share of its own reach. */
        const val HALO_FROM = 0.5f
        /** The cast shadow: a flattened disc a little below the body. */
        const val SHADOW_DROP = 0.9f
        const val SHADOW_W = 0.82f
        const val SHADOW_H = 0.26f
        /** The highlight: a soft ellipse leaning with the curve. */
        const val GLEAM_TILT = -32f
        const val GLEAM_W = 0.42f
        const val GLEAM_H = 0.24f

        fun clear(color: Int) = color and 0x00FFFFFF

        fun radial(x: Float, y: Float, r: Float, colors: IntArray, stops: FloatArray) =
            Paint(Paint.ANTI_ALIAS_FLAG).apply { shader = RadialGradient(x, y, r, colors, stops, Shader.TileMode.CLAMP) }
    }
}
