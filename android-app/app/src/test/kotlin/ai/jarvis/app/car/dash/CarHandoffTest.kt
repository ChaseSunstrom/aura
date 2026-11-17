package ai.jarvis.app.car.dash

import ai.jarvis.app.car.voice.CarVoiceView
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageInfo
import android.os.Looper
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import java.time.Duration

/**
 * A route follows the person between the phone and the car (the owner, 2026-11-09: "I asked
 * for this not connected to the car, so it needs to cleanly transfer from both").
 */
@RunWith(RobolectricTestRunner::class)
class CarHandoffTest {
    private val app = RuntimeEnvironment.getApplication()

    @Before
    fun maps() {
        shadowOf(app.packageManager).installPackage(PackageInfo().apply { packageName = "app.organicmaps" })
    }

    @After
    fun letGo() {
        CarHud.carBack()
        CarHud.routeEvent(app, JSONObject().put("cancel", true))
    }

    private fun idle(ms: Long) = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms))

    @Test
    fun `a route sent off the car is in the state the car page reads when the car comes up`() {
        CarHud.route(app, null, "", 41.6, -40.6, "Burger place")
        val state = JSONObject(CarHud.state(null, null, emptyList(), null, emptyList(), CarVoiceView()))
        val held = state.getJSONObject("destination")
        assertEquals(41.6, held.getDouble("lat"), 0.0)
        assertEquals(-40.6, held.getDouble("lon"), 0.0)
        assertEquals("Burger place", held.getString("label"))
    }

    @Test
    fun `the car going away with the route on its map opens it on the phone`() {
        CarHud.hold(41.6, -40.6, "Burger place", "", onCar = true)
        CarHud.carGone(app)
        idle(CarHud.HANDOFF_DELAY_MS - 1)
        assertNull(shadowOf(app).nextStartedActivity)
        idle(1)
        val started = shadowOf(app).nextStartedActivity
        assertEquals(Intent.ACTION_VIEW, started.action)
        assertEquals("app.organicmaps", started.`package`)
        // Still held: a car that comes up later draws it again.
        assertEquals("Burger place", CarHud.destination?.getString("label"))
        // Android 15 blocks that start with no window of ours showing (the review of T-0806), and
        // it does so silently: a tap in the shade carries the same route.
        val note = shadowOf(app.getSystemService(NotificationManager::class.java)).allNotifications.single()
        assertEquals("Continue to Burger place", note.extras.getString("android.title"))
        val tap = shadowOf(note.contentIntent).savedIntent
        assertEquals(Intent.ACTION_VIEW, tap.action)
        assertEquals("app.organicmaps", tap.`package`)
    }

    @Test
    fun `a car back within the delay keeps the route on the car`() {
        CarHud.hold(41.6, -40.6, "Burger place", "", onCar = true)
        CarHud.carGone(app)
        idle(CarHud.HANDOFF_DELAY_MS / 2)
        CarHud.carBack()
        idle(CarHud.HANDOFF_DELAY_MS)
        assertNull(shadowOf(app).nextStartedActivity)
        assertEquals(0, shadowOf(app.getSystemService(NotificationManager::class.java)).allNotifications.size)
    }

    @Test
    fun `an arrived, cancelled or phone-side route is not handed off`() {
        CarHud.hold(41.6, -40.6, "Burger place", "", onCar = true)
        CarHud.carGone(app)
        CarHud.routeEvent(app, JSONObject().put("cancel", true))
        idle(CarHud.HANDOFF_DELAY_MS)
        assertNull(shadowOf(app).nextStartedActivity)
        // Opened on the phone already: the phone's maps app has it.
        CarHud.hold(41.6, -40.6, "Burger place", "", onCar = false)
        CarHud.carGone(app)
        idle(CarHud.HANDOFF_DELAY_MS)
        assertNull(shadowOf(app).nextStartedActivity)
    }
}
