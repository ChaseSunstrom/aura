package ai.jarvis.app.assist.island

import ai.jarvis.app.config.JarvisConfig
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.os.Looper
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowSettings
import java.time.Duration

/**
 * The resting island's window (T-0890), driven for real: the owner, 2026-11-09, "a persistant
 * dynamic island type look in the phone camera hole … this should be able to be turned off …
 * make sure it's also performant so it doesnt kill the battery, and can be hidden/shown while a
 * video or something is full screened". Does not cover the owner's phone: whether its maker
 * lets an app overlay into the status bar's strip, or reports another app's full screen in the
 * overlay's insets, is seen there.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class IslandRestHostTest {
    private val app: Context = RuntimeEnvironment.getApplication()
    private val config = JarvisConfig(app)
    private lateinit var rest: RestIsland

    @Before
    fun up() {
        ShadowSettings.setCanDrawOverlays(true)
        config.islandRest = true
        config.islandStatus = false
        rest = RestIsland(app, config)
        others = allCallbacks()
    }

    @After
    fun down() = rest.stop()

    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(300))
    private fun view() = rest.viewForTest
    private fun params() = view()!!.layoutParams as WindowManager.LayoutParams
    private fun statusBar(shown: Boolean) {
        view()!!.dispatchApplyWindowInsets(WindowInsets.Builder().setVisible(WindowInsets.Type.statusBars(), shown).build())
        idle()
    }
    // Its own receiver: the minute's tick with do-not-disturb's change (another part of the app hears the tick too).
    private fun listening() = shadowOf(app as android.app.Application).registeredReceivers.count {
        it.intentFilter.hasAction(Intent.ACTION_TIME_TICK) && it.intentFilter.hasAction(android.app.NotificationManager.ACTION_INTERRUPTION_FILTER_CHANGED)
    }
    private fun allCallbacks() = shadowOf(app.getSystemService(ConnectivityManager::class.java)).networkCallbacks.size
    // The island's own: the app follows the network for its link too, from its start.
    private var others = 0
    private fun networkCallbacks() = allCallbacks() - others
    /** Started, and told what a phone tells a new window: the status bar shows. Robolectric's own first insets carry no bar at all. */
    private fun started() {
        rest.start(); idle()
        statusBar(true)
    }
    private fun screen(on: Boolean) {
        app.sendBroadcast(Intent(if (on) Intent.ACTION_SCREEN_ON else Intent.ACTION_SCREEN_OFF))
        idle()
    }

    @Test
    fun `it rests on the camera as an app overlay that is never touched, so the shade still pulls down`() {
        started()
        assertEquals(View.VISIBLE, view()!!.visibility)
        val p = params()
        assertEquals(WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, p.type)
        assertTrue("not touchable", p.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE != 0)
        assertTrue("not focusable", p.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE != 0)
        assertEquals("over the camera, not under the bar", WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS, p.layoutInDisplayCutoutMode)
        assertEquals("placed against the screen, not fitted below the bars", 0, p.fitInsetsTypes)
    }

    @Test
    fun `it hides while another app is full screen and comes back after`() {
        started()
        statusBar(false)
        assertEquals(View.INVISIBLE, view()!!.visibility)
        statusBar(true)
        assertEquals(View.VISIBLE, view()!!.visibility)
    }

    @Test
    fun `it stays over the app's own full-screen home, which hides the status bar`() {
        // The real order: the home starts, then starts the service that puts the island up.
        val home = Robolectric.buildActivity(Activity::class.java).setup()
        started()
        statusBar(false); idle()
        assertEquals(View.VISIBLE, view()!!.visibility)
        home.pause().stop(); idle()
        assertEquals("another app full screen again", View.INVISIBLE, view()!!.visibility)
        home.destroy()
    }

    @Test
    fun `it steps aside for the conversation's island and comes back after`() {
        started()
        rest.conversation(true); idle()
        assertEquals(View.INVISIBLE, view()!!.visibility)
        rest.conversation(false); idle()
        assertEquals(View.VISIBLE, view()!!.visibility)
    }

    @Test
    fun `turned off there is no window, and turned on again it is back`() {
        config.islandRest = false
        rest.start(); idle()
        assertNull(view())
        config.islandRest = true; idle()
        assertNotNull(view())
        config.islandRest = false; idle()
        assertNull(view())
    }

    @Test
    fun `at rest nothing is scheduled, no frame and no timer`() {
        config.islandStatus = true
        started()
        assertEquals(Duration.ZERO, shadowOf(Looper.getMainLooper()).nextScheduledTaskTime)
        assertEquals(null, view()!!.animation)
    }

    @Test
    fun `its status listeners run only while the screen is on`() {
        config.islandStatus = true
        started()
        assertEquals(1, listening())
        assertEquals(1, networkCallbacks())
        screen(false)
        assertEquals(0, listening())
        assertEquals(0, networkCallbacks())
        screen(true)
        assertEquals(1, listening())
        assertEquals(1, networkCallbacks())
    }

    @Test
    fun `without the status option it listens to nothing but the screen`() {
        started()
        assertEquals(0, listening())
        assertEquals(0, networkCallbacks())
    }
}
