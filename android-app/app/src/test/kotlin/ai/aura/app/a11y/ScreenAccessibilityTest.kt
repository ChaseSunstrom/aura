package ai.aura.app.a11y

import ai.aura.app.ApprovalActivity
import ai.aura.app.SettingsActivity
import ai.aura.app.VoiceIdentityActivity
import ai.aura.app.WakeEnrolActivity
import ai.aura.app.automation.ui.ActionPolicyActivity
import ai.aura.app.automation.ui.AuditLogActivity
import ai.aura.app.automation.ui.AutomationsActivity
import ai.aura.app.car.setup.CarSetupActivity
import ai.aura.app.ui.ApprovalBridge
import ai.aura.app.ui.EmergencyCountdown
import ai.aura.app.ui.EmergencyCountdownActivity
import ai.aura.app.ui.SystemCheckActivity
import android.app.Activity
import android.content.Intent
import android.os.Looper
import android.view.ViewGroup
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.isRoot
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
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
 * The accessibility gate over the phone's real screens (T-0699): the Activities the screenshot
 * suite draws (`PhoneScreensScreenshotTest`, `ApprovalScreenshotTest`), composed as the app
 * composes them, each at the default text size and at Android's large step (1.3). The bar is
 * [ComposeAudit]'s, the one [AccessibilityTest] holds the theme's controls to: every clickable
 * is 48 dp and named, every text is AA against the pixels behind it.
 *
 * A first-run face only: Robolectric grants nothing and pairs nothing, so a screen's states
 * beyond that (a paired car, a rule being edited) are not walked here. The window is tall so
 * a whole scrolling column is one picture; a node past its bottom fails rather than passing
 * unsampled. Not a TalkBack run: a name is checked to exist, not to read well.
 *
 * Screens of the screenshot suite NOT here, and why:
 * - the console (`ConsolePage`) is a WebView: the console's own axe gate covers it;
 * - the car's map and shell goldens (`CarNavGoldenTest`, `CarShellScreenshotTest`) draw
 *   canvases on a head unit; their own change is in hand elsewhere (see the T-0699 report);
 * - the reactor goldens draw one custom canvas, the island's goldens an overlay window:
 *   the island's states are bound to their spoken descriptions in `IslandStateTest`.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w411dp-h4000dp-xhdpi")
class ScreenAccessibilityTest {

    @get:Rule
    val compose = createEmptyComposeRule()

    private val app get() = RuntimeEnvironment.getApplication()

    @Before
    fun pin() {
        // A never-idle animation would keep waitForIdle from settling (PhoneScreensScreenshotTest).
        android.animation.ValueAnimator::class.java
            .getMethod("setDurationScale", Float::class.javaPrimitiveType).invoke(null, 0f)
    }

    @After
    fun drain() {
        // Compose queues one task to apply a write and queues no other until it has run
        // (GlobalSnapshotManager.sent). The audits end with autoAdvance off and a screen's
        // write still queued; Robolectric then resets the looper and drops that task, `sent`
        // stays set for the JVM, and the next Compose test's first write is never applied:
        // CarSetupScreenshotTest and two ApprovalScreenshotTests spun 60 s each to
        // AppNotIdleException (T-1291). Run it here, so only this class's end changes: a
        // drain in TestJarvisApp.afterTest ran every class's queued work and moved two goldens.
        shadowOf(Looper.getMainLooper()).idle()
    }

    private fun <T : Activity> audit(name: String, fontScale: Float, type: Class<T>, intent: Intent? = null, scrolled: Boolean = false) {
        RuntimeEnvironment.setFontScale(fontScale)
        val controller = if (intent == null) Robolectric.buildActivity(type) else Robolectric.buildActivity(type, intent)
        controller.setup()
        // A screen that reads its store on a real thread sets its content late: wait in real time.
        val content = controller.get().findViewById<ViewGroup>(android.R.id.content)
        val deadline = System.nanoTime() + Duration.ofSeconds(30).toNanos()
        while (content.childCount == 0 && System.nanoTime() < deadline) {
            shadowOf(Looper.getMainLooper()).idle()
            Thread.sleep(10)
        }
        check(content.childCount > 0) { "$name: the screen set no content in 30 s" }
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(900))
        // A screen with a loop that never ends (a pulse, a poll) keeps waitForIdle spinning its
        // clock for ever (run 3: five screens timed out at 60 s each). The looper has settled the
        // screen; the clock is held and moved on by hand so the tree can be read.
        compose.mainClock.autoAdvance = false
        compose.mainClock.advanceTimeBy(500)
        val partial = if (scrolled) ComposeAudit.Partial() else null
        val problems = mutableListOf<String>()
        var windows = 0
        while (true) {
            windows++
            val pixels = ComposeAudit.drawn(controller.get().window.decorView)
            assertTrue("$name drew a single colour", ComposeAudit.distinctColours(pixels) > 1)
            // Every compose root of the screen: a view screen can hold more than one ComposeView.
            val roots = compose.onAllNodes(isRoot(), useUnmergedTree = true).fetchSemanticsNodes()
            problems += ComposeAudit.problems("$name at x$fontScale", roots, pixels, partial)
            if (!scrolled || !scrollOneWindow(name, roots)) break
            check(windows < 20) { "$name: still scrolling after 20 windows" }
        }
        if (partial != null) {
            problems += partial.unaudited("$name at x$fontScale")
            // The list is taller than one window: a single pass means the scroll never happened.
            if (windows < 2) problems += "$name: audited in $windows window, the list was not scrolled"
            println("$name at x$fontScale: $windows windows, ${partial.audited.size} nodes audited, ${partial.skipped.size} seen clipped")
        }
        assertEquals("$name ($windows windows):\n" + problems.joinToString("\n"), emptyList<String>(), problems)
    }

    /**
     * Scrolls the screen's one vertical scroller by a window less 1000 dp (so a row cut by one
     * window's bottom sits whole in the next) and says whether it moved; false at the end.
     */
    private fun scrollOneWindow(name: String, roots: List<SemanticsNode>): Boolean {
        val scroller = roots.firstNotNullOfOrNull { findScroller(it) } ?: error("$name: no vertical scroller to walk")
        val range = scroller.config[SemanticsProperties.VerticalScrollAxisRange]
        val before = range.value()
        if (before >= range.maxValue()) return false
        val root = roots.first()
        val step = root.size.height - 1000f * root.layoutInfo.density.density
        compose.runOnUiThread { scroller.config[SemanticsActions.ScrollBy].action!!(0f, step) }
        // The scroll animates on the held clock: hand it time until it lands.
        compose.mainClock.advanceTimeBy(3000)
        shadowOf(Looper.getMainLooper()).idle()
        check(range.value() > before) { "$name: the list did not scroll (still at $before of ${range.maxValue()})" }
        return true
    }

    private fun findScroller(node: SemanticsNode): SemanticsNode? =
        if (node.config.contains(SemanticsProperties.VerticalScrollAxisRange) && node.config.contains(SemanticsActions.ScrollBy)) node
        else node.children.firstNotNullOfOrNull { findScroller(it) }

    private fun approval(id: String, action: String, reason: String, params: String, choices: List<String>?, description: String = "") =
        Intent(app, ApprovalActivity::class.java)
            .putExtra(ApprovalBridge.EXTRA_REQUEST_ID, id)
            .putExtra(ApprovalBridge.EXTRA_ACTION_ID, action)
            .putExtra(ApprovalBridge.EXTRA_REASON, reason)
            .putExtra(ApprovalBridge.EXTRA_PARAMS, params)
            .putExtra(ApprovalBridge.EXTRA_DESCRIPTION, description)
            .apply { choices?.let { putExtra(ApprovalBridge.EXTRA_CHOICES, it.toTypedArray()) } }

    private fun cardApproval(fontScale: Float) {
        val reason = "The model wants to text Sam that you are on your way.\n" +
            "What: Text Sam \"on my way\"\nWhy: You asked me to tell Sam when you leave\n" +
            "Impact: Sam gets a text from your number\nUndoable: no\nHome: yes"
        val params = """{"to": "Sam", "text": "on my way"}"""
        ApprovalBridge.raiseHermesRequest(app, "a11y-card", "send_message", params, reason, ApprovalBridge.ALL_CHOICES, 60) {}
        audit("approval with a card", fontScale, ApprovalActivity::class.java, approval("a11y-card", "send_message", reason, params, ApprovalBridge.ALL_CHOICES))
    }

    private fun tier3Approval(fontScale: Float) {
        val reason = "An automation wants to send a text from this phone."
        val params = """{"to": "+15550100", "body": "Leaving work now"}"""
        ApprovalBridge.raiseServerRequest(app, "a11y-tier3", "phone.sms_send", "Send a text", 60) { _, _ -> }
        audit("approval for a device tier 3 action", fontScale, ApprovalActivity::class.java, approval("a11y-tier3", "phone.sms_send", reason, params, null, "Send a text when you leave work"))
    }

    @Test fun `phone settings at 1`() = audit("phone settings", 1f, SettingsActivity::class.java)
    @Test fun `phone settings at 1_3`() = audit("phone settings", 1.3f, SettingsActivity::class.java)

    @Test fun `car setup at 1`() = audit("car setup", 1f, CarSetupActivity::class.java)
    @Test fun `car setup at 1_3`() = audit("car setup", 1.3f, CarSetupActivity::class.java)

    @Test fun `phone tasks at 1`() = audit("phone tasks", 1f, AutomationsActivity::class.java)
    @Test fun `phone tasks at 1_3`() = audit("phone tasks", 1.3f, AutomationsActivity::class.java)

    @Test fun `voice on this phone at 1`() = audit("voice on this phone", 1f, VoiceIdentityActivity::class.java)
    @Test fun `voice on this phone at 1_3`() = audit("voice on this phone", 1.3f, VoiceIdentityActivity::class.java)

    @Test fun `system check at 1`() = audit("system check", 1f, SystemCheckActivity::class.java)
    @Test fun `system check at 1_3`() = audit("system check", 1.3f, SystemCheckActivity::class.java)

    // About a hundred actions, up to three chips each: some 25 000 dp, taller than the shared
    // window, and a chip past a window's bottom has no touch bounds to measure (run 4: 113 at
    // 0 x 0). A bitmap holds 32 767 px (16 000 dp at xhdpi), so the list is audited a window at a
    // time (T-1275): scrolled by 15 000 dp, redrawn and audited again until the end, and a row
    // no window held whole fails. ldpi would fit all of it and was tried: at 0.75 px a dp, a
    // glyph's edge or a pill's border lands on the pixel the contrast check reads as backdrop
    // (run 8 and 10), which is no finding.
    @Config(sdk = [34], qualifiers = "w411dp-h16000dp-xhdpi")
    @Test fun `what jarvis may do at 1`() = audit("what jarvis may do", 1f, ActionPolicyActivity::class.java, scrolled = true)
    @Config(sdk = [34], qualifiers = "w411dp-h16000dp-xhdpi")
    @Test fun `what jarvis may do at 1_3`() = audit("what jarvis may do", 1.3f, ActionPolicyActivity::class.java, scrolled = true)

    @Test fun `teach it the name at 1`() = audit("teach it the name", 1f, WakeEnrolActivity::class.java)
    @Test fun `teach it the name at 1_3`() = audit("teach it the name", 1.3f, WakeEnrolActivity::class.java)

    @Test fun `approval with a card at 1`() = cardApproval(1f)
    @Test fun `approval with a card at 1_3`() = cardApproval(1.3f)

    @Test fun `approval for a device tier 3 action at 1`() = tier3Approval(1f)
    @Test fun `approval for a device tier 3 action at 1_3`() = tier3Approval(1.3f)

    // The two screens beside them that the screenshot suite does not draw but a person reaches.
    @Test fun `the audit log at 1`() = audit("the audit log", 1f, AuditLogActivity::class.java)
    @Test fun `the audit log at 1_3`() = audit("the audit log", 1.3f, AuditLogActivity::class.java)

    // The screen finishes at once with no countdown running, so one is put up first.
    private fun countdown(fontScale: Float) {
        EmergencyCountdown.state.value = EmergencyCountdown.State(15, "Text Sam and Alex that you need help", "I need help at the house")
        try {
            audit("the emergency countdown", fontScale, EmergencyCountdownActivity::class.java)
        } finally {
            EmergencyCountdown.state.value = null
        }
    }

    @Test fun `the emergency countdown at 1`() = countdown(1f)
    @Test fun `the emergency countdown at 1_3`() = countdown(1.3f)
}
