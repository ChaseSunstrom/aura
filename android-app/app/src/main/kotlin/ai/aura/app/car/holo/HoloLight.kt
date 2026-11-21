package ai.aura.app.car.holo

import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.tan

/**
 * Where the sun is for a place and a moment (T-1107; the owner, 2026-11-12: "accurate lighting
 * depending on the time of day"), and how much daylight that is. The low-precision formulas of
 * the Astronomical Almanac: within a few hundredths of a degree for this century, which is
 * far finer than a shaded wall can show.
 *
 * Does not know the weather (an overcast noon is as bright as a clear one here), the hills
 * (a valley's early dusk), or refraction at the horizon (the sun shows about half a degree
 * higher than this says as it sets).
 */
object HoloLight {
    /**
     * The wall clock (UTC ms) every map is lit by unless it is given its own; the app sets
     * it as it starts. With none (a test: a golden recorded at noon would not match one
     * recorded at night) a map is the night it always was.
     */
    @Volatile var clock: (() -> Long)? = null

    /** [elevationDeg] above the horizon (negative below); [azimuthDeg] clockwise from north. */
    class Sun(val elevationDeg: Double, val azimuthDeg: Double)

    fun sun(utcMs: Long, lat: Double, lon: Double): Sun {
        val d = utcMs / 86_400_000.0 - J2000_DAYS
        val g = Math.toRadians(357.529 + 0.98560028 * d)
        val l = Math.toRadians(280.459 + 0.98564736 * d + 1.915 * sin(g) + 0.020 * sin(2 * g))
        val tilt = Math.toRadians(23.439 - 0.00000036 * d)
        val ra = atan2(cos(tilt) * sin(l), cos(l))
        val dec = asin(sin(tilt) * sin(l))
        val hour = Math.toRadians((18.697374558 + 24.06570982441908 * d) * 15.0 + lon) - ra
        val phi = Math.toRadians(lat.coerceIn(-89.9, 89.9))  // the bearing has no meaning on a pole
        val up = asin((sin(phi) * sin(dec) + cos(phi) * cos(dec) * cos(hour)).coerceIn(-1.0, 1.0))
        val round = atan2(-sin(hour), tan(dec) * cos(phi) - sin(phi) * cos(hour))
        return Sun(Math.toDegrees(up), (Math.toDegrees(round) + 360.0) % 360.0)
    }

    /**
     * 0 in the dark, 1 in full day, easing between through twilight: dark once the sun is
     * [DARK_DEG] under the horizon (the end of civil twilight, when street lamps are on and
     * needed), full day once it is as far above.
     */
    fun daylight(elevationDeg: Double): Float {
        val t = ((elevationDeg - DARK_DEG) / (-2 * DARK_DEG)).coerceIn(0.0, 1.0)
        return (t * t * (3 - 2 * t)).toFloat()
    }

    /**
     * The light's colour as well as its amount (T-1584; the owner, 2026-11-20: "not just
     * white/dark, like including sunset"): [daylight] as the eye takes it, not [daylight] above
     * (the 3D drive's): most of the day's light is still there as the sun sets and is gone only
     * near civil twilight's end, so it eases from [LIT_FROM_DEG] to [LIT_FULL_DEG] (a sunset
     * drawn half lit was the night's navy under the gold: brown); [warm] 0..1, the low sun's gold,
     * full from just under the horizon to a few degrees up (sunrise, sunset, golden hour) and
     * gone by [WARM_TOP_DEG], so a winter noon at a northern place stays golden all day; [blue]
     * 0..1, the blue hour while the sun is a few degrees under, gone by the horizon and by
     * [BLUE_BOTTOM_DEG]. Each eases with the elevation, so the light never steps.
     */
    class Tone(val daylight: Float, val warm: Float, val blue: Float)

    fun tone(elevationDeg: Double): Tone {
        val e = elevationDeg
        val warm = ease(e, WARM_FROM_DEG, WARM_FULL_DEG) * (1f - ease(e, WARM_FADE_DEG, WARM_TOP_DEG))
        val blue = ease(e, BLUE_BOTTOM_DEG, BLUE_FULL_DEG) * (1f - ease(e, BLUE_FADE_DEG, BLUE_TOP_DEG))
        return Tone(ease(e, LIT_FROM_DEG, LIT_FULL_DEG), warm, blue)
    }

    /** 0 at [from], 1 at [to], smooth between (the same easing as [daylight]). */
    private fun ease(e: Double, from: Double, to: Double): Float {
        val t = ((e - from) / (to - from)).coerceIn(0.0, 1.0)
        return (t * t * (3 - 2 * t)).toFloat()
    }

    // The phases' edges, in degrees of the sun's elevation: where photographers put them.
    private const val LIT_FROM_DEG = -8.0
    private const val LIT_FULL_DEG = 3.0
    private const val WARM_FROM_DEG = -5.0
    private const val WARM_FULL_DEG = 0.5
    private const val WARM_FADE_DEG = 4.5
    private const val WARM_TOP_DEG = 14.0
    private const val BLUE_BOTTOM_DEG = -11.0
    private const val BLUE_FULL_DEG = -6.0
    private const val BLUE_FADE_DEG = -3.0
    private const val BLUE_TOP_DEG = 1.0

    /** Days from the Unix epoch to noon on 1 January 2000, the formulas' zero. */
    private const val J2000_DAYS = 10_957.5
    const val DARK_DEG = -6.0
}
