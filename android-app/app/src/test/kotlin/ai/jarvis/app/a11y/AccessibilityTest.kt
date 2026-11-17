package ai.jarvis.app.a11y

import android.app.Activity
import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.View
import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.isRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Density
import ai.jarvis.app.car.setup.CarChoice
import ai.jarvis.app.car.setup.CarSetupScreen
import ai.jarvis.app.car.setup.Gate
import ai.jarvis.app.car.setup.GateReport
import ai.jarvis.app.car.setup.Remedy
import ai.jarvis.app.car.setup.SetupActions
import ai.jarvis.app.car.setup.SetupNotice
import ai.jarvis.app.car.setup.SetupStep
import ai.jarvis.app.car.setup.SetupUi
import ai.jarvis.app.car.setup.staticIpFor
import ai.jarvis.app.ui.v2.V2
import androidx.compose.foundation.layout.offset
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.onNodeWithTag
import ai.jarvis.app.ui.v2.ActionButton
import ai.jarvis.app.ui.v2.Body
import ai.jarvis.app.ui.v2.Figure
import ai.jarvis.app.ui.v2.Headline
import ai.jarvis.app.ui.v2.HoldButton
import ai.jarvis.app.ui.v2.Label
import ai.jarvis.app.ui.v2.Projection
import ai.jarvis.app.ui.v2.RuleChip
import ai.jarvis.app.ui.v2.Screen
import ai.jarvis.app.ui.v2.ScreenState
import ai.jarvis.app.ui.v2.Stage
import ai.jarvis.app.ui.v2.Tone
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.core.app.ApplicationProvider
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.theme.JarvisTheme
import ai.jarvis.app.ui.theme.JarvisTokens
import com.google.android.apps.common.testing.accessibility.framework.AccessibilityCheckPreset
import com.google.android.apps.common.testing.accessibility.framework.AccessibilityCheckResult.AccessibilityCheckResultType
import com.google.android.apps.common.testing.accessibility.framework.AccessibilityHierarchyCheckResult
import com.google.android.apps.common.testing.accessibility.framework.Parameters
import com.google.android.apps.common.testing.accessibility.framework.uielement.AccessibilityHierarchyAndroid
import com.google.android.apps.common.testing.accessibility.framework.utils.contrast.BitmapImage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.util.Locale

/**
 * The phone's accessibility gate (GLASS_HUD §12; the brief's "Compose
 * accessibility checks"), on the JVM beside the screenshot tests.
 *
 * Two halves, one bar — 48 dp targets, a name for everything a finger or a
 * screen reader lands on, AA contrast where a pixel can be sampled:
 *
 * - **Views**, through Google's Accessibility Test Framework: the same checks
 *   Play's pre-launch report and Espresso's `AccessibilityChecks` run
 *   (TouchTargetSize, SpeakableTextPresent, TextContrast from a screenshot,
 *   DuplicateSpeakableText, …) over a sheet of the widgets every screen is
 *   built from (`JarvisUi`). Any ERROR fails; WARNINGs are printed.
 * - **Compose**, through the semantics tree: every clickable node ≥ 48 dp
 *   and named (text or content description); every text node's colour ≥ 4.5:1
 *   (3:1 large) against the pixels at the corners of its own bounds in the
 *   rendered image, which is the rendered backdrop and not the theme's claim.
 *
 * The Compose fixture is Material 3 under `JarvisTheme` — the theme's own
 * colours, type and shapes, no literal — so the check exercises the tokens as
 * a screen will. As the glass migration lands Compose screens they add their
 * root composable to `composeSubjects` and are held to the same bar.
 *
 * What this is not: a device test. Robolectric's Canvas draws the contrast
 * sample; text shaping and Material's ripple are not a phone's, and the
 * touch-target check reads layout, not a finger. It is the check that can
 * run on every commit; `docs/how-to/ANDROID_DEVICE_TESTS.md` lists the rest.
 */
@RunWith(RobolectricTestRunner::class)
// Tall: a scrolling screen at large type is longer than a phone, and a control past the window's
// bottom has clipped touch bounds (the car setup's static-ip button measured 18 px of its 144).
@Config(sdk = [34], qualifiers = "w411dp-h4000dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class AccessibilityTest {

    @get:Rule
    // The Android rule rather than the plain one: it exposes the host activity,
    // whose content view is what the contrast check draws.
    val compose = createAndroidComposeRule<ComponentActivity>()

    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()

    // --- the View widgets, through ATF ------------------------------------

    /** A sheet of the widgets a screen is assembled from, laid out at a phone's width. */
    private fun widgetSheet(): View = JarvisUi.column(context).apply {
        setBackgroundColor(JarvisUi.BG)
        addView(JarvisUi.label(context, "CONTROLS"))
        addView(JarvisUi.hint(context, "Hold to talk, or type below."))
        addView(JarvisUi.primary(context, "PAIR") {})
        addView(JarvisUi.spacer(context, JarvisUi.Space.TIGHT))
        addView(JarvisUi.button(context, "SETTINGS") {})
        addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
        addView(JarvisUi.field(context, hint = "Server", value = "https://jarvis.example"))
        addView(JarvisUi.spacer(context, JarvisUi.Space.GAP))
        measure(
            View.MeasureSpec.makeMeasureSpec(PHONE_WIDTH_PX, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
        )
        layout(0, 0, measuredWidth, measuredHeight)
    }

    @Test
    fun `every widget passes the accessibility framework's checks`() {
        // ATF reads each view's screen rectangle the way TalkBack does, through
        // the window it is attached to; a free-standing view has no window and
        // the framework dereferences null. So the sheet is hosted in a bare
        // Activity — the host is scaffolding, not the thing under test.
        val activity = Robolectric.buildActivity(Activity::class.java).setup().get()
        val sheet = widgetSheet()
        activity.setContentView(sheet, ViewGroup.LayoutParams(PHONE_WIDTH_PX, sheet.measuredHeight))
        sheet.measure(
            View.MeasureSpec.makeMeasureSpec(PHONE_WIDTH_PX, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(sheet.measuredHeight, View.MeasureSpec.EXACTLY),
        )
        sheet.layout(0, 0, sheet.measuredWidth, sheet.measuredHeight)
        val bitmap = drawn(sheet)
        val checks = AccessibilityCheckPreset.getAccessibilityHierarchyChecksForPreset(AccessibilityCheckPreset.LATEST)
        val hierarchy = AccessibilityHierarchyAndroid.newBuilder(sheet).build()
        val parameters = Parameters().apply { putScreenCapture(BitmapImage(bitmap)) }
        val results = checks.flatMap { it.runCheckOnHierarchy(hierarchy, null, parameters) }
        val describe = { r: AccessibilityHierarchyCheckResult ->
            "${r.sourceCheckClass.simpleName} on ${r.element?.className ?: "?"}" +
                "${r.element?.text?.let { " \"$it\"" } ?: ""}: ${r.getMessage(Locale.ENGLISH)}"
        }
        results.filter { it.type == AccessibilityCheckResultType.WARNING }.forEach { println("a11y warning: ${describe(it)}") }
        val errors = results.filter { it.type == AccessibilityCheckResultType.ERROR }.map(describe)
        // The ratchet: a known error is reported, an unknown one fails, and a known
        // one that is gone fails too so its line is removed rather than kept as slack.
        val fresh = errors.filter { e -> KNOWN_VIEW_FINDINGS.none { e.startsWith(it) } }
        val stale = KNOWN_VIEW_FINDINGS.filter { k -> errors.none { it.startsWith(k) } }
        errors.filter { e -> KNOWN_VIEW_FINDINGS.any { e.startsWith(it) } }.forEach { println("a11y known: $it") }
        assertEquals("ATF errors on the widget sheet:\n" + fresh.joinToString("\n"), emptyList<String>(), fresh)
        assertEquals("known ATF findings no longer seen — remove them from KNOWN_VIEW_FINDINGS:\n" + stale.joinToString("\n"), emptyList<String>(), stale)
        assertTrue("the sheet drew nothing to sample contrast from", bitmap.width > 0 && bitmap.height > 0)
    }

    /**
     * A view drawn onto a bitmap over the room colour: the picture the contrast
     * checks sample. Drawn directly (as ScreenshotTest does) rather than through
     * a window capture, which Robolectric never delivers a frame for.
     */
    private fun drawn(view: View): Bitmap {
        val bitmap = Bitmap.createBitmap(view.width.coerceAtLeast(1), view.height.coerceAtLeast(1), Bitmap.Config.ARGB_8888)
        Canvas(bitmap).also { canvas ->
            canvas.drawColor(JarvisUi.BG)
            view.draw(canvas)
        }
        return bitmap
    }

    // --- Compose, through semantics ----------------------------------------

    /**
     * The composables under test: the theme's own controls, the v2 kit every native screen is
     * built from, and the car setup's screen in each moment its screenshots draw. The phone's
     * Activities are [ScreenAccessibilityTest]'s, which composes the real ones.
     */
    private val composeSubjects: Map<String, @androidx.compose.runtime.Composable () -> Unit> = mapOf(
        "theme controls" to {
            JarvisTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    Column(Modifier.fillMaxWidth().padding(JarvisTokens.Space.SECTION.dp)) {
                        Text("Kitchen", style = MaterialTheme.typography.titleLarge)
                        Text("The kitchen is 22 °C.", style = MaterialTheme.typography.bodyMedium)
                        Text(
                            "updated just now",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.labelSmall,
                        )
                        Row {
                            Button(onClick = {}) { Text("Approve") }
                            // The glyph is decoration; the name is on the control (§12:
                            // the title-bar verbs are 48 dp even when the glyph is 20).
                            IconButton(onClick = {}, modifier = Modifier.semantics { contentDescription = "Dismiss Kitchen" }) {
                                Text("×", style = MaterialTheme.typography.titleLarge)
                            }
                        }
                    }
                }
            }
        },
        "v2 kit" to {
            Stage {
                Column(
                    Modifier.fillMaxWidth().padding(JarvisTokens.Space.WIDE.dp),
                    verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.WIDE.dp),
                ) {
                    Projection(Modifier.fillMaxWidth()) {
                        Label("Kitchen")
                        Headline("The lights are on")
                        Body("Two of four, at 60%.", dim = true)
                        Figure("21.5", "°C")
                    }
                    Projection(Modifier.fillMaxWidth(), tone = Tone.WARN) {
                        Label("Needs your OK", tone = Tone.WARN)
                        Headline("Text Sam “on my way”?")
                        HoldButton("Hold to send", {}, Modifier.fillMaxWidth())
                        Row(horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp)) {
                            ActionButton("Change", {})
                            ActionButton("Don't send", {})
                        }
                        RuleChip("Ask every time", selected = true, onClick = {})
                        RuleChip("Allow when I'm home", selected = false, onClick = {})
                    }
                    ScreenState(Screen.OFFLINE, "The house is not answering.", action = "Retry", onAction = {})
                    ScreenState(Screen.ERROR, "Pairing failed: the code ran out.")
                }
            }
        },
    )

    /** A getter: [carSetupMoments] is declared below, and an initialiser here would read it null. */
    private val carSetupSubjects: Map<String, @androidx.compose.runtime.Composable () -> Unit>
        get() = carSetupMoments.entries.associate { (moment, ui) ->
            val content: @androidx.compose.runtime.Composable () -> Unit = { JarvisTheme { CarSetupScreen(ui, SetupActions()) } }
            "car setup: $moment" to content
        }

    @Test
    fun `every clickable composable is 48 dp and named, every text clears AA on its backdrop, at font scale 1 and 1_3`() {
        val problems = mutableListOf<String>()
        // One setContent per test (the rule allows no second): the subject is state the one
        // composition reads, so each swap recomposes it in place.
        var showing by mutableStateOf<Pair<Float, @androidx.compose.runtime.Composable () -> Unit>?>(null)
        compose.setContent {
            showing?.let { (fontScale, content) ->
                val density = LocalDensity.current
                CompositionLocalProvider(LocalDensity provides Density(density.density, fontScale)) { content() }
            }
        }
        for (fontScale in FONT_SCALES) for ((name, content) in composeSubjects + carSetupSubjects) {
            compose.runOnUiThread {
                showing = fontScale to content
                androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
            }
            // The clock is held and moved by hand: a subject with a loop that never ends keeps
            // waitForIdle spinning (run 6: this test timed out at 60 s).
            compose.mainClock.autoAdvance = false
            compose.mainClock.advanceTimeBy(500)
            org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(600))
            // The unmerged tree: merged, a Button's text is the Button, whose
            // bounds are the pill and whose corners are outside its own fill.
            val roots = compose.onAllNodes(isRoot(), useUnmergedTree = true).fetchSemanticsNodes()
            // The composition's own view, drawn: the rendered backdrop under each text.
            val pixels = ComposeAudit.drawn(compose.activity.findViewById<ViewGroup>(android.R.id.content))
            assertTrue("$name drew a single colour: nothing to sample contrast from", ComposeAudit.distinctColours(pixels) > 1)
            problems += ComposeAudit.problems("$name at x$fontScale", roots, pixels)
        }
        assertEquals("Compose accessibility problems:\n" + problems.joinToString("\n"), emptyList<String>(), problems)
    }

    /**
     * T-1323: the audit above samples a text's four corners, so a star under the middle of a
     * word (or one a pixel off a corner) goes unseen. Here the star is put there on purpose: the
     * brightest star of the bare stage's sky is found, a dim [Body] of blank characters (nothing
     * inked, so every pixel of its box is backdrop) is laid with its corner on that star, and every
     * pixel of the box and the margin the audit reads is held to 4.5:1 against the dim colour.
     */
    @Test
    fun `dim stage text keeps AA over a star directly under it, at font scale 1 and 1_3`() =
        starUnderText("dim stage text", V2.TextDim) { m -> Body("            ", m, dim = true) }

    /** T-1327: the same star-under-the-text measure for the kit's [Label] (faint, uppercase). */
    @Test
    fun `label on the stage keeps AA over a star directly under it, at font scale 1 and 1_3`() =
        starUnderText("stage label", V2.TextFaint) { m -> Label("            ", m) }

    /** T-1327: and for [Headline] (the display face, V2.Text). */
    @Test
    fun `headline on the stage keeps AA over a star directly under it, at font scale 1 and 1_3`() =
        starUnderText("stage headline", V2.Text) { m -> Headline("            ", m) }

    private fun starUnderText(
        what: String,
        ink: androidx.compose.ui.graphics.Color,
        text: @androidx.compose.runtime.Composable (Modifier) -> Unit,
    ) {
        var scale by mutableStateOf(1f)
        var at by mutableStateOf<androidx.compose.ui.unit.IntOffset?>(null)
        compose.setContent {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, scale)) {
                Stage {
                    at?.let { p -> text(Modifier.offset { p }.testTag("dim")) }
                }
            }
        }
        val content = { compose.activity.findViewById<ViewGroup>(android.R.id.content) }
        val problems = mutableListOf<String>()
        for (fontScale in FONT_SCALES) {
            compose.runOnUiThread {
                scale = fontScale
                at = null
                androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
            }
            compose.waitForIdle()
            val sky = ComposeAudit.drawn(content())
            var sx = 0
            var sy = 0
            var best = -1
            val row = IntArray(sky.width)
            for (y in 0 until sky.height) {
                sky.getPixels(row, 0, sky.width, 0, y, sky.width, 1)
                for (x in row.indices) {
                    val c = row[x]
                    val l = android.graphics.Color.red(c) + android.graphics.Color.green(c) + android.graphics.Color.blue(c)
                    if (l > best) { best = l; sx = x; sy = y }
                }
            }
            assertTrue("x$fontScale: the stage's sky has no star to put under the text", best > 600)
            compose.runOnUiThread {
                at = androidx.compose.ui.unit.IntOffset(sx - 1, sy - 1)
                androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
            }
            compose.waitForIdle()
            val under = ComposeAudit.drawn(content())
            val b = compose.onNodeWithTag("dim").fetchSemanticsNode().boundsInWindow
            val left = (b.left - 2).toInt().coerceAtLeast(0)
            val right = (b.left + b.width / 2).toInt().coerceAtMost(under.width - 1)
            val top = (b.top - 2).toInt().coerceAtLeast(0)
            val bottom = (b.bottom + 2).toInt().coerceAtMost(under.height - 1)
            assertTrue("x$fontScale: the star ($sx,$sy) is not inside the box $left..$right x $top..$bottom", sx in left..right && sy in top..bottom)
            var worst = Float.MAX_VALUE
            for (y in top..bottom) for (x in left..right) {
                worst = minOf(worst, ComposeAudit.contrast(ink.compositeOver(androidx.compose.ui.graphics.Color(under.getPixel(x, y))), androidx.compose.ui.graphics.Color(under.getPixel(x, y))))
            }
            println("a11y $what over a star at x$fontScale: worst %.2f:1".format(worst))
            if (worst < ComposeAudit.AA_TEXT) problems += "x$fontScale: $what over the star at ($sx,$sy) is %.2f:1, needs ${ComposeAudit.AA_TEXT}".format(worst)
        }
        assertEquals("$what on the bare stage fails AA:\n" + problems.joinToString("\n"), emptyList<String>(), problems)
    }

    private val malibu = CarChoice("MyChevrolet", "AA:BB:CC:DD:EE:FF")

    /** The car setup's moments, the ones CarSetupScreenshotTest draws. */
    private val carSetupMoments = mapOf(
        "permissions" to SetupUi(SetupStep.PERMISSIONS),
        "no paired car" to SetupUi(SetupStep.PICK_CAR),
        "picking" to SetupUi(SetupStep.PICK_CAR, cars = listOf(malibu, CarChoice("Headphones", "11:22:33:44:55:66"))),
        "picked, not yet tried" to SetupUi(SetupStep.CONNECT, chosen = malibu),
        "joining" to SetupUi(SetupStep.CONNECT, chosen = malibu, running = true, canPair = true, gates = GateReport(Gate.ACCESS_POINT, passed = false, verdict = null, remedy = null)),
        "static ip" to SetupUi(
            SetupStep.CONNECT, chosen = malibu, paired = true, canPair = true,
            gates = GateReport(Gate.ADDRESS, passed = false, verdict = "IP_PROVISIONING", remedy = Remedy.SAVE_NETWORK_WITH_STATIC_IP),
            staticIp = staticIpFor("192.168.5.1"),
        ),
        "failure notice" to SetupUi(SetupStep.CONNECT, chosen = malibu, canPair = true, notice = SetupNotice("The car's network was not found. Keep the car on and showing Android Auto, then try again.", failed = true)),
        "on the car screen" to SetupUi(SetupStep.CONNECT, chosen = malibu, running = true, canPair = true, paired = true, gates = GateReport(Gate.SESSION, passed = true, verdict = null, remedy = null)),
    )

    private companion object {
        /**
         * ATF errors today's widgets carry, by the prefix of their description.
         * Only ever shrinks: the stale check above fails once a line is fixed.
         */
        val KNOWN_VIEW_FINDINGS = listOf<String>()
        /** The text sizes a person sets: the default and a large one (1.3 is Android's "large" step). */
        val FONT_SCALES = listOf(1f, 1.3f)
        /** 411 dp at xxhdpi (the screenshot tests' qualifier), in pixels. */
        const val PHONE_WIDTH_PX = 1233
    }
}
