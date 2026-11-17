package ai.jarvis.app

import ai.jarvis.app.support.Activities
import ai.jarvis.app.support.Device
import ai.jarvis.app.support.JarvisTestRule
import ai.jarvis.app.support.Screenshots
import ai.jarvis.app.support.Views
import ai.jarvis.app.support.Waits
import ai.jarvis.app.compat.GrapheneCompat
import ai.jarvis.app.testing.TestHooks
import android.webkit.WebView
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.LargeTest
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The app starts, and the home screen is the home screen.
 *
 * The cheapest test in this suite and the one with the best record. Every unit
 * test in `src/test` passed on the day the APK crashed on launch with a
 * `ClassNotFoundException`, because a JVM test never loads the app's classes the
 * way ART does, never inflates a view, and never runs `Application.onCreate`.
 * "It compiles" and "it starts" are different claims, and only one of them can
 * be checked without a device.
 *
 * The assertions are therefore about existence rather than behaviour:
 *
 *  * `MainActivity` reaches RESUMED — not created-then-immediately-finished.
 *  * The console's WebView is instantiated, attached and actually laid out. It
 *    is the home (T-0667), and a WebView that fails to construct is exactly the
 *    shape of failure a JVM test never sees.
 */
@RunWith(AndroidJUnit4::class)
@LargeTest
class AppLaunchTest {

    @get:Rule
    val jarvis = JarvisTestRule()

    @Test
    fun mainActivityLaunchesAndShowsTheConsole() {
        val activity = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(activity)

        Activities.onMain {
            assertTrue(
                "MainActivity must not be finishing straight after launch",
                !activity.isFinishing,
            )

            val console = Views.firstOfType(activity, WebView::class.java)
            assertNotNull(
                "MainActivity's view tree must contain the console's WebView. A null " +
                    "here means the home never opened -- check logcat for a failure " +
                    "inside ConsolePage.",
                console,
            )
            assertTrue("The console must be attached to the window", console!!.isAttachedToWindow)
        }

        // JarvisTestRule cleared the config, so this is a first-run device and the
        // page is the offline asset: it must still draw, not sit there looking broken.
        Screenshots.take("AppLaunchTest-home")
    }

    /**
     * The one-shot tour every other test in this suite turns off.
     *
     * `JarvisTestRule` resets to a first-install state and then calls
     * `TestHooks.markFirstRunSeen`, because otherwise this behaviour lands on
     * top of every home-screen assertion in the suite — the checklist is an
     * Activity, Espresso matches the topmost window, and nine tests across
     * three classes failed reading "the home screen does not exist" while the
     * home screen was built, attached and one window down.
     *
     * That suppression is only safe while the behaviour itself is pinned
     * somewhere, so this is that somewhere. Arming the flag AFTER the rule has
     * run is what makes this a genuine first launch.
     *
     * An emulator always qualifies: "display over other apps" and the
     * battery-optimisation exemption are granted on a Settings screen a test
     * cannot drive, and `adb shell pm grant` reaches neither — so
     * `missingEssentials()` is non-empty here by construction rather than by
     * luck.
     */
    @Test
    fun aFirstLaunchWithSomethingEssentialMissingOpensTheChecklist() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assertTrue(
            "This test is meaningless unless something essential is missing, and on " +
                "an emulator something always is. If this fails, the requirement " +
                "table changed and the first-run flow no longer has a trigger.",
            GrapheneCompat.missingEssentials(context).isNotEmpty(),
        )
        TestHooks.armFirstRunChecklist(context)

        val activity = Activities.launch(MainActivity::class.java)
        Activities.awaitResumed(activity)

        Waits.until("the first-run checklist to open over the home screen") {
            Device.ui.findObject(By.text(Views.textIgnoringCase("SYSTEM CHECK"))) != null
        }

        Screenshots.take("AppLaunchTest-first-run-checklist")
    }
}
