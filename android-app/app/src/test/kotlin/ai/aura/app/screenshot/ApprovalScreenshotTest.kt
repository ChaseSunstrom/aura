package ai.aura.app.screenshot

import ai.aura.app.ApprovalActivity
import ai.aura.app.ui.ApprovalBridge
import ai.aura.app.ui.theme.JarvisTokens
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Looper
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.Duration

/**
 * The approval screen as the person sees it once it has armed (Phone.dc.html "Approval"): a
 * Hermes prompt carrying the T-0606 card, and a device Tier-3 prompt that has none.
 *
 * A class of its own, with a compose rule: the screen's state changes after the first frame
 * (it arms after ConsentGate.ARM_MS), and only waitForIdle reliably recomposes that in a
 * sandbox an earlier test already used.
 *
 * Does not cover the locked face (Robolectric's keyguard is never up) or a countdown that has
 * ticked (its timer is shadowed and never fires).
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
class ApprovalScreenshotTest {

    @get:Rule
    val compose = createEmptyComposeRule()

    private val app get() = RuntimeEnvironment.getApplication()

    private fun approval(id: String, action: String, reason: String, params: String, choices: List<String>?, description: String = "") =
        Intent(app, ApprovalActivity::class.java)
            .putExtra(ApprovalBridge.EXTRA_REQUEST_ID, id)
            .putExtra(ApprovalBridge.EXTRA_ACTION_ID, action)
            .putExtra(ApprovalBridge.EXTRA_REASON, reason)
            .putExtra(ApprovalBridge.EXTRA_PARAMS, params)
            .putExtra(ApprovalBridge.EXTRA_DESCRIPTION, description)
            .putExtra(ApprovalBridge.EXTRA_ASK_WHY, choices != null)
            .apply { choices?.let { putExtra(ApprovalBridge.EXTRA_CHOICES, it.toTypedArray()) } }

    private fun draw(intent: Intent, name: String) {
        val controller = Robolectric.buildActivity(ApprovalActivity::class.java, intent).setup()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(900))
        compose.waitForIdle()
        val view = controller.get().window.decorView
        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        Canvas(bitmap).apply { drawColor(JarvisTokens.Color.BG) }.also { view.draw(it) }
        bitmap.captureRoboImage("src/test/screenshots/$name.png")
        // Not destroyed: a destroyed screen's compose root stays registered in this sandbox, and
        // the next test's waitForIdle then never sees it settle (AppNotIdleException).
    }

    @Test fun `approval with a card`() {
        val reason = "The model wants to text Sam that you are on your way.\n" +
            "What: Text Sam \"on my way\"\nWhy: You asked me to tell Sam when you leave\n" +
            "Impact: Sam gets a text from your number\nUndoable: no\nHome: yes"
        val params = """{"to": "Sam", "text": "on my way"}"""
        ApprovalBridge.raiseHermesRequest(app, "golden-card", "send_message", params, reason, ApprovalBridge.ALL_CHOICES, 60, why = {}) {}
        draw(approval("golden-card", "send_message", reason, params, ApprovalBridge.ALL_CHOICES), "phone-approval-card")
    }

    @Test fun `approval for a device tier 3 action`() {
        val reason = "An automation wants to send a text from this phone."
        val params = """{"to": "+15550100", "body": "Leaving work now"}"""
        ApprovalBridge.raiseServerRequest(app, "golden-tier3", "phone.sms_send", "Send a text", 60) { _, _ -> }
        draw(
            approval("golden-tier3", "phone.sms_send", reason, params, null, description = "Send a text when you leave work"),
            "phone-approval-tier3",
        )
    }
}
