package ai.jarvis.app.assist.island

import ai.jarvis.app.assist.AssistOverlay
import ai.jarvis.app.car.dash.CarHud
import ai.jarvis.app.car.holo.CarUnits
import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.util.Locale

/**
 * Who keeps a route held off the car on the island (T-0695): its own island while nothing else is
 * up, the conversation's while one is ([lend]), so two islands are never on screen at once. Fed
 * by CarHud whenever the held route changes; the distance is re-read from the phone's last fix
 * every [REFRESH_MS] (no new location requests, nothing sent anywhere).
 *
 * Does not cover the locked screen (overlays never show over the keyguard) or a phone without
 * "display over other apps": the hand-off's notification is the route's tap there.
 */
object RouteIsland {

    private const val REFRESH_MS = 30_000L

    private val main by lazy { Handler(Looper.getMainLooper()) }
    private var app: Context? = null
    private var held: JSONObject? = null
    /** The route's own island, while no conversation carries it. */
    private var own: AssistOverlay? = null
    /** A conversation's island, which carries the route while it is up. */
    private var lent: AssistOverlay? = null

    /** The island the route is on now, for the tests. */
    internal val showingForTest: AssistOverlay? get() = lent ?: own

    /**
     * CarHud's held route changed: [route] null once cancelled; [onCar] while the car's own map
     * draws it, which needs no pill. Any thread.
     */
    fun update(context: Context, route: JSONObject?, onCar: Boolean) {
        val appContext = context.applicationContext
        main.post {
            app = appContext
            held = if (onCar) null else route
            refresh()
        }
    }

    /** A conversation's island came up ([surface]), or went (null). Main thread. */
    fun lend(surface: AssistOverlay?) {
        lent?.takeIf { it !== surface }?.setActivity(null)
        lent = surface
        refresh()
    }

    private val tick = Runnable { refresh() }

    private fun refresh() {
        main.removeCallbacks(tick)
        val context = app ?: return
        val pill = RouteActivity.pill(held, CarHud.lastFix(context), CarUnits.of(Locale.getDefault()))
        // There: the route is done with, here; the car's page keeps its own until it is told.
        if (pill == null) held = null
        val open = { pill?.let { CarHud.navigate(context, null, it.app, it.lat, it.lon, it.label) }; Unit }
        val cancel = { CarHud.routeEvent(context, JSONObject().put("cancel", true)); Unit }
        val carrier = lent
        if (carrier != null || pill == null) {
            own?.detach()
            own = null
            carrier?.setActivity(pill, open, cancel)
        } else {
            val surface = own ?: AssistOverlay(context) { }
            surface.setActivity(pill, open, cancel)
            if (own == null && surface.attach(turn = false)) own = surface
        }
        if (pill != null) main.postDelayed(tick, REFRESH_MS)
    }
}
