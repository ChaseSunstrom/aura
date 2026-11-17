package ai.jarvis.app.car.holo

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

    /** Days from the Unix epoch to noon on 1 January 2000, the formulas' zero. */
    private const val J2000_DAYS = 10_957.5
    const val DARK_DEG = -6.0
}
