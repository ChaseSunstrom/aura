package ai.aura.app.assist.island

import ai.aura.app.assist.AssistOverlay
import ai.aura.app.config.JarvisConfig
import ai.aura.app.glass.ReactorView
import ai.aura.app.ui.JarvisUi
import ai.aura.app.assist.island.Island.Gesture
import ai.aura.app.assist.island.Island.State
import android.graphics.Rect
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
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
import kotlin.math.abs
import kotlin.math.max

/**
 * Where the conversation's island sits and how the core alone comes and goes (T-0968): the
 * owner, 2026-11-10, "a slight animation bug on the orb popup when it shows and disappears,
 * also make it work with the dynamic island … (also configurable to be at the bottom instead)".
 * Does not cover the states themselves (IslandOverlayTest) or how the orb looks (the goldens).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
class IslandPlaceTest {

    private val app get() = RuntimeEnvironment.getApplication()
    private var overlay: AssistOverlay? = null

    /** A hole at the top centre of the 822 px screen, 40 px across, 20 to 60 px down. */
    private val hole = IslandRest.Box(391, 20, 431, 60)

    @Before
    fun up() {
        ShadowSettings.setCanDrawOverlays(true)
        motion(0f)
    }

    @After
    fun down() {
        motion(0f)
        overlay?.detach()
        idle()
        JarvisConfig(app).islandBottom = false
    }

    /** The system's animator scale: 0 lands everything at once (reduce motion), 1 runs the springs. */
    private fun motion(scale: Float) {
        android.animation.ValueAnimator::class.java
            .getMethod("setDurationScale", Float::class.javaPrimitiveType).invoke(null, scale)
        android.provider.Settings.Global.putFloat(app.contentResolver, android.provider.Settings.Global.ANIMATOR_DURATION_SCALE, scale)
    }

    private fun idle(ms: Long = 300) = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms))

    private fun open(hole: IslandRest.Box? = null): IslandView {
        val o = AssistOverlay(app, hole = { hole }) { }
        overlay = o
        assertTrue(o.attach())
        idle()
        return o.rootForTest as IslandView
    }

    private fun IslandView.gesture(g: Gesture) {
        listener!!.onGesture(g)
        idle()
    }

    private fun IslandView.params() = layoutParams as WindowManager.LayoutParams

    private fun IslandView.core(): View = (0 until childCount).map(::getChildAt).single { it is ReactorView }

    /** [child]'s bounds in this view's coordinates. */
    private fun IslandView.boundsOf(child: View) = Rect().also { child.getDrawingRect(it); offsetDescendantRectToMyCoords(child, it) }

    /** How far the core's centre is from the middle of the island's frame, px, across or down. */
    private fun IslandView.offCentre(): Float {
        val c = core()
        val margin = JarvisUi.dp(context, JarvisUi.Space.GAP)
        val x = c.translationX + c.measuredWidth / 2f - measuredWidth / 2f
        val y = c.translationY + c.measuredHeight / 2f - (measuredHeight - margin) / 2f
        return max(abs(x), abs(y))
    }

    @Test
    fun `the core alone grows and shrinks where it stands, and stays the core alone as it goes`() {
        motion(1f)
        val o = AssistOverlay(app, hole = { null }) { }
        overlay = o
        assertTrue(o.attach())
        val view = o.rootForTest as IslandView
        var seen = 0
        repeat(40) {
            idle(16)
            if (view.isLaidOut && view.core().measuredWidth > 0) {
                seen++
                assertTrue(view.bareForTest)
                assertEquals("frame $it of the grow", 0f, view.offCentre(), 1.5f)
            }
        }
        assertTrue("the grow ran in frames", seen > 10)
        o.detach()
        overlay = null
        var gone = 0
        while (view.isAttachedToWindow && gone < 120) {
            // Bare to the end: a card's backing drawn round the core as it shrank was the flash.
            assertTrue("frame $gone of the shrink", view.bareForTest)
            assertEquals("frame $gone of the shrink", 0f, view.offCentre(), 1.5f)
            idle(16)
            gone++
        }
        assertFalse(view.isAttachedToWindow)
    }

    @Test
    fun `with the resting island on, the island at the top sits on the camera hole with its word beside the hole`() {
        val view = open(hole)
        view.gesture(Gesture.FLICK_UP)
        assertEquals(State.PILL, view.state)
        val p = view.params()
        assertEquals(Gravity.TOP or Gravity.CENTER_HORIZONTAL, p.gravity)
        assertEquals(0, p.fitInsetsTypes)
        assertEquals(WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS, p.layoutInDisplayCutoutMode)
        // Centred on the hole: its 88 px pill would start above the screen, so it starts at the edge.
        assertEquals(0, p.x)
        assertEquals(0, p.y)
        val row = view.pill.getChildAt(0) as ViewGroup
        val mid = view.measuredWidth / 2
        val notch = hole.right - hole.left
        assertTrue("the core left of the hole", view.boundsOf(row.getChildAt(0)).right <= mid - notch / 2)
        assertTrue("the word right of the hole", view.boundsOf(row.getChildAt(row.childCount - 1)).left >= mid + notch / 2)
    }

    @Test
    fun `a card opened on the hole starts its rows below the hole`() {
        val view = open(hole)
        // A tap on the core alone opens its words at the top.
        view.gesture(Gesture.TAP)
        assertEquals(State.CARD, view.state)
        assertEquals(Gravity.TOP or Gravity.CENTER_HORIZONTAL, view.params().gravity)
        val column = view.card.getChildAt(0) as ViewGroup
        assertTrue("the first row clears the hole", view.boundsOf(column.getChildAt(0)).top >= hole.bottom - view.params().y)
    }

    @Test
    fun `with no hole the island at the top keeps its place under the status bar`() {
        val view = open(null)
        view.gesture(Gesture.FLICK_UP)
        val p = view.params()
        assertEquals(Gravity.TOP or Gravity.CENTER_HORIZONTAL, p.gravity)
        assertTrue(p.fitInsetsTypes != 0)
        assertEquals(JarvisUi.dp(app, JarvisUi.Space.SNUG), p.y)
    }

    @Test
    fun `the bottom setting keeps every state at the bottom, and only a talking turn is the core alone`() {
        JarvisConfig(app).islandBottom = true
        val view = open(hole)
        assertTrue(view.bareForTest)
        view.gesture(Gesture.TAP)
        assertEquals(State.CARD, view.state)
        assertEquals(Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL, view.params().gravity)
        assertFalse("a card opened shows its words", view.bareForTest)
        // Closed again, then put away: the core alone, then its pill, still at the bottom.
        view.gesture(Gesture.FLICK_UP)
        assertTrue(view.bareForTest)
        view.gesture(Gesture.FLICK_UP)
        assertEquals(State.PILL, view.state)
        assertEquals(Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL, view.params().gravity)
        assertFalse(view.bareForTest)
    }
}
