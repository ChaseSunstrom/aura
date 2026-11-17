package ai.jarvis.app

import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiObject2
import java.util.regex.Pattern
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * What the user types into Settings is still there when they come back.
 *
 * Sounds trivial; it is the single most consequential piece of state in the app.
 * The server URL decides which host the phone will hold an authenticated socket
 * to, and the token is, in `JarvisConfig`'s own words, the key to the whole
 * house. A settings screen that appears to save and does not is an app that
 * silently does nothing forever, and no JVM test can tell the difference —
 * `SharedPreferences` is stubbed out there.
 *
 * ## Finding the fields
 *
 * The screen is Compose (T-0639, one screen since T-0689), so there are no views and no resource ids.
 * The stable handle is each field's LABEL, which the kit's `Field` gives it as
 * its accessibility name ("Server URL", "Access token"), rather than a
 * positional index that quietly starts pointing at a different field the day
 * someone inserts a spacer. The fields sit in the Server fold, which starts
 * folded once a server is saved, so [field] opens it first.
 *
 * ## Two independent checks
 *
 * The values are asserted through `JarvisConfig` — the object every other part
 * of the app reads them through — AND by reopening the screen and reading them
 * off the fields. Either one alone can pass while the feature is broken: a
 * screen that writes correctly but populates its fields from somewhere else is
 * still wrong, and so is one whose fields look right because it never cleared
 * an in-memory copy.
 *
 * Does not read the token back off the screen: it is a password field, whose
 * accessibility text is the masked string, so the token is checked through
 * `JarvisConfig` only.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class SettingsPersistenceTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun serverUrlAndTokenSurviveClosingAndReopeningTheScreen() {
        openSettings()

        typeInto(URL_FIELD, SERVER_URL)
        typeInto(TOKEN_FIELD, TOKEN)
        typeInto(PIPELINE_FIELD, PIPELINE)
        typeInto(DEVICE_NAME_FIELD, DEVICE_NAME)

        Screenshots.take("SettingsPersistenceTest-filled-in")

        save()

        // 1. Through the object the rest of the app reads.
        val config = JarvisConfig(context)
        assertEquals(
            "The server URL must be stored exactly as ServerUrl.check normalised it",
            SERVER_URL,
            config.serverUrl,
        )
        assertEquals("The access token must be stored verbatim", TOKEN, config.token)
        assertEquals("The pipeline name must be stored", PIPELINE, config.pipeline)
        assertEquals("The device name must be stored", DEVICE_NAME, config.deviceName)
        assertEquals(
            "A URL and a token together are the definition of configured",
            true,
            config.isConfigured,
        )

        // 2. Through the screen, reopened from scratch.
        Activities.finishAll()
        openSettings()
        assertEquals(SERVER_URL, field(URL_FIELD).text)
        // The token field hides its characters, so only its length can be read off it.
        assertEquals(TOKEN.length, field(TOKEN_FIELD).text.length)
        assertEquals(PIPELINE, field(PIPELINE_FIELD).text)
        assertEquals(DEVICE_NAME, field(DEVICE_NAME_FIELD).text)

        Screenshots.take("SettingsPersistenceTest-reopened")
    }

    @Test
    fun anInvalidServerUrlIsRefusedAndNothingIsStored() {
        openSettings()

        // Cleartext to a public host. `ServerUrl.check` refuses it because the
        // platform's network security config would refuse the connection anyway,
        // and failing here says why. The important half of the assertion is that
        // the refusal does not half-save: a stored token with no usable URL is a
        // credential sitting on disk for no reason.
        typeInto(URL_FIELD, "http://example.com")
        typeInto(TOKEN_FIELD, TOKEN)
        tapSave()

        // "Not saved" is the screen's own words; the reason after it is ServerUrl's, which
        // instrumentation_contract_test cannot see as on-screen text.
        Waits.until("the screen's note to say the URL was refused") {
            Device.ui.findObject(By.text(Views.containingIgnoringCase("Not saved"))) != null
        }
        Screenshots.take("SettingsPersistenceTest-invalid-url")

        val config = JarvisConfig(context)
        assertEquals("A refused URL must not be stored", "", config.serverUrl)
        assertEquals("…and neither must the token that came with it", "", config.token)
        assertEquals("…so the app is still unconfigured", false, config.isConfigured)
        assertEquals(true, Activities.isResumed(SettingsActivity::class.java))
    }

    @Test
    fun aMissingTokenIsRefused() {
        openSettings()

        typeInto(URL_FIELD, SERVER_URL)
        typeInto(TOKEN_FIELD, "")
        tapSave()

        Waits.until("the screen's note to say the token is required") {
            Device.ui.findObject(By.text(Views.containingIgnoringCase("Not saved: an access token is required"))) != null
        }

        val config = JarvisConfig(context)
        assertEquals(
            "Without a token there is nothing to authenticate with, so nothing is saved",
            "",
            config.serverUrl,
        )
        assertEquals(false, config.isConfigured)

        Screenshots.take("SettingsPersistenceTest-missing-token")
    }

    @Test
    fun theDeviceIdIsStableAcrossScreenLaunches() {
        // Not cosmetic: `jarvis/device/register` identifies this install by it,
        // and one that changed between launches would look like a new device
        // needing fresh authorisation every time the app started.
        val first = JarvisConfig(context).deviceId
        openSettings()
        Activities.finishAll()
        openSettings()
        val second = JarvisConfig(context).deviceId

        assertEquals("The device id must be generated once and persisted", first, second)
        assertEquals(
            "…and it must be a real UUID, not an empty string standing in for one",
            UUID_LENGTH,
            first.length,
        )
    }

    // --- helpers ------------------------------------------------------------

    private fun openSettings() {
        val settings = Activities.launch(SettingsActivity::class.java)
        Activities.awaitResumed(settings)
        // SAVE is in the bar fixed under the scroll, so it is there once the screen is.
        Waits.until("the settings screen to finish building") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("SAVE"))) != null
        }
    }

    /** The field named [label], opening the Server fold if it is folded. */
    private fun field(label: String): UiObject2 {
        if (Device.ui.findObject(By.desc(label)) == null) {
            // The fold's header reads as one node: its title, then what it is set to.
            Device.ui.findObject(By.text(SERVER_FOLD))?.click()
        }
        return requireNotNull(Views.findScrolling(By.desc(label))) { "No $label field.\n${Device.windowDump()}" }
    }

    private fun typeInto(label: String, text: String) {
        field(label).text = text
    }

    /** Tap SAVE without asserting the outcome. */
    private fun tapSave() {
        val save = Device.ui.findObject(By.text(Views.textIgnoringCase("SAVE")))
        requireNotNull(save) { "No SAVE button on screen.\n${Device.windowDump()}" }.click()
    }

    /**
     * Tap SAVE and wait for the screen's note, which is what success looks like:
     * the screen stays open after a save (T-0689), so leaving is not the signal.
     * The note begins "Saved" and may carry the cleartext warning after it.
     */
    private fun save() {
        tapSave()
        Waits.until("the settings screen to note a successful SAVE") {
            Device.ui.findObject(By.text(SAVED)) != null
        }
    }

    private companion object {
        // Exactly the label each `Field` is given on the screen.
        const val URL_FIELD = "Server URL"
        const val TOKEN_FIELD = "Access token"
        const val PIPELINE_FIELD = "Pipeline name"
        const val DEVICE_NAME_FIELD = "Device name"
        val SERVER_FOLD: Pattern = Pattern.compile("^server\\b.*", Pattern.CASE_INSENSITIVE or Pattern.DOTALL)

        /**
         * The screen's own "Saved" note or save-bar label. Anchored: "Unsaved changes" is on
         * screen the whole time something is edited and contains "saved".
         */
        val SAVED: Pattern = Pattern.compile("^saved\\b.*", Pattern.CASE_INSENSITIVE or Pattern.DOTALL)

        /**
         * A private-range host with an explicit port: accepted by
         * `ServerUrl.check`, normalised to exactly this string (no trailing
         * slash), and it is the address `network_security_config.xml` documents.
         */
        const val SERVER_URL = "http://192.168.2.10:8123"
        const val TOKEN = "settings-persistence-test-token-4f2a"
        const val PIPELINE = "Jarvis Test Pipeline"
        const val DEVICE_NAME = "Instrumented Pixel"

        /** `UUID.randomUUID().toString()` is always 36 characters. */
        const val UUID_LENGTH = 36
    }
}
