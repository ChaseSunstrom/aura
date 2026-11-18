package ai.aura.app.assist.island

import ai.aura.app.car.holo.CarUnits
import org.json.JSONObject
import kotlin.math.asin
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * A route held off the car as the island's ongoing activity (T-0695): where to, and how far
 * from the phone's last fix. The owner asked for a route in progress to live in the island
 * (2026-11-09), after a route sent from the popup disappeared with it.
 *
 * Pure: IslandActivityTest binds it. Does not cover progress along the road (off the car the
 * phone has no route geometry), only the straight-line distance, which the pill says as "away";
 * nor a route the driver set in the maps app themselves, which the phone never sees.
 */
object RouteActivity {

    /** The pill: [label] is where to, [figure] how far ("4.2 km"), null with no recent fix. */
    data class Pill(val label: String, val figure: String?, val lat: Double, val lon: Double, val app: String)

    /** Closer than this to the destination is there: the pill goes. A car park is about this wide. */
    const val ARRIVED_M = 75f

    private const val EARTH_M = 6_371_000.0

    /** Great-circle metres between two points. */
    fun metres(aLat: Double, aLon: Double, bLat: Double, bLon: Double): Float {
        val dLat = Math.toRadians(bLat - aLat)
        val dLon = Math.toRadians(bLon - aLon)
        val h = sin(dLat / 2).pow(2) + cos(Math.toRadians(aLat)) * cos(Math.toRadians(bLat)) * sin(dLon / 2).pow(2)
        return (2 * EARTH_M * asin(sqrt(h))).toFloat()
    }

    /**
     * The pill for [destination] (CarHud's held route: lat, lon, label, app) seen from [fix], or
     * null with no route, or once the fix is within [ARRIVED_M] of it.
     */
    fun pill(destination: JSONObject?, fix: Pair<Double, Double>?, units: CarUnits): Pill? {
        destination ?: return null
        val lat = destination.optDouble("lat")
        val lon = destination.optDouble("lon")
        if (lat.isNaN() || lon.isNaN()) return null
        val away = fix?.let { metres(it.first, it.second, lat, lon) }
        if (away != null && away < ARRIVED_M) return null
        return Pill(
            label = destination.optString("label").trim().ifEmpty { "Destination" },
            figure = away?.let(units::distance),
            lat = lat,
            lon = lon,
            app = destination.optString("app"),
        )
    }
}
