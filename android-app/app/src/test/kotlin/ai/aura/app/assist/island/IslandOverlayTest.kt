package ai.aura.app.assist.island

import ai.aura.app.assist.AssistOverlay
import ai.aura.app.ui.JarvisUi
import ai.aura.app.ui.theme.JarvisTokens
import ai.aura.app.assist.island.Island.Gesture
import ai.aura.app.assist.island.Island.State
import android.os.Looper
import android.view.Gravity
import android.view.WindowManager
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
 * The wake overlay on the island (T-0695): which state the conversation and the hand put it
 * in, and what the window does with it. Does not cover how a state looks (IslandScreenshotTest)
 * or the spring itself (IslandMotionTest).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
class IslandOverlayTest {

    private var dismissed = 0
    private lateinit var overlay: AssistOverlay

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
        overlay = AssistOverlay(RuntimeEnvironment.getApplication()) { dismissed++ }
        assertTrue(overlay.attach())
        idle()
    }

    @After
    fun down() {
        overlay.detach()
        idle()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(300))

    private fun island() = overlay.rootForTest as IslandView

    private fun gesture(g: Gesture) {
        island().listener!!.onGesture(g)
        idle()
    }

    private fun params() = island().layoutParams as WindowManager.LayoutParams

    private fun dims() = params().flags and WindowManager.LayoutParams.FLAG_DIM_BEHIND != 0

    @Test
    fun `a turn opens at the bottom as the core alone in its glow, with nothing behind it dimmed`() {
        // The owner, 2026-11-09 (T-0912): "just be the core, not a box around it ... not the entire screen".
        assertEquals(State.CARD, overlay.stateForTest)
        assertEquals(Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL, params().gravity)
        assertFalse(dims())
        assertEquals(0, params().flags and WindowManager.LayoutParams.FLAG_BLUR_BEHIND)
        assertEquals(WindowManager.LayoutParams.WRAP_CONTENT, params().width)
        assertTrue(island().bareForTest)
        assertEquals(0f, island().card.alpha)
        val app = RuntimeEnvironment.getApplication()
        val room = JarvisUi.dp(app, JarvisTokens.Material.Floating.GLOW_DP) + 2 * JarvisUi.dp(app, JarvisUi.Space.GAP)
        assertEquals(room, island().measuredWidth)
    }

    @Test
    fun `a tap on the core at the bottom opens its words at the top`() {
        gesture(Gesture.TAP)
        assertEquals(State.CARD, overlay.stateForTest)
        assertEquals(Gravity.TOP or Gravity.CENTER_HORIZONTAL, params().gravity)
        assertFalse(island().bareForTest)
        assertEquals(1f, island().card.alpha)
        assertTrue(dims())
    }

    @Test
    fun `a tool at work sends the turn to its pill at the top, and the phone stays usable`() {
        overlay.setTools(ai.aura.app.assist.ToolRun().apply { started("web_search", 1, 0, 1, "searching") })
        idle()
        assertEquals(State.PILL, overlay.stateForTest)
        assertEquals(Gravity.TOP or Gravity.CENTER_HORIZONTAL, params().gravity)
        assertFalse(dims())

        // The next conversation is not sent up by this one's tool.
        overlay.detach()
        idle()
        assertTrue(overlay.attach())
        idle()
        assertEquals(State.CARD, overlay.stateForTest)
        assertEquals(Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL, params().gravity)
    }

    @Test
    fun `a flick puts the turn in its pill and clears the dim, a second ends the conversation`() {
        gesture(Gesture.FLICK_UP)
        assertEquals(State.PILL, overlay.stateForTest)
        assertFalse(dims())
        assertEquals(0, dismissed)
        gesture(Gesture.FLICK_UP)
        assertEquals(1, dismissed)
    }

    @Test
    fun `a tap or a pull opens the pill again`() {
        gesture(Gesture.FLICK_UP)
        gesture(Gesture.TAP)
        assertEquals(State.CARD, overlay.stateForTest)
        gesture(Gesture.FLICK_UP)
        gesture(Gesture.PULL_DOWN)
        assertEquals(State.CARD, overlay.stateForTest)
    }

    @Test
    fun `a consent prompt on screen holds the turn as its pill, and its answer brings the card back`() {
        overlay.setHiddenForPrompt(true)
        idle()
        assertEquals(State.PILL, overlay.stateForTest)
        assertFalse(dims())
        overlay.setHiddenForPrompt(false)
        idle()
        assertEquals(State.CARD, overlay.stateForTest)
    }

    @Test
    fun `a turn put away stays a pill after a prompt comes and goes`() {
        gesture(Gesture.FLICK_UP)
        overlay.setHiddenForPrompt(true)
        idle()
        overlay.setHiddenForPrompt(false)
        idle()
        assertEquals(State.PILL, overlay.stateForTest)
    }

    @Test
    fun `the pill cannot be opened over a prompt that is still on screen`() {
        overlay.setHiddenForPrompt(true)
        idle()
        gesture(Gesture.TAP)
        assertEquals(State.PILL, overlay.stateForTest)
        assertFalse(dims())
        overlay.setHiddenForPrompt(false)
        idle()
        assertEquals(State.CARD, overlay.stateForTest)
    }

    @Test
    fun `an approval opens over a pill and is never flicked away unanswered`() {
        gesture(Gesture.FLICK_UP)
        var approved = false
        overlay.showApproval("Unlock the front door", "", onApprove = { approved = true }, onDeny = {})
        idle()
        assertEquals(State.APPROVAL, overlay.stateForTest)
        assertTrue(dims())
        gesture(Gesture.FLICK_UP)
        assertEquals(State.APPROVAL, overlay.stateForTest)
        assertFalse(approved)
        overlay.clearApproval()
        idle()
        assertEquals(State.PILL, overlay.stateForTest)
    }

    @Test
    fun `a notice opens the island again if it was put out of the way`() {
        gesture(Gesture.FLICK_UP)
        overlay.showNotice("Parcel delivered", "At the front door")
        idle()
        assertEquals(State.CARD, overlay.stateForTest)
    }

    @Test
    fun `the window goes once the island has shrunk to nothing`() {
        val view = island()
        assertTrue(view.isAttachedToWindow)
        overlay.detach()
        idle()
        assertEquals(State.HIDDEN, view.state)
        assertFalse(view.isAttachedToWindow)
        assertFalse(overlay.isShowing)
    }
}
