package ai.aura.app.car.dash

import android.content.Intent
import android.content.pm.PackageInfo
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf

/**
 * A route Jarvis sends to a screen that does not draw routes itself (the owner, 2026-11-09:
 * "route me to ..." from the phone's popup, off the car -- Jarvis said the phone had the
 * destination open and nothing opened). [CarHud.routeEvent] opens it where the person is and
 * answers the waiting `car_navigate` call with where it opened, or why it did not.
 */
@RunWith(RobolectricTestRunner::class)
class CarNavigateTest {
    private val app = RuntimeEnvironment.getApplication()
    private val place = JSONObject().put("id", "r1").put("lat", 41.6).put("lon", -40.6).put("label", "Burger place").put("app", "")

    @After
    fun letGo() {
        CarHud.routeEvent(app, JSONObject().put("cancel", true))
    }

    @Test
    fun `off the car a route opens in the phone's maps app, says so, and is held for the car`() {
        shadowOf(app.packageManager).installPackage(PackageInfo().apply { packageName = "app.organicmaps" })
        val answer = CarHud.routeEvent(app, place)
        assertEquals("ok", answer.getString("status"))
        assertEquals("phone", answer.getString("opened"))
        assertEquals("organic", answer.getString("app"))
        val started = shadowOf(app).nextStartedActivity
        assertEquals(Intent.ACTION_VIEW, started.action)
        assertEquals("app.organicmaps", started.`package`)
        // A car screen that comes up later reads it (CarHud.snapshot's `destination`).
        assertEquals("Burger place", CarHud.destination?.getString("label"))
    }

    @Test
    fun `with no maps app the answer says why, never that it opened`() {
        val answer = CarHud.routeEvent(app, place)
        assertEquals("error", answer.getString("status"))
        assertTrue(answer.getString("error"), answer.getString("error").startsWith("No maps app"))
        assertNull(shadowOf(app).nextStartedActivity)
    }

    @Test
    fun `a route with no place is refused, and cancel lets the held route go`() {
        assertEquals("error", CarHud.routeEvent(app, JSONObject().put("label", "Nowhere")).getString("status"))
        shadowOf(app.packageManager).installPackage(PackageInfo().apply { packageName = "app.organicmaps" })
        CarHud.routeEvent(app, place)
        assertEquals("ok", CarHud.routeEvent(app, JSONObject().put("cancel", true)).getString("status"))
        assertNull(CarHud.destination)
    }
}
