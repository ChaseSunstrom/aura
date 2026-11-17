package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Harness
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.testing.TestHooks
import android.content.Intent
import androidx.test.platform.app.InstrumentationRegistry
import ai.jarvis.app.ui.CrashLogActivity
import ai.jarvis.app.ui.JarvisScreens
import ai.jarvis.app.ui.SystemCheckActivity
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.uiautomator.By
import androidx.test.uiautomator.StaleObjectException
import androidx.test.uiautomator.UiObject2
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.regex.Pattern

/**
 * Every button on the home screen and on Settings does something, and nothing
 * takes the app down.
 *
 * ## The two buttons that matter most
 *
 * `AndroidManifest.xml` declares `ai.jarvis.app.automation.ui.AutomationsActivity`
 * and `…AuditLogActivity` so the automation module never has to edit it, and
 * neither class exists in this build. That combination is a trap, and
 * `JarvisScreens` documents it precisely: because the components ARE declared,
 * `startActivity` does not throw `ActivityNotFoundException` — the intent
 * resolves against the manifest entry, and the app dies a moment later on the
 * main thread with "Unable to instantiate activity". A `try/catch` around
 * `startActivity` looks like it handles this and does not.
 *
 * `JarvisScreens.isPresent` is the actual fix: resolve the class first, toast if
 * it is missing. So the test asserts the TOAST, not merely the absence of a
 * crash — "nothing crashed" would also pass on a build where the crash simply
 * happens one frame later, and the toast is the only positive evidence that the
 * guard ran.
 *
 * ## How "nothing crashes" is checked
 *
 * Twice over. The instrumented suite runs inside the app's own process, so a
 * crash on the main thread takes the test run with it. And `JarvisTestRule`
 * asserts after every test that `JarvisCrashHandler` recorded nothing, which
 * catches a crash on a background thread that would otherwise pass silently.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class NavigationTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    // --- MainActivity -------------------------------------------------------

    // The home is the console in a WebView (T-0667): its buttons are the console's
    // own, not native views, so the home-button tests are gone.

    @Test
    fun jarvisSettingsAreNativeNotTheConsole() {
        // The owner, 2026-09-25: "it should only be the phone with the phone
        // settings, with all of the console settings". Configured to the house
        // alone, the native screen opens and says what it needs -- the Hermes
        // link -- instead of loading the console in a WebView. The old name
        // (JarvisSettingsActivity) is a manifest alias since T-0689: an intent
        // that names it still lands on the one settings screen.
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        TestHooks.configure(
            context,
            serverUrl = Harness.baseUrl,
            token = Harness.token,
        )
        val jarvis = Activities.launchIntent(
            SettingsActivity::class.java,
            Intent().setClassName(context, "ai.jarvis.app.JarvisSettingsActivity"),
        )
        Activities.awaitResumed(jarvis)
        Waits.until("the settings screen to say it needs the Hermes link") {
            Device.ui.findObject(By.text(Views.containingIgnoringCase("isn't paired to Hermes yet"))) != null
        }

        Screenshots.take("NavigationTest-jarvis-settings")
    }

    // --- SettingsActivity ---------------------------------------------------

    @Test
    fun settingsPhoneTasksAndAuditLogButtonsToastRatherThanCrashing() {
        openSettings()

        assertUnimplementedScreenIsHandled(
            label = "PHONE TASKS",
            className = JarvisScreens.AUTOMATIONS,
            screenshot = "NavigationTest-settings-phone-tasks",
        )
        assertUnimplementedScreenIsHandled(
            label = "AUDIT LOG",
            className = JarvisScreens.AUDIT_LOG,
            screenshot = "NavigationTest-settings-audit-log",
        )

        assertTrue(
            "SettingsActivity must survive taps on screens this build does not have",
            Activities.isResumed(SettingsActivity::class.java),
        )
    }

    @Test
    fun settingsOpensTheDiagnosticScreensItOwns() {
        openSettings()

        val systemCheck = Activities.expect(SystemCheckActivity::class.java) {
            tap("SYSTEM CHECK")
        }
        Activities.awaitResumed(systemCheck)
        Screenshots.take("NavigationTest-system-check")
        Device.ui.pressBack()
        Activities.awaitResumed(SettingsActivity::class.java)

        val crashLog = Activities.expect(CrashLogActivity::class.java) {
            tap("CRASH LOGS")
        }
        Activities.awaitResumed(crashLog)
        Screenshots.take("NavigationTest-crash-logs")
        Device.ui.pressBack()
        Activities.awaitResumed(SettingsActivity::class.java)
    }

    @Test
    fun settingsSystemAccessButtonsNeverCrash() {
        openSettings()

        // Each of these hands off to a system settings screen. On an emulator
        // image that has the screen, it opens; on one that does not,
        // SettingsActivity catches ActivityNotFoundException and toasts. Both
        // are correct, and neither may crash — which is what the crash-log
        // assertion in JarvisTestRule checks after this returns.
        for (label in SYSTEM_ACCESS_BUTTONS) {
            tapAndComeBack(label)
        }

        Screenshots.take("NavigationTest-settings-system-access")
    }

    @Test
    fun settingsTokenPasteButtonReportsWhatItDid() {
        openSettings()

        // Whether the emulator's clipboard happens to be empty is not this
        // suite's business, and both answers are correct. What must always
        // happen is that the button says which one it was: a token that
        // half-arrives with no feedback sends the user off debugging a
        // connection instead of a paste.
        //
        // The answer is the screen's own note now, not a system toast (T-0639, ScreenNote).
        locate("PASTE").click()
        Waits.until("the paste to say whether the clipboard was empty") {
            Device.ui.findObject(By.text(Views.containingIgnoringCase("Clipboard is empty"))) != null ||
                Device.ui.findObject(By.text(Views.containingIgnoringCase("Pasted"))) != null
        }

        Screenshots.take("NavigationTest-settings-paste")
    }

    // --- helpers ------------------------------------------------------------

    private fun openSettings(): SettingsActivity {
        val settings = Activities.launch(SettingsActivity::class.java)
        Activities.awaitResumed(settings)
        // SAVE is in the bar fixed under the scroll, so it is there once the screen is.
        Waits.until("the settings screen to finish building") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("SAVE"))) != null
        }
        return settings
    }

    /**
     * Tap a button by its label, scrolling it into view first if need be.
     *
     * UiAutomator rather than Espresso because several of these live inside a
     * `ScrollView` in a hierarchy with no resource ids, and because the same
     * helper then works across an activity boundary.
     */
    private fun tap(label: String) {
        // Re-locate on a stale handle, rather than reporting the app broke.
        //
        // `locate()` returns a UiObject2 that wraps one AccessibilityNodeInfo.
        // If the screen re-renders between finding it and clicking it, that node
        // is gone and `click()` throws StaleObjectException — which is what run
        // 32091666250 hit on `homePhoneButtonOpensThePhonesOwnSettings`, tapping
        // a tab on a Settings screen that was still settling. Nothing was wrong
        // with the app: the button existed before and after, just not as the
        // same node.
        //
        // Retrying cannot double-tap. The stack shows where it throws:
        //
        //     at UiObject2.getAccessibilityNodeInfo(UiObject2.java:1018)
        //     at UiObject2.click(UiObject2.java:516)
        //
        // `click()` resolves the node to compute its bounds BEFORE dispatching
        // any gesture, so a throw from there means no touch was sent. A retry
        // that found a fresh node is the first tap, not a second one.
        //
        // Bounded, and the last attempt is left to throw: a label that is
        // genuinely never clickable must still fail, and fail as itself rather
        // than as a timeout.
        var attempt = 0
        while (true) {
            try {
                locate(label).click()
                return
            } catch (stale: StaleObjectException) {
                attempt += 1
                if (attempt >= STALE_RETRIES) throw stale
                Device.ui.waitForIdle(IDLE_MS)
            }
        }
    }

    /**
     * Find a button by its label, scrolling it into view, WITHOUT clicking it.
     *
     * Split out from [tap] because of a trap that costs an emulator run to
     * diagnose. `UiAutomation` has exactly one event queue, and
     * `executeAndWaitForEvent` — which both `Toasts` and UiAutomator's own
     * scrolling are built on — takes it over for the duration of the command it
     * is given, then on the way out sets `mWaitingForEventDelivery = false` and
     * clears the queue. So a scroll *inside* the action passed to
     * [Toasts.expect] silently unsubscribes the toast wait that is wrapping it:
     * the click lands, the app toasts, and the outer filter is no longer
     * listening. It then fails with "No toast was posted at all", which points
     * at the app rather than at the harness.
     *
     * That is what run 31309094331 hit. The app was entirely correct — logcat
     * from the same second shows the guard running and the toast being shown:
     *
     *     W/JarvisScreens: ai.jarvis.app.automation.ui.AutomationsActivity is
     *                      not present in this build
     *     W/NotificationService: Toast already killed. pkg=ai.jarvis.app
     *
     * The home-screen version of the same assertion passed, because that button
     * is on screen and needs no scroll — which is exactly how a nested-wait bug
     * disguises itself as a difference between two screens.
     *
     * So: scroll here, click inside the toast window, and keep the action passed
     * to `Toasts` down to a single interaction that waits for nothing.
     */
    private fun locate(label: String): UiObject2 {
        val button = findButton(label)
        assertNotNull(
            "No button labelled \"$label\" on screen.\n${Device.windowDump()}",
            button,
        )
        return button!!
    }

    /**
     * The settings screen is folds (T-0639), so a button in a folded part is not on screen
     * until its fold is opened: [FOLD_OF] says which one, and its header starts with its name.
     */
    private fun findButton(label: String): UiObject2? {
        Views.findScrolling(By.text(Views.textIgnoringCase(label)))?.let { return it }
        val fold = FOLD_OF[label] ?: return null
        Views.findScrolling(By.text(Pattern.compile("^$fold\\b.*", Pattern.CASE_INSENSITIVE or Pattern.DOTALL)))?.click()
        Device.ui.waitForIdle(IDLE_MS)
        return Views.findScrolling(By.text(Views.textIgnoringCase(label)))
    }

    /**
     * Assert that a button pointing at a screen this build does not ship toasts
     * instead of crashing.
     *
     * Branching on whether the class exists rather than hard-coding "it does
     * not": the automation module is expected to land eventually, and a test
     * that then fails with "expected a toast" would be reporting its own
     * staleness as a product defect. Either way the assertion is real — the
     * screen opens, or the user is told it is not there.
     */
    private fun assertUnimplementedScreenIsHandled(
        label: String,
        className: String,
        screenshot: String,
    ) {
        if (classExists(className)) {
            // The automation module landed. Assert the screen actually opens.
            tap(label)
            Waits.until("$className to reach the foreground") {
                Activities.isResumed(className)
            }
            Screenshots.take("$screenshot-implemented")
            Device.ui.pressBack()
            return
        }
        // The assertion is unchanged: the person is told, not merely spared a crash. The
        // telling is the screen's own note now, not a system toast (T-0639, ScreenNote).
        locate(label).click()
        Waits.until("the screen to say $label is not in this build") {
            Device.ui.findObject(By.text(Views.containingIgnoringCase("not available in this build"))) != null
        }
        Screenshots.take(screenshot)
    }

    private fun classExists(className: String): Boolean = try {
        Class.forName(className)
        true
    } catch (t: Throwable) {
        false
    }

    /**
     * Tap something that may leave the app, then get back to Settings however
     * far it went.
     *
     * Bounded and explicit rather than a blind `pressBack()`: a settings deep
     * link can land in a nested screen of the Settings app, and a test that
     * assumed one Back would silently start driving whatever screen it happened
     * to be looking at.
     */
    private fun tapAndComeBack(label: String) {
        val device = Device.ui
        tap(label)
        device.waitForIdle(IDLE_MS)
        Screenshots.take("NavigationTest-settings-${label.lowercase().replace(' ', '-')}")

        repeat(MAX_BACK_PRESSES) {
            if (Activities.isResumed(SettingsActivity::class.java)) return
            device.pressBack()
            device.waitForIdle(IDLE_MS)
        }
        if (!Activities.isResumed(SettingsActivity::class.java)) {
            // Whatever it opened would not let us out. Relaunching keeps the
            // remaining buttons testable and still proves this one did not crash
            // — the crash-log assertion in JarvisTestRule is what covers that.
            openSettings()
        }
    }

    private companion object {
        /**
         * The buttons on Settings that leave the app, and must come back.
         *
         * This used to be eight raw Settings shortcuts sitting in a grid on the
         * Settings screen, with no indication of which were granted, two of them
         * opening a screen a button higher up already opened, and two more with
         * near-identical names and unrelated meanings. They now live on the
         * checklist, which shows each one's state and what breaks without it —
         * so what is left to walk from here is APP INFO plus the two grants
         * Settings still offers directly, because those decide whether a wake
         * word can put anything on screen at all. The checklist itself is walked
         * by `settingsOpensTheDiagnosticScreensItOwns`.
         */
        val SYSTEM_ACCESS_BUTTONS = listOf(
            "APP INFO",
            "ALLOW BACKGROUND",
            "DISPLAY OVER APPS",
        )

        /** The fold each tapped settings button sits in. */
        val FOLD_OF = mapOf(
            "PASTE" to "Server",
            "ALLOW BACKGROUND" to "Voice",
            "DISPLAY OVER APPS" to "Voice",
            "SYSTEM CHECK" to "Permissions",
            "APP INFO" to "Permissions",
            "PHONE TASKS" to "More",
            "AUDIT LOG" to "More",
            "CRASH LOGS" to "More",
        )

        const val MAX_BACK_PRESSES = 4
        const val IDLE_MS = 3_000L

        /**
         * How many times [tap] will re-find a label whose node went stale.
         *
         * Three, because staleness comes from the screen settling and a screen
         * that is still re-rendering after two idle waits is a real defect, not
         * a race. Higher would turn "this button is never stable" into a slow
         * pass.
         */
        const val STALE_RETRIES = 3
    }
}
