package ai.jarvis.app.screenshot

import ai.jarvis.app.SettingsActivity
import ai.jarvis.app.VoiceIdentityActivity
import ai.jarvis.app.WakeEnrolActivity
import ai.jarvis.app.automation.ui.ActionPolicyActivity
import ai.jarvis.app.automation.ui.AutomationsActivity
import ai.jarvis.app.car.setup.CarSetupActivity
import ai.jarvis.app.ui.SystemCheckActivity
import ai.jarvis.app.ui.theme.JarvisThemes
import ai.jarvis.app.ui.theme.JarvisTokens
import ai.jarvis.app.ui.theme.PersonaAccent
import android.app.Activity
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Looper
import android.view.ViewGroup
import androidx.compose.runtime.snapshots.Snapshot
import androidx.test.core.app.ApplicationProvider
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.Duration

/**
 * The native screens the console's Settings → This phone opens, drawn by the real
 * Activities (the owner, 2026-09-27: "make them match the look of everything else, and
 * consistent"). Each is a first-run phone on a tall window, so the whole column is one
 * picture; what they must share with the console is its register -- sentence-case
 * headings, hairline rows, quiet controls and one filled primary.
 *
 * A test that names no pack wears the default one (AURA: her space behind the words, T-0949);
 * the two `-jarvis` pictures are the same screens in the JARVIS pack, which a change to the
 * stage under a starfield must not move by a pixel.
 *
 * Does not cover the state a real phone reaches (a server, granted permissions, a
 * paired car): Robolectric grants nothing, so each screen shows its first-run face.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w411dp-h1600dp-xhdpi")
class PhoneScreensScreenshotTest {

    @Before
    fun pin() {
        android.animation.ValueAnimator::class.java
            .getMethod("setDurationScale", Float::class.javaPrimitiveType).invoke(null, 0f)
        // Settings prints the app's version and build, which CI sets per pipeline
        // (AURA_VERSION_NAME, AURA_VERSION_CODE): unpinned, the golden recorded here as
        // 1.0.0 (build 1) failed apk:build as 1.0.1695, then as build 1697.
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        shadowOf(context.packageManager).getInternalMutablePackageInfo(context.packageName).apply {
            versionName = "1.0.0"
            longVersionCode = 1L
        }
    }

    @After
    fun back() = wear(JarvisThemes.DEFAULT_ID)

    // In a snapshot: a bare write reaches Compose's global observer and the later Compose
    // screenshots never get idle (IslandRestGoldenTest).
    private fun wear(pack: String) =
        Snapshot.withMutableSnapshot { PersonaAccent.set(ApplicationProvider.getApplicationContext(), pack) }

    private fun <T : Activity> draw(type: Class<T>, name: String, pack: String = JarvisThemes.DEFAULT_ID) {
        wear(pack)
        val controller = Robolectric.buildActivity(type).setup()
        // Phone tasks sets its content only after reading the task store on Dispatchers.IO,
        // a real thread the looper's clock does not wait for: on a loaded runner the picture
        // was taken first and was all ground (apk:build, pipeline 808). Wait for the first
        // content, in real time, before the settle.
        val content = controller.get().findViewById<ViewGroup>(android.R.id.content)
        val deadline = System.nanoTime() + Duration.ofSeconds(30).toNanos()
        while (content.childCount == 0 && System.nanoTime() < deadline) {
            shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
        check(content.childCount > 0) { "$name: the screen set no content in 30 s" }
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(600))
        val view = controller.get().window.decorView
        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        Canvas(bitmap).apply { drawColor(JarvisTokens.Color.BG) }.also { view.draw(it) }
        bitmap.captureRoboImage("src/test/screenshots/$name.png")
        controller.pause().stop().destroy()
    }

    @Test fun `phone settings`() = draw(SettingsActivity::class.java, "phone-settings")

    @Test fun `car setup`() = draw(CarSetupActivity::class.java, "phone-car-setup")

    @Test fun `phone tasks`() = draw(AutomationsActivity::class.java, "phone-tasks")

    @Test fun `voice on this phone`() = draw(VoiceIdentityActivity::class.java, "phone-voice")

    @Test fun `system check`() = draw(SystemCheckActivity::class.java, "phone-system-check")

    @Test fun `what jarvis may do`() = draw(ActionPolicyActivity::class.java, "phone-action-policy")

    @Test fun `teach it the name`() = draw(WakeEnrolActivity::class.java, "phone-wake-enrol")

    @Test fun `phone settings in the JARVIS pack`() =
        draw(SettingsActivity::class.java, "phone-settings-jarvis", JarvisThemes.JARVIS.id)

    @Test fun `system check in the JARVIS pack`() =
        draw(SystemCheckActivity::class.java, "phone-system-check-jarvis", JarvisThemes.JARVIS.id)
}
