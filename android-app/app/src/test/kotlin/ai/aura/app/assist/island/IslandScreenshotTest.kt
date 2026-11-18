package ai.aura.app.assist.island

import ai.aura.app.assist.AssistOverlay
import ai.aura.app.assist.Phase
import ai.aura.app.glass.ReactorView
import ai.aura.app.reactor.ReactorInputs
import ai.aura.app.ui.JarvisUi
import ai.aura.app.ui.theme.JarvisTokens
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Looper
import android.view.View
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowSettings
import java.time.Duration

/**
 * The wake overlay's island (T-0695) in each state, drawn by the real [AssistOverlay] over a
 * white app (the worst case for reading through), at a small, a common and a wide phone.
 * The state each picture is in is asserted first, so a golden never records the wrong state.
 *
 * ### What is not drawn
 *
 * The GL orb and the motion: Robolectric has no GL ([ReactorView.canvasOnly]) and the system's
 * animations are off, so every spring rests at its target. The morph is IslandMotionTest's.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34])
class IslandScreenshotTest {

    private var overlay: AssistOverlay? = null

    @Before
    fun pin() {
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
    }

    @After
    fun unpin() {
        overlay?.detach()
        idle()
    }

    private fun idle(ms: Long = 600) = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms))

    /** A turn on the island, at [widthDp]; [setUp] puts it in the state the picture is of. */
    private fun shot(name: String, widthDp: Int, expect: Island.State, turn: Boolean = true, setUp: AssistOverlay.() -> Unit) {
        RuntimeEnvironment.setQualifiers("w${widthDp}dp-h${widthDp * 2}dp-xhdpi")
        val context = RuntimeEnvironment.getApplication()
        val surface = AssistOverlay(context) { }
        overlay = surface
        if (!turn) surface.setUp()
        check(surface.attach(turn)) { "the overlay window was refused" }
        if (turn) {
            surface.setReactor(ReactorInputs(listening = true, turnRunning = true))
            surface.setUp()
        }
        // A reply's last word is drawn once the stream pauses, then fades in (ReplyReveal).
        idle(1500)
        assertEquals(name, expect, surface.stateForTest)

        val island = surface.rootForTest!!
        island.measure(View.MeasureSpec.UNSPECIFIED, View.MeasureSpec.UNSPECIFIED)
        island.layout(0, 0, island.measuredWidth, island.measuredHeight)
        val screen = context.resources.displayMetrics.widthPixels
        val gap = JarvisUi.dp(context, JarvisUi.Space.SNUG)
        val bitmap = Bitmap.createBitmap(screen, island.measuredHeight + 2 * gap, Bitmap.Config.ARGB_8888)
        Canvas(bitmap).apply {
            drawColor(JarvisTokens.Color.TEXT_BRIGHT)
            translate((screen - island.measuredWidth) / 2f, gap.toFloat())
            island.draw(this)
        }
        bitmap.captureRoboImage("src/test/screenshots/island-$name-${widthDp}.png")
    }

    private fun pill(widthDp: Int) = shot("pill", widthDp, Island.State.PILL) {
        setMode(Phase.LISTENING, "Listening")
        // A consent prompt on screen puts the turn in its pill.
        setHiddenForPrompt(true)
    }

    /** The reactor that talks at the bottom (T-0912): the core alone in its glow. */
    private fun bottom(widthDp: Int) = shot("bottom", widthDp, Island.State.CARD) {
        setMode(Phase.SPEAKING, "Responding")
        setTranscript("What's left on my list today?")
    }

    /** The card's words are a tap on the core away, at the top. */
    private fun AssistOverlay.open() = (rootForTest as IslandView).listener!!.onGesture(Island.Gesture.TAP)

    private fun listening(widthDp: Int) = shot("listening", widthDp, Island.State.CARD) {
        setMode(Phase.LISTENING, "Listening")
        setTranscript("Turn on the kitchen lights and dim the hall to forty percent")
        open()
    }

    private fun replying(widthDp: Int) = shot("replying", widthDp, Island.State.CARD) {
        setMode(Phase.SPEAKING, "Responding")
        setTranscript("What's left on my list today?")
        setResponse(
            "Three things. The plumber at four, a parcel to collect before six, and you said " +
                "you'd call about the car's service. Want a reminder for the call?",
        )
        open()
    }

    private fun approval(widthDp: Int) = shot("approval", widthDp, Island.State.APPROVAL) {
        setMode(Phase.THINKING, "Waiting on you")
        setTranscript("Unlock the front door for the courier")
        showApproval("Unlock the front door", "lock.front_door · unlock", onApprove = {}, onDeny = {})
    }

    /** A route held off the car, with no conversation: its own pill (made-up place and distance). */
    private fun route(widthDp: Int) = shot("route", widthDp, Island.State.ACTIVITY, turn = false) {
        setActivity(RouteActivity.Pill("Burger place on the high street", "2.4 km", 0.0, 0.0, "organic"))
    }

    @Test fun `route 360`() = route(360)
    @Test fun `route 412`() = route(412)
    @Test fun `route 600`() = route(600)
    @Test fun `pill 360`() = pill(360)
    @Test fun `pill 412`() = pill(412)
    @Test fun `pill 600`() = pill(600)
    @Test fun `bottom 360`() = bottom(360)
    @Test fun `bottom 412`() = bottom(412)
    @Test fun `listening 360`() = listening(360)
    @Test fun `listening 412`() = listening(412)
    @Test fun `listening 600`() = listening(600)
    @Test fun `replying 360`() = replying(360)
    @Test fun `replying 412`() = replying(412)
    @Test fun `replying 600`() = replying(600)
    @Test fun `approval 360`() = approval(360)
    @Test fun `approval 412`() = approval(412)
    @Test fun `approval 600`() = approval(600)
}
