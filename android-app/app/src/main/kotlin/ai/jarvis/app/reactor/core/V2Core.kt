package ai.jarvis.app.reactor.core

import ai.jarvis.app.reactor.ReactorHaptics
import ai.jarvis.app.reactor.ReactorState
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.theme.ActiveTheme
import android.os.PowerManager
import android.os.Vibrator
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.currentStateAsState

/**
 * The console's core on the phone (`$lib/ds/core/Core.svelte`, T-0712): [CoreEngine] stepped
 * on the frame clock and drawn by [CoreCanvas]. A reactor state lands on a core state through
 * [coreStateFor], the console's own table.
 *
 * The clock runs only while the lifecycle is RESUMED (a popup behind the lock screen or a
 * stopped activity draws nothing). Under reduced motion it stands still and a change is one
 * frame; on battery saver or under reduced motion it draws [LIGHT] points instead of [FULL],
 * as the console does. [testTimeMs] draws one frozen frame, the goldens' picture.
 *
 * Does not cover the console's music beat (the phone has no beat level) or the speech
 * envelope: while speaking the engine's own pulse stands in, as on the console without one.
 *
 * @param level the mic level, 0..1, while listening.
 * @param haptics off for a second core on the same screen, a preview or a golden.
 * @param label what a screen reader says; the host adds the goal ([Derived.spoken]).
 */
@Composable
fun V2Core(
    state: ReactorState,
    modifier: Modifier = Modifier,
    level: Float = 0f,
    haptics: Boolean = true,
    testTimeMs: Double? = null,
    label: String = state.word,
) {
    val context = LocalContext.current
    val reduced = remember { JarvisUi.reducedMotion(context) }
    val saver = remember { context.getSystemService(PowerManager::class.java)?.isPowerSaveMode == true }
    val shell = remember(reduced, saver) { CoreEngine.fibonacci(if (reduced || saver) LIGHT else FULL) }
    val target = coreStateFor(state.key)
    val core = remember(reduced) { CoreEngine.createCore(target, 0.0, still = reduced) }
    val renderer = remember { CoreCanvas() }
    val lifecycle by LocalLifecycleOwner.current.lifecycle.currentStateAsState()

    var now by remember { mutableDoubleStateOf(0.0) }
    val running = testTimeMs == null && !reduced && lifecycle.isAtLeast(Lifecycle.State.RESUMED)
    LaunchedEffect(running) {
        if (!running) return@LaunchedEffect
        // Resume where the model's clock stopped: a minute away is one capped step, not a leap.
        val base = core.t
        var start = -1L
        while (true) {
            withFrameNanos { nanos ->
                if (start < 0) start = nanos
                now = base + (nanos - start) / 1_000_000.0
            }
        }
    }

    // The felt transitions, as the reactor buzzed them: arriving in a state is not entering it.
    val buzzer = remember(haptics) {
        if (haptics) ReactorHaptics.of(context.getSystemService(Vibrator::class.java)) else null
    }
    var previous by remember { mutableStateOf<ReactorState?>(null) }
    LaunchedEffect(state) {
        ReactorHaptics.onTransition(previous, state, buzzer)
        previous = state
    }

    // AURA's pack names the galaxy (T-0871); every other persona keeps the reactor below, untouched.
    if (ActiveTheme.theme.renderer == GALAXY) {
        GalaxyCore(state, target, modifier, level, reduced, running, now, testTimeMs, label)
        return
    }

    val frozen = testTimeMs?.let { remember(target, it) { CoreEngine.frozenAt(target, CoreState.IDLE, it, Inputs(level = level.toDouble())) } }
    Canvas(
        modifier
            // The console's canvas is its own layer: 'lighter' adds inside it, then the whole
            // core lies over the page. Offscreen does the same over whatever this sits on.
            .graphicsLayer(compositingStrategy = CompositingStrategy.Offscreen)
            .semantics { contentDescription = label },
    ) {
        val drawn = frozen ?: core.also {
            val t = if (running) now else it.t
            CoreEngine.setState(it, target, t)
            CoreEngine.step(it, t, Inputs(level = level.toDouble()))
        }
        drawIntoCanvas { renderer.draw(it.nativeCanvas, drawn, shell, size.width, size.height, density) }
    }
}

/**
 * The galaxy in the core's place: [GalaxyModel] at the phone's budget, stepped by the frame
 * clock's delta (capped in the model, so a minute away is one step), drawn by [GalaxyCanvas].
 * Its goldens ([testTimeMs]) draw without the AGSL glow, which Robolectric cannot build.
 */
@Composable
private fun GalaxyCore(
    state: ReactorState,
    target: CoreState,
    modifier: Modifier,
    level: Float,
    reduced: Boolean,
    running: Boolean,
    now: Double,
    testTimeMs: Double?,
    label: String,
) {
    // Keyed on reduced: a reduced-motion change mid-session makes a still galaxy; and on the worn
    // pack, so a change of persona builds that pack's galaxy (the reviews of T-0871).
    val pack = GalaxyModel.worn()
    val galaxy = remember(reduced, pack) { GalaxyModel.create(GalaxyQuality.PHONE, target, still = reduced, pack = pack) }
    val renderer = remember(testTimeMs == null) { GalaxyCanvas(useShader = testTimeMs == null) }
    // Plain memory, not state: a state written in the draw phase would redraw every frame forever.
    val last = remember { doubleArrayOf(Double.NaN) }
    val frozen = testTimeMs?.let {
        remember(target, it, pack) { GalaxyModel.at(target, CoreState.IDLE, it, GalaxyQuality.PHONE, Inputs(level = level.toDouble()), pack) }
    }
    Canvas(
        modifier
            .graphicsLayer(compositingStrategy = CompositingStrategy.Offscreen)
            .semantics { contentDescription = label },
    ) {
        val drawn = frozen ?: galaxy.also {
            GalaxyModel.setState(it, target)
            // The clock restarts from 0 on each resume: a backwards now is a fresh start, not a step.
            val dt = if (running && last[0].isFinite() && now >= last[0]) now - last[0] else 0.0
            last[0] = if (running) now else Double.NaN
            GalaxyModel.step(it, dt, Inputs(level = level.toDouble()))
        }
        drawIntoCanvas { renderer.draw(it.nativeCanvas, drawn, size.width, size.height) }
    }
}

private const val GALAXY = "galaxy"

/** Points in the shell, and the fewer drawn on battery saver or under reduced motion (Core.svelte). */
private const val FULL = 1500
internal const val LIGHT = 600
