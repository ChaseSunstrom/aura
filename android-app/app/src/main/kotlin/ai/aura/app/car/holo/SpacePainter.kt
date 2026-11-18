package ai.aura.app.car.holo

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.ui.theme.JarvisThemes
import ai.aura.app.ui.theme.JarvisTokens.Color
import android.graphics.BlendMode
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import androidx.core.graphics.ColorUtils
import java.util.Random
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.sin

/**
 * How the drive map is coloured, chosen by the persona's pack `map.style` (T-0871): JARVIS's
 * hologram in its tokens, or AURA's space drive, every colour read from her pack's `map`
 * (sky and ground gradients, buildings near and far, beacons, lanes, the route). [stars] is the
 * pack's `sky.stars`; [band] its `sky.band` (a milky way across the sky).
 *
 * Does not cover the space drive's own shapes (crystal buildings, a comet route, a planet turn
 * marker, orb vehicles): the scene's geometry is the hologram's, recoloured.
 */
class MapLook private constructor(
    val style: String,
    /** [Ink] to colour, indexed by the ink's number. */
    val inks: IntArray,
    /** Top to horizon, then horizon to bottom. */
    val sky: IntArray,
    val ground: IntArray?,
    val stars: Int,
    val band: Boolean,
) {
    companion object {
        const val SPACE_DRIVE = "space-drive"

        val HOLOGRAM = MapLook(
            "hologram",
            intArrayOf(Color.ACCENT, Color.TEXT_BRIGHT, Color.WARN, Color.DANGER, Color.ACCENT_LIFT, Color.ACCENT, Color.TEXT_BRIGHT),
            intArrayOf(Color.BG, Color.BG_RAISED, Color.CORE_GLOW, Color.BG_RAISED, Color.BG),
            null,
            0,
            false,
        )

        /** One look per pack, so asking every frame allocates nothing and compares by identity. */
        private val looks = ConcurrentHashMap<String, MapLook>()

        /** The look [theme]'s pack names: the hologram unless its `map.style` is the space drive. */
        fun of(theme: JarvisThemes.Theme = ActiveTheme.theme): MapLook =
            if (theme.mapStyle != SPACE_DRIVE) HOLOGRAM else looks.getOrPut(theme.id) { space(theme) }

        private fun space(theme: JarvisThemes.Theme): MapLook {
            val map = theme.map
            val h = HOLOGRAM.inks
            fun ink(key: String, fallback: Int) = colour(map[key]) ?: fallback
            val route = ink("route.color", h[Ink.WARN])
            return MapLook(
                SPACE_DRIVE,
                intArrayOf(
                    ink("buildings.near", h[Ink.ACCENT]),
                    ink("road.lanes", h[Ink.BRIGHT]),
                    route,
                    ink("buildings.beacons", h[Ink.DANGER]),
                    ink("buildings.far", h[Ink.LIFT]),
                    route,
                    ink("road.lanes", h[Ink.ROUTE_CORE]),
                ),
                gradient(map["sky.gradient"]) ?: HOLOGRAM.sky,
                gradient(map["ground.gradient"]),
                map["sky.stars"]?.toIntOrNull()?.coerceIn(0, MAX_STARS) ?: 0,
                map["sky.band"] != null,
            )
        }

        /** `#RRGGBB`, or `#RRGGBB@35%` (the opacity is the scene's own, so it is dropped here). */
        private fun colour(value: String?): Int? = ActiveTheme.parse(value?.substringBefore('@'))

        /** A space-separated list of at least two colours, or null when any is not one. */
        private fun gradient(value: String?): IntArray? {
            val parts = value?.split(' ')?.map { colour(it) ?: return null } ?: return null
            return if (parts.size >= 2) parts.toIntArray() else null
        }

        /** A pack asking for more stars than a frame should draw gets this many. */
        private const val MAX_STARS = 2000
    }
}

/**
 * The space drive's sky (T-0871): the pack's gradient down to the horizon and its ground below,
 * a milky way across it and [MapLook.stars] stars that twinkle on the drive's clock. The same
 * stars every run (a fixed seed), placed again only in [resize].
 */
class StarSky(private val look: MapLook) {
    private val sx = FloatArray(look.stars)
    private val sy = FloatArray(look.stars)
    private val sr = FloatArray(look.stars)
    private val ph = FloatArray(look.stars)
    private val star = Paint(Paint.ANTI_ALIAS_FLAG).apply { blendMode = BlendMode.PLUS }
    private val band = Paint().apply { blendMode = BlendMode.PLUS }
    private var bandLength = 0f
    private var bandHalf = 0f
    private var width = 0f
    private var horizon = 0f

    /** The sky's shader for a [height] px surface whose horizon is at [horizonY]. */
    fun shader(height: Float, horizonY: Float): Shader {
        val h = (horizonY / height).coerceIn(0.05f, 0.95f)
        val ground = look.ground
        val colours = look.sky + (ground ?: intArrayOf(look.sky.last()))
        val stops = FloatArray(colours.size) { i ->
            if (i < look.sky.size) h * i / (look.sky.size - 1)
            else if (ground == null) 1f
            else h + (1 - h) * (i - look.sky.size) / (ground.size - 1).coerceAtLeast(1)
        }
        return LinearGradient(0f, 0f, 0f, height, colours, stops, Shader.TileMode.CLAMP)
    }

    fun resize(width: Float, horizonY: Float, unit: Float) {
        this.width = width
        horizon = horizonY
        val rnd = Random(SEED)
        for (i in sx.indices) {
            sx[i] = rnd.nextFloat() * width
            // Thicker toward the zenith, thinning into the horizon's haze.
            sy[i] = horizonY * (1 - rnd.nextFloat()) * (1 - rnd.nextFloat())
            sr[i] = (STAR_MIN + rnd.nextFloat() * STAR_SPAN) * unit
            ph[i] = rnd.nextFloat() * TAU
        }
        bandLength = width
        val inks = look.inks
        band.shader = LinearGradient(
            0f, -BAND * unit, 0f, BAND * unit,
            intArrayOf(clear(inks[Ink.LIFT]), ColorUtils.setAlphaComponent(inks[Ink.LIFT], a(BAND_ALPHA)), clear(inks[Ink.LIFT])),
            floatArrayOf(0f, 0.5f, 1f),
            Shader.TileMode.CLAMP,
        )
        bandHalf = BAND * unit
    }

    fun draw(canvas: Canvas, timeMs: Long) {
        if (look.band && width > 0f) {
            canvas.save()
            canvas.translate(width / 2, horizon * BAND_Y)
            canvas.rotate(BAND_TILT)
            canvas.drawRect(-bandLength, -bandHalf, bandLength, bandHalf, band)
            canvas.restore()
        }
        val t = timeMs * TWINKLE
        val ink = look.inks[Ink.BRIGHT]
        for (i in sx.indices) {
            val k = 0.55f + 0.45f * sin(t * (1 + i % 5) + ph[i])
            star.color = ColorUtils.setAlphaComponent(ink, a(k))
            canvas.drawCircle(sx[i], sy[i], sr[i], star)
        }
    }

    private fun clear(color: Int) = ColorUtils.setAlphaComponent(color, 0)

    private fun a(alpha: Float) = (alpha.coerceIn(0f, 1f) * 255f + 0.5f).toInt()

    private companion object {
        const val SEED = 7L
        const val TAU = 6.2831855f
        /** A star's radius in the board's pixels, and how much more the largest is. */
        const val STAR_MIN = 0.5f
        const val STAR_SPAN = 1.1f
        /** Radians a millisecond for the slowest twinkle. */
        const val TWINKLE = 0.0011f
        /** The milky way: half its width in board pixels, its strength, its lean, and how far up the sky. */
        const val BAND = 90f
        const val BAND_ALPHA = 0.14f
        const val BAND_TILT = -14f
        const val BAND_Y = 0.42f
    }
}
