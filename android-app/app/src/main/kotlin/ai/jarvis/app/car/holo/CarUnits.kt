package ai.jarvis.app.car.holo

import java.util.Locale
import kotlin.math.roundToInt

/**
 * The units the drive speaks in: the road signs of the driver's country (T-0648).
 *
 * The United States, Liberia and Myanmar measure the road in feet, miles and mph; the
 * rest of the world in metres, kilometres and km/h. Does not cover the United Kingdom,
 * which signs miles and mph (the brief names the three above), or a driver who wants
 * the other unit than their country's: there is no setting.
 */
class CarUnits(val imperial: Boolean) {

    /** "300 ft", "0.6 mi", "12 mi"; or the route guide's "90 m", "4.2 km". */
    fun distance(metres: Float): String {
        if (!imperial) return RouteGuide.metres(metres)
        val feet = ((metres * FT_PER_M) / FT_STEP).roundToInt() * FT_STEP
        if (feet < FT_MAX) return "$feet ft"
        val miles = metres / M_PER_MI
        return if (miles < 9.95f) String.format(Locale.ROOT, "%.1f mi", miles) else "${miles.roundToInt()} mi"
    }

    /** The speed as a whole number, in the unit [imperial] picks (km/h or mph). */
    fun speed(metresPerSecond: Float): String =
        (metresPerSecond * if (imperial) MPS_TO_MPH else MPS_TO_KMH).roundToInt().toString()

    companion object {
        private val IMPERIAL = setOf("US", "LR", "MM")

        fun of(locale: Locale) = CarUnits(locale.country in IMPERIAL)

        private const val FT_PER_M = 3.28084f
        private const val M_PER_MI = 1609.344f

        /** Feet are rounded like the metric zone rounds metres: to what a glance can use. */
        private const val FT_STEP = 50

        /** Past this the drive says miles: 1000 ft is a fifth of a mile. */
        private const val FT_MAX = 1000
        private const val MPS_TO_MPH = 2.236936f
        private const val MPS_TO_KMH = 3.6f
    }
}
