package ai.jarvis.app.screenshot

import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.View
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import ai.jarvis.app.tasks.TaskBoard
import ai.jarvis.app.tasks.TaskProgressView
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.theme.JarvisTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * What the phone LOOKS like, on a machine with no phone.
 *
 * Robolectric runs the real Android framework on the JVM — real resources, a
 * real layout pass, a real Canvas — and Roborazzi turns the result into a PNG
 * and compares it with the golden beside this file. `verifyRoborazziDebug`
 * fails on a difference.
 *
 * ## Why this is worth having when the goldens cannot be looked at by a person
 *
 * They can. They are PNGs in the repository, reviewed in a diff like anything
 * else. What this catches is the class of change nobody notices until a device
 * is in front of them: a token that moved, a padding that collapsed, a colour
 * that stopped being read from `design/tokens.json` and became a literal.
 * `design_token_test.py` proves the *values* agree; this proves they are what
 * actually gets drawn.
 *
 * ## What the goldens are
 *
 * The console frame's bar with its brand, readout and underline;
 * the widgets every screen is built from; the held bar; the task overlay; the
 * settings widgets; the voice screen's surface and task dock; the four screen
 * states and the section strip (M64); and the generated Compose theme. Each
 * is one nameable moment — the animations are frozen at a fixed time — or it
 * would record the scheduler's mood.
 *
 * ## What it is not
 *
 * It is not a device test. Robolectric's Canvas is not a phone's GPU, text
 * shaping is not identical to a real one, and `docs/how-to/ANDROID_DEVICE_TESTS.md`
 * lists what still needs hardware. `GraphicsMode.Mode.NATIVE` is what makes the
 * pixels real enough to compare at all — without it every drawing test records
 * a blank frame and passes for the worst possible reason.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ScreenshotTest {

    @get:Rule
    val compose = createComposeRule()

    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()

    private fun golden(name: String) = "src/test/screenshots/$name.png"

    /**
     * A view, laid out and drawn onto a bitmap.
     *
     * Drawn rather than handed to `View.captureRoboImage()`, which wants the
     * view attached to an Activity ("View should have Activity") — and these
     * are the pieces screens are BUILT from, so hanging each one off a host
     * activity would be testing the activity. Drawing them is what the real
     * one does with them anyway.
     */
    private fun bitmapOf(view: View, width: Int = 900, height: Int = 0): Bitmap {
        measured(view, width, height)
        val bitmap = Bitmap.createBitmap(
            view.measuredWidth.coerceAtLeast(1),
            view.measuredHeight.coerceAtLeast(1),
            Bitmap.Config.ARGB_8888,
        )
        val canvas = Canvas(bitmap)
        canvas.drawColor(JarvisUi.BG)
        view.draw(canvas)
        return bitmap
    }

    /** Lay a view out at a fixed size, so a golden is not a race with wrap-content. */
    private fun measured(view: View, width: Int = 900, height: Int = 0): View {
        val heightSpec =
            if (height > 0) View.MeasureSpec.makeMeasureSpec(height, View.MeasureSpec.EXACTLY)
            else View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        view.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY), heightSpec)
        view.layout(0, 0, view.measuredWidth, view.measuredHeight)
        return view
    }

    // --- the instrument, in the four states a person can tell apart ---------

    // --- the widgets every screen is built from ------------------------------

    @Test
    fun `the component sheet`() {
        val column = JarvisUi.column(context).apply {
            setBackgroundColor(JarvisUi.BG)
            addView(JarvisUi.label(context, "STATUS"))
            addView(JarvisUi.hint(context, "Listening for the wake word"))
            addView(JarvisUi.readout(context, "wake 0.62  ·  vad 550 ms"))
            addView(JarvisUi.spacer(context, JarvisUi.Space.TIGHT))
            addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
            // The one filled control on a screen, and the quiet one beside it.
            addView(JarvisUi.primary(context, "PAIR") {})
            addView(JarvisUi.spacer(context, JarvisUi.Space.TIGHT))
            addView(JarvisUi.button(context, "SETTINGS") {})
        }
        bitmapOf(column).captureRoboImage(golden("components"))
    }

    @Test
    fun `the task overlay`() {
        val view = TaskProgressView(context)
        view.render(
            listOf(
                TaskBoard.Row(
                    id = "a",
                    title = "Research the boiler pressure range",
                    kind = "research",
                    status = TaskBoard.Status.RUNNING,
                    fraction = 0.4,
                    detail = "reading the handbook",
                    doneSteps = 2,
                    totalSteps = 5,
                ),
                TaskBoard.Row(
                    id = "b",
                    title = "Fix the failing tests",
                    kind = "code",
                    status = TaskBoard.Status.DONE,
                    fraction = 1.0,
                    result = "3 tests pass on jarvis/20260825-a1b2",
                    doneSteps = 4,
                    totalSteps = 4,
                ),
            ),
            summary = "2 jobs",
        )
        view.visibility = View.VISIBLE
        bitmapOf(view).captureRoboImage(golden("task-overlay"))
    }

    // --- and the generated Compose theme -------------------------------------

    @Test
    fun `the generated theme`() {
        compose.setContent {
            JarvisTheme {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .background(MaterialTheme.colorScheme.background)
                        .padding(16.dp)
                ) {
                    Text("JARVIS", style = MaterialTheme.typography.titleLarge)
                    Text("Reactor II", style = MaterialTheme.typography.bodyMedium)
                    Text(
                        "accent",
                        color = MaterialTheme.colorScheme.primary,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }
        compose.onRoot().captureRoboImage(golden("theme-panel"))
    }

    // --- the voice screen's company: the surface and the task dock ---------

    @Test
    fun `the surface's panels, one line each`() {
        // M103: what the console draws as instruments the phone says in lines
        // — an entity with its state, a room's readings, a note, a page, a job.
        val view = ai.jarvis.app.surface.SurfaceView(context)
        view.stateOf = { entity -> if (entity == "light.bed_light") "on" else null }
        view.render(
            listOf(
                ai.jarvis.app.surface.SurfaceWatch.Panel(id = "p1", kind = "entity", title = "Bed light", entity = "light.bed_light"),
                ai.jarvis.app.surface.SurfaceWatch.Panel(id = "p2", kind = "readings", title = "", area = "Garage"),
                ai.jarvis.app.surface.SurfaceWatch.Panel(id = "p3", kind = "note", title = "Boiler serviced", note = "boiler-serviced"),
                ai.jarvis.app.surface.SurfaceWatch.Panel(id = "p4", kind = "page", title = "", url = "https://example.test/handbook"),
                ai.jarvis.app.surface.SurfaceWatch.Panel(id = "p5", kind = "task", title = "Audit every sensor", task = "t1"),
            ),
        )
        bitmapOf(view).captureRoboImage(golden("voice-surface"))
    }

    @Test
    fun `the task dock under the instrument`() {
        // M103: the board's visible rows with their bars — a job that knows how
        // far it is, one that cannot know, and one that failed.
        val view = ai.jarvis.app.surface.TaskDockView(context)
        view.render(
            listOf(
                ai.jarvis.app.tasks.TaskBoard.Row(id = "t1", title = "Audit every sensor in the house", status = ai.jarvis.app.tasks.TaskBoard.Status.RUNNING, fraction = 0.4),
                ai.jarvis.app.tasks.TaskBoard.Row(id = "t2", title = "Read the handbook", kind = "research", status = ai.jarvis.app.tasks.TaskBoard.Status.RUNNING),
                ai.jarvis.app.tasks.TaskBoard.Row(id = "t3", title = "Fetch the index", status = ai.jarvis.app.tasks.TaskBoard.Status.ERROR, error = "the model server refused"),
            ),
        )
        bitmapOf(view).captureRoboImage(golden("voice-task-dock"))
    }
}
