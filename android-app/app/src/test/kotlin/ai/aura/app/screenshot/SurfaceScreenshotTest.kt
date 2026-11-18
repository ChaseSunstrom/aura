package ai.aura.app.screenshot

import ai.aura.app.JarvisAssistActivity
import ai.aura.app.assist.Phase
import ai.aura.app.glass.ReactorView
import ai.aura.app.reactor.ReactorInputs
import ai.aura.app.ui.theme.JarvisTokens
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import com.github.takahirom.roborazzi.captureRoboImage
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
 * The assist popup (`JarvisAssistActivity`) mid-question, drawn by the real
 * Activity rather than by a stand-in body. The phone's home is the console in a
 * WebView, which Robolectric cannot draw, so it has no golden here.
 *
 * ### What is not drawn
 *
 * The GL orb. Robolectric has no GL, and [ReactorView.canvasOnly] pins every
 * reactor to its Canvas path on a stopped clock, so the 3D setting draws its
 * software fallback here.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class SurfaceScreenshotTest {

    private fun golden(name: String) = "src/test/screenshots/$name.png"

    @Before
    fun pin() {
        // Robolectric runs frames synchronously: an endlessly repeating animator
        // would never let the looper go idle, and a boot that plays would put the
        // panels mid-condense in the picture.
        android.animation.ValueAnimator::class.java
            .getMethod("setDurationScale", Float::class.javaPrimitiveType).invoke(null, 0f)
    }

    private fun idle(ms: Long = 600) = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms))

    private fun reactors(view: View): List<ReactorView> = when (view) {
        is ReactorView -> listOf(view)
        is ViewGroup -> (0 until view.childCount).flatMap { reactors(view.getChildAt(it)) }
        else -> emptyList()
    }

    /** Draw [view] as laid out, over the room's ground so nothing reads as a hole. */
    private fun picture(view: View): Bitmap {
        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        canvas.drawColor(JarvisTokens.Color.BG)
        view.draw(canvas)
        return bitmap
    }

    /**
     * The popup's card, measured at its own width (`sizeAsCard`), with a turn
     * on it. Unconfigured, the Activity builds its card and hands off to
     * Settings without opening a conversation — so the card is drawn with no
     * socket behind it and the turn below is the test's, said through the
     * same Ui callbacks a conversation uses.
     */
    private fun assist(name: String, reply: String? = null) {
        val controller = Robolectric.buildActivity(JarvisAssistActivity::class.java).setup()
        val activity = controller.get()
        activity.onMode(Phase.LISTENING, "Listening")
        activity.onReactor(ReactorInputs(listening = true, turnRunning = true))
        activity.onAmplitude(0.12f)
        activity.onTranscript(if (reply == null) "Turn on the bed light" else "What's on the page?")
        reply?.let(activity::onResponse)
        // A reply's last word is drawn once the stream pauses, then fades in (ReplyReveal, T-0728):
        // the picture is of the settled reply.
        idle(if (reply == null) 600 else 1500)
        val card = activity.findViewById<ViewGroup>(android.R.id.content).getChildAt(0)
        val width = JarvisTokens.Size.PANEL_MAX * activity.resources.displayMetrics.density
        card.measure(
            View.MeasureSpec.makeMeasureSpec(width.toInt(), View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
        )
        card.layout(0, 0, card.measuredWidth, card.measuredHeight)
        // The reply's picture is about its words. The core's frozen pose depended on which test
        // composed a core first in the JVM (CI's full suite drew it tilted, this class alone
        // face-on), which failed apk:build in pipeline 675; it is left out of this one.
        if (reply != null) reactors(card).forEach { it.visibility = View.INVISIBLE }
        picture(card).captureRoboImage(golden(name))
        controller.pause().stop().destroy()
    }

    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
    fun `the assist popup mid-question`() = assist("assist-popup")

    /** The owner's NUFI reply (T-0727): a list with bold labels, never literal ** or inline "- ". */
    @Test
    @Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi")
    fun `the assist popup draws a reply's Markdown`() {
        val cases = org.json.JSONObject(java.io.File("../../tests/contracts/markdown_text.json").readText()).getJSONArray("cases")
        val nufi = (0 until cases.length()).map { cases.getJSONObject(it).getString("md") }.first { "NUFI" in it }
        assist("assist-popup-markdown", nufi)
    }
}
