package ai.aura.app

import ai.aura.app.config.JarvisConfig
import android.app.AlertDialog
import android.os.Looper
import android.widget.FrameLayout
import android.widget.TextView
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextReplacement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.android.controller.ActivityController
import org.robolectric.shadows.ShadowAlertDialog
import org.robolectric.annotation.Config
import java.time.Duration

/**
 * The one settings screen (T-0689), through the real activity: SAVE stores what is typed and
 * says so in the screen's note, a refused address or a missing key stores nothing and the note
 * says why, Back with an edit asks before it discards, and a phone with no Hermes link says
 * where to get one instead of an empty Jarvis section.
 *
 * Does not cover the Hermes rows against a live socket (no adapter in Robolectric; the
 * contract parsing is HermesSettingsTest's), the scanner hand-off or the system screens the
 * buttons open.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
class SettingsScreenTest {

    @get:Rule
    val compose = createEmptyComposeRule()

    private val config get() = JarvisConfig(RuntimeEnvironment.getApplication())

    private fun open(): ActivityController<SettingsActivity> =
        Robolectric.buildActivity(SettingsActivity::class.java).setup().also { idle() }

    private fun idle() {
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(300))
        Snapshot.sendApplyNotifications()
        compose.waitForIdle()
    }

    /** A field is found by its label, the kit's `Field` gives it as its accessibility name. */
    private fun type(label: String, text: String) {
        compose.onNodeWithContentDescription(label).performScrollTo().performTextReplacement(text)
        idle()
    }

    private fun tap(text: String) {
        // SAVE is in the bar fixed under the scroll, so there is nothing to scroll to.
        compose.onNodeWithText(text).performClick()
        idle()
    }

    private fun note(controller: ActivityController<SettingsActivity>): String =
        controller.get().findViewById<FrameLayout>(android.R.id.content)
            .findViewWithTag<TextView>("jarvis.screen-note")?.text?.toString().orEmpty()

    @Test
    fun `save stores the address and the key, and the note says so`() {
        val screen = open()
        type("Server URL", "http://192.168.2.10:8123")
        type("Access token", "settings-screen-test-key")
        tap("SAVE")

        assertEquals("http://192.168.2.10:8123", config.serverUrl)
        assertEquals("settings-screen-test-key", config.token)
        assertTrue("the note did not say it saved: ${note(screen)}", note(screen).contains("Saved"))
    }

    @Test
    fun `a refused address stores nothing, and the note says why`() {
        val screen = open()
        type("Server URL", "http://example.com")
        type("Access token", "settings-screen-test-key")
        tap("SAVE")

        assertEquals("", config.serverUrl)
        assertEquals("a refused address took its key with it", "", config.token)
        assertTrue(note(screen), note(screen).startsWith("Not saved: "))
        assertTrue(note(screen), note(screen).contains("Plain HTTP"))
        assertFalse(screen.get().isFinishing)
    }

    @Test
    fun `a missing key is refused`() {
        val screen = open()
        type("Server URL", "http://192.168.2.10:8123")
        tap("SAVE")

        assertEquals("", config.serverUrl)
        assertTrue(note(screen), note(screen).startsWith("Not saved: "))
        assertTrue(note(screen), note(screen).contains("access token is required"))
    }

    @Test
    fun `back with an edit asks, and discard leaves without storing it`() {
        val screen = open()
        type("Server URL", "http://192.168.2.10:8123")
        screen.get().onBackPressedDispatcher.onBackPressed()
        idle()

        // The question is HEAD's AlertDialog (not Compose), titled "Unsaved changes".
        val dialog = ShadowAlertDialog.getLatestAlertDialog()
        assertTrue("Back with an edit did not ask", dialog != null && dialog.isShowing)
        assertFalse("Back left with an edit on screen", screen.get().isFinishing)
        dialog.getButton(AlertDialog.BUTTON_NEGATIVE).performClick()
        idle()

        assertTrue(screen.get().isFinishing)
        assertEquals("", config.serverUrl)
    }

    @Test
    fun `back with nothing changed just leaves`() {
        val screen = open()
        screen.get().onBackPressedDispatcher.onBackPressed()
        idle()
        assertTrue(screen.get().isFinishing)
    }

    @Test
    fun `with no Hermes link the Jarvis rows say where to get one`() {
        open()
        compose.onNodeWithText("isn't paired to Hermes yet", substring = true).assertExists()
    }
}
