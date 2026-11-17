package ai.jarvis.app.assist.island

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisThemes
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import kotlin.math.ceil
import kotlin.random.Random

/**
 * The island's ground under a pack whose ground is a starfield (AURA, T-0898; the owner,
 * 2026-11-09: "the nice glass effect with the space stuff from the core screen"): her void,
 * the pack's nebulae and a seeded field of stars, as the console's Starfield draws them.
 * Opaque, like the console's island: the app behind it never shows through. [of] is null for
 * any other ground, and the island keeps its flat fill.
 *
 * The stars are a fixed sky round the island's centre that its shape reveals, so they hold
 * still while it morphs instead of re-seeding every frame.
 *
 * The v2 `Stage` draws the same space behind a native screen (T-0949): a shape wider or taller
 * than one field is tiled with further fields, each seeded from where it sits, so the sky does
 * not repeat down a long screen and the island's own field is the one it always was.
 *
 * Does not cover: the twinkle (the island draws for its own motion, not ten times a second),
 * or stars past [MAX_REACH] fields from the centre (a shape over ~19 000 dp).
 */
internal class IslandSpace private constructor(
    voidColor: Int,
    starColor: Int,
    nebulae: List<JarvisThemes.Nebula>,
    private val density: Float,
) {
    private val void = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = voidColor }
    private val star = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = starColor }
    // CSS radial-gradient(<w> <h> at x y, colour, transparent 70%): a unit circle scaled to the radii.
    private val washes = nebulae.mapNotNull { n ->
        ActiveTheme.parseArgb(n.color)?.let { argb ->
            n to Paint(Paint.ANTI_ALIAS_FLAG).apply {
                shader = RadialGradient(0f, 0f, 1f, intArrayOf(argb, argb and 0x00FFFFFF), NEBULA_STOPS, Shader.TileMode.CLAMP)
            }
        }
    }
    // The fields seeded so far, by where they sit: a redraw allocates nothing.
    private val fields = arrayOfNulls<FloatArray>(SPAN * SPAN)

    // x, y (dp from the field's centre), radius (dp), alpha: one star per four floats. The
    // field at 0, 0 is seeded by SEED alone, the sky the island has always drawn.
    private fun field(ix: Int, iy: Int): FloatArray {
        val at = (ix + MAX_REACH) * SPAN + iy + MAX_REACH
        fields[at]?.let { return it }
        val r = Random(SEED + ix * SEED_X + iy * SEED_Y)
        return FloatArray(STARS * 4).also { a ->
            for (i in 0 until STARS) {
                a[i * 4] = (r.nextFloat() - 0.5f) * FIELD_DP
                a[i * 4 + 1] = (r.nextFloat() - 0.5f) * FIELD_DP
                a[i * 4 + 2] = STAR_MIN_DP + r.nextFloat() * (STAR_MAX_DP - STAR_MIN_DP)
                a[i * 4 + 3] = STAR_MIN_ALPHA + r.nextFloat() * (STAR_MAX_ALPHA - STAR_MIN_ALPHA)
            }
            fields[at] = a
        }
    }
    private val clip = Path()

    fun draw(canvas: Canvas, shape: RectF, radius: Float) {
        canvas.drawRoundRect(shape, radius, radius, void)
        clip.rewind()
        clip.addRoundRect(shape, radius, radius, Path.Direction.CW)
        canvas.save()
        canvas.clipPath(clip)
        for ((n, paint) in washes) {
            canvas.save()
            canvas.translate(shape.left + n.x * shape.width(), shape.top + n.y * shape.height())
            canvas.scale(n.w * density, n.h * density)
            canvas.drawCircle(0f, 0f, 1f, paint)
            canvas.restore()
        }
        // Fields either side of the centre one until the shape is covered: none for an island.
        val side = FIELD_DP * density
        val nx = reach(shape.width(), side)
        val ny = reach(shape.height(), side)
        for (iy in -ny..ny) for (ix in -nx..nx) {
            val sky = field(ix, iy)
            val cx = shape.centerX() + ix * side
            val cy = shape.centerY() + iy * side
            for (i in 0 until STARS) {
                star.alpha = sky[i * 4 + 3].toInt()
                canvas.drawCircle(cx + sky[i * 4] * density, cy + sky[i * 4 + 1] * density, sky[i * 4 + 2] * density, star)
            }
        }
        canvas.restore()
    }

    // ponytail: every field of a shape is drawn, seen or not, up to MAX_REACH each way (a
    // 16 000 dp audit window is 35 fields); cull to the canvas clip if a frame profile asks.
    private fun reach(extent: Float, side: Float): Int =
        if (side <= 0f || extent <= side) 0 else ceil((extent / side - 1f) / 2f).toInt().coerceAtMost(MAX_REACH)

    companion object {
        private val NEBULA_STOPS = floatArrayOf(0f, 0.7f)
        private const val SEED = 0x5A7
        // Prime strides, the second past MAX_REACH times the first: no two fields share a seed.
        private const val SEED_X = 31
        private const val SEED_Y = 977
        private const val MAX_REACH = 20
        private const val SPAN = MAX_REACH * 2 + 1
        // A 480 dp sky, wider than any island, at about the console's density (one per ~1200 dp²).
        private const val FIELD_DP = 480f
        private const val STARS = 192
        private const val STAR_MIN_DP = 0.4f
        private const val STAR_MAX_DP = 1.1f
        private const val STAR_MIN_ALPHA = 90f
        private const val STAR_MAX_ALPHA = 230f

        /** The pack's space, or null when its ground is not a starfield (or it names no void). */
        fun of(theme: JarvisThemes.Theme, density: Float): IslandSpace? {
            if (theme.ground != "starfield") return null
            val void = ActiveTheme.parse(theme.colors["bg.void"]) ?: return null
            val text = ActiveTheme.parse(theme.colors["text"]) ?: return null
            return IslandSpace(void, text, theme.nebulae, density)
        }
    }
}
