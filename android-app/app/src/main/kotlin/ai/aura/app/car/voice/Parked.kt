package ai.aura.app.car.voice

import ai.aura.app.automation.actions.ActionResult
import ai.aura.app.automation.policy.ActionTier
import ai.aura.app.automation.actions.JarvisAction
import ai.aura.app.automation.actions.builtin.GetLocation
import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * "Where did I park?": the phone's fix when the car link goes down.
 *
 * Remembered on every session end, so a drive whose link drops and re-forms keeps the
 * last one, which is where the car stopped. Asks for a fine fix but takes what the
 * phone has (a two-minute-old GPS fix from the drive is usually the best answer). Does
 * not know the car moved without the phone: someone else driving it away is invisible.
 */
object Parked {
    private const val PREFS = "jarvis_drive"
    private const val KEY = "parked"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun remember(context: Context, nowMs: Long = System.currentTimeMillis()) {
        val app = context.applicationContext
        scope.launch {
            val fix = runCatching {
                GetLocation.execute(app, JSONObject().put("accuracy", "fine").put("max_age_ms", 120_000L).put("timeout_ms", 15_000L))
            }.getOrNull()
            val data = fix?.data?.takeIf { fix.ok } ?: return@launch
            app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(KEY, data.put("parked_at_ms", nowMs).toString()).apply()
        }
    }

    /**
     * Pure: what `where_parked` answers from the stored fix, and -- given where the phone
     * is now, [hereLat]/[hereLon] -- how far and which way the car is ("guided find-my-car",
     * 2026-10-08 wants): `metres_away` and `direction` ("northeast").
     */
    fun answer(stored: String?, nowMs: Long, hereLat: Double? = null, hereLon: Double? = null): ActionResult {
        val fix = stored?.let { runCatching { JSONObject(it) }.getOrNull() }
            ?: return ActionResult.error("no parked location yet: one is saved when the car disconnects")
        val at = fix.optLong("parked_at_ms", 0L)
        fix.put("minutes_ago", ((nowMs - at) / 60_000L).coerceAtLeast(0L))
        val lat = fix.optDouble("latitude", Double.NaN)
        val lon = fix.optDouble("longitude", Double.NaN)
        if (hereLat != null && hereLon != null && !lat.isNaN() && !lon.isNaN()) {
            // To 50 m: a coarse fix says no more, and the answer is "about".
            val m = ai.aura.app.routines.RoutineRules.metres(hereLat, hereLon, lat, lon)
            fix.put("metres_away", (Math.round(m / 50.0) * 50).toInt())
            fix.put("direction", compass(bearing(hereLat, hereLon, lat, lon)))
        }
        return ActionResult.ok(fix)
    }

    /** Degrees from north, 0..360, from the first point to the second. */
    fun bearing(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
        val p1 = Math.toRadians(lat1)
        val p2 = Math.toRadians(lat2)
        val dl = Math.toRadians(lon2 - lon1)
        val y = Math.sin(dl) * Math.cos(p2)
        val x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)
        return (Math.toDegrees(Math.atan2(y, x)) + 360.0) % 360.0
    }

    private val POINTS = listOf("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")

    fun compass(degrees: Double): String = POINTS[(((degrees + 22.5) % 360.0) / 45.0).toInt()]

    object WhereParked : JarvisAction {
        override val id = "where_parked"
        override val tier = ActionTier.AUTO
        override val description = "Where the car was when it last disconnected from this phone (lat/lon, minutes ago, and how far and which way from here)."
        override val paramsSchema = emptyMap<String, String>()
        override val capability = "location"

        override suspend fun execute(ctx: Context, params: JSONObject): ActionResult {
            // Where the phone is now, quickly and from what it already has: the answer
            // without the distance beats a walk-away while a fresh fix is found. Coarse,
            // as get_location's own free tier is: a fine fix asks first (GetLocation.tierFor),
            // and this AUTO action must not read one around that (the 2026-10-08 review).
            val here = runCatching {
                GetLocation.execute(ctx, JSONObject().put("accuracy", "coarse").put("max_age_ms", 60_000L).put("timeout_ms", 5_000L))
            }.getOrNull()?.takeIf { it.ok }?.data
            return answer(
                ctx.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null),
                System.currentTimeMillis(),
                here?.optDouble("latitude")?.takeUnless { it.isNaN() },
                here?.optDouble("longitude")?.takeUnless { it.isNaN() },
            )
        }
    }
}
