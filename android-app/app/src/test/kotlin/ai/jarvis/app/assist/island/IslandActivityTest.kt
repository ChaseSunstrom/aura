package ai.jarvis.app.assist.island

import ai.jarvis.app.assist.AssistOverlay
import ai.jarvis.app.car.dash.CarHud
import ai.jarvis.app.car.holo.CarUnits
import android.os.Looper
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowSettings
import java.time.Duration

/**
 * A route held off the car as the island's activity (T-0695): what the pill says, and when it
 * goes, and who shows it (RouteIsland). Made-up coordinates.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
class IslandActivityTest {

    private val app get() = RuntimeEnvironment.getApplication()

    @Before
    fun up() {
        ShadowSettings.setCanDrawOverlays(true)
        android.animation.ValueAnimator::class.java
            .getMethod("setDurationScale", Float::class.javaPrimitiveType).invoke(null, 0f)
        // The system's animations off, as reduced motion: the thinking dots hold still rather than
        // ask for frames for ever, which a paused looper's idleFor never gets to the end of.
        android.provider.Settings.Global.putFloat(
            RuntimeEnvironment.getApplication().contentResolver,
            android.provider.Settings.Global.ANIMATOR_DURATION_SCALE,
            0f,
        )
    }

    @After
    fun down() {
        RouteIsland.lend(null)
        RouteIsland.update(app, null, onCar = false)
        idle()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(300))

    private val metric = CarUnits(imperial = false)
    private val there = JSONObject().put("lat", 51.5).put("lon", -0.12).put("label", "Burger place").put("app", "organic")

    @Test
    fun `a held route is a pill with where to and how far from the last fix`() {
        // 0.02 degrees of latitude is about 2.2 km.
        val pill = RouteActivity.pill(there, 51.48 to -0.12, metric)!!
        assertEquals("Burger place", pill.label)
        assertEquals("2.2 km", pill.figure)
        assertEquals("organic", pill.app)
    }

    @Test
    fun `with no fix it still says where to, and an unnamed place is the destination`() {
        val pill = RouteActivity.pill(JSONObject(there.toString()).put("label", " "), null, metric)!!
        assertEquals("Destination", pill.label)
        assertNull(pill.figure)
    }

    @Test
    fun `arriving, cancelling or a route with no place takes the pill down`() {
        assertNull(RouteActivity.pill(there, 51.5003 to -0.12, metric))
        assertNull(RouteActivity.pill(null, 51.48 to -0.12, metric))
        assertNull(RouteActivity.pill(JSONObject().put("label", "x"), null, metric))
    }

    @Test
    fun `the distance is the great circle`() {
        // A degree of longitude on the equator is about 111.2 km.
        val m = RouteActivity.metres(0.0, 0.0, 0.0, 1.0)
        assertTrue("$m", m in 111_000f..111_400f)
    }

    @Test
    fun `a route off the car is the island's pill, and none while the car's map draws it`() {
        RouteIsland.update(app, there, onCar = true)
        idle()
        assertNull(RouteIsland.showingForTest)
        RouteIsland.update(app, there, onCar = false)
        idle()
        assertEquals(Island.State.ACTIVITY, RouteIsland.showingForTest!!.stateForTest)
    }

    @Test
    fun `a conversation carries the route on its own island, and hands it back when it ends`() {
        RouteIsland.update(app, there, onCar = false)
        idle()
        val own = RouteIsland.showingForTest!!
        val convo = AssistOverlay(app) { }
        assertTrue(convo.attach())
        RouteIsland.lend(convo)
        idle()
        assertSame(convo, RouteIsland.showingForTest)
        assertEquals(Island.State.CARD, convo.stateForTest)
        assertTrue("the route's own island is gone", !own.isShowing)
        RouteIsland.lend(null)
        convo.detach()
        idle()
        assertEquals(Island.State.ACTIVITY, RouteIsland.showingForTest!!.stateForTest)
    }

    @Test
    fun `a flick hides the route's pill without dropping the route, and ending it takes it down`() {
        CarHud.routeEvent(app, JSONObject().put("cancel", true))
        RouteIsland.update(app, there, onCar = false)
        idle()
        val island = RouteIsland.showingForTest!!
        (island.rootForTest as IslandView).listener!!.onGesture(Island.Gesture.FLICK_UP)
        idle()
        assertEquals(Island.State.HIDDEN, island.stateForTest)
        assertTrue(island.isShowing)
        CarHud.routeEvent(app, JSONObject().put("cancel", true))
        idle()
        assertNull(RouteIsland.showingForTest)
    }
}
