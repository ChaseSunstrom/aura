package ai.aura.app.assist.island

import ai.aura.app.car.input.HeadwayAccessibilityService
import ai.aura.app.config.JarvisConfig
import android.accessibilityservice.AccessibilityService
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Looper
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
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
 * The opt-in cover over the phone's status icons (T-0969): off by default; on, the resting island
 * and a band in its ground are accessibility overlays over the status bar, the corner where Android
 * draws its camera and microphone dot is left open, and both step aside while System UI is in
 * front. Does not cover a real phone's System UI: which events its shade sends is seen there.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class IslandStatusCoverTest {
    private val app: Context = RuntimeEnvironment.getApplication()
    private val config = JarvisConfig(app)
    private lateinit var rest: RestIsland
    private var service: HeadwayAccessibilityService? = null

    @Before
    fun up() {
        ShadowSettings.setCanDrawOverlays(true)
        config.islandRest = true
        config.islandStatus = true
        rest = RestIsland(app, config)
    }

    @After
    fun down() {
        rest.stop()
        service?.onUnbind(null)
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(300))
    private fun type(v: View?) = (v!!.layoutParams as WindowManager.LayoutParams).type
    /** What a phone tells a new window: the status bar shows (Robolectric's first insets carry none). */
    private fun barShown() {
        rest.viewForTest!!.dispatchApplyWindowInsets(WindowInsets.Builder().setVisible(WindowInsets.Type.statusBars(), true).build())
        idle()
    }
    private fun grantTouch() {
        service = Robolectric.setupService(HeadwayAccessibilityService::class.java).also {
            AccessibilityService::class.java.getDeclaredMethod("onServiceConnected").apply { isAccessible = true }.invoke(it)
        }
        idle()
    }
    private fun inFront(pkg: String, paneClosed: Boolean = false) {
        service!!.onAccessibilityEvent(
            AccessibilityEvent(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED).apply {
                packageName = pkg
                // The platform stamps each event with its display; a test's event has none.
                AccessibilityEvent::class.java.getMethod("setDisplayId", Int::class.java).invoke(this, android.view.Display.DEFAULT_DISPLAY)
                if (paneClosed) contentChangeTypes = AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_DISAPPEARED
            },
        )
        idle()
    }

    @Test
    fun `the cover is off by default, and without the car's touch service the island stays an app overlay`() {
        assertFalse(config.islandCover)
        rest.start(); idle()
        config.islandCover = true; idle()
        assertEquals(WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, type(rest.viewForTest))
        assertNull("no band without the service to draw it", rest.bandForTest)
    }

    @Test
    fun `the band spans the status bar and leaves the camera and microphone dot's end corner open`() {
        assertEquals(IslandRest.Box(0, 0, 984, 96), IslandRest.band(1080, 96, rtl = false))
        assertEquals(IslandRest.Box(96, 0, 1080, 96), IslandRest.band(1080, 96, rtl = true))
    }

    @Test
    fun `System UI's panes are counted, so quick settings folding back leaves the shade covered by nothing`() {
        val ui = "com.android.systemui"
        val shade = HeadwayAccessibilityService.systemPanes(0, ui, AccessibilityEvent.CONTENT_CHANGE_TYPE_UNDEFINED)
        val settings = HeadwayAccessibilityService.systemPanes(shade, ui, AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_APPEARED)
        val folded = HeadwayAccessibilityService.systemPanes(settings, ui, AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_DISAPPEARED)
        assertEquals("the shade is still down", 1, folded)
        assertEquals(0, HeadwayAccessibilityService.systemPanes(folded, ui, AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_DISAPPEARED))
        assertEquals("a change not understood keeps it aside", 1, HeadwayAccessibilityService.systemPanes(1, ui, AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_TITLE))
        assertEquals(0, HeadwayAccessibilityService.systemPanes(0, ui, AccessibilityEvent.CONTENT_CHANGE_TYPE_PANE_DISAPPEARED))
        assertEquals("another app in front", 0, HeadwayAccessibilityService.systemPanes(3, "com.example.maps", AccessibilityEvent.CONTENT_CHANGE_TYPE_UNDEFINED))
    }

    @Test
    fun `on with the service, the island and its band cover the bar, step aside for the shade, and go back to an app overlay when off`() {
        grantTouch()
        rest.start(); idle()
        config.islandCover = true; idle()
        barShown()
        val island = rest.viewForTest
        val band = rest.bandForTest
        assertNotNull(band)
        band!!
        assertEquals(WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY, type(island))
        assertEquals(WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY, type(band))
        assertEquals(View.VISIBLE, island!!.visibility)
        assertEquals(View.VISIBLE, band.visibility)

        inFront("com.android.systemui")
        assertEquals("the shade is down: the island is not over it", View.INVISIBLE, island.visibility)
        assertEquals(View.INVISIBLE, band.visibility)

        inFront("com.android.systemui", paneClosed = true)
        assertEquals(View.VISIBLE, island.visibility)
        assertEquals(View.VISIBLE, band.visibility)

        val keyguard = shadowOf(app.getSystemService(KeyguardManager::class.java))
        keyguard.setKeyguardLocked(true)
        app.sendBroadcast(Intent(Intent.ACTION_SCREEN_ON)); idle()
        assertEquals("the lock screen is System UI too, whatever the events said", View.INVISIBLE, island.visibility)
        assertEquals(View.INVISIBLE, band.visibility)
        keyguard.setKeyguardLocked(false)
        app.sendBroadcast(Intent(Intent.ACTION_USER_PRESENT)); idle()
        assertEquals(View.VISIBLE, island.visibility)

        config.islandStatus = false; idle()
        assertEquals("without the status in the island nothing replaces the bar's icons", WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, type(rest.viewForTest))
        assertNull(rest.bandForTest)
        config.islandStatus = true; idle()
        config.islandCover = false; idle()
        assertEquals(WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, type(rest.viewForTest))
        assertNull(rest.bandForTest)
    }
}
