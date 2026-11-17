package ai.jarvis.app.macrobenchmark

import android.content.Intent
import androidx.benchmark.macro.CompilationMode
import androidx.benchmark.macro.FrameTimingMetric
import androidx.benchmark.macro.StartupMode
import androidx.benchmark.macro.junit4.MacrobenchmarkRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.uiautomator.By
import androidx.test.uiautomator.Direction
import androidx.test.uiautomator.Until
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The 8-panel scene on a phone, timed frame by frame (GLASS_HUD §8.3 rule 6).
 *
 * The scene the web gate drove before the v2 shell (T-0611 deleted `hud-scene.ts`): eight
 * panels of eight kinds on the surface with the reactor at its brightest,
 * taken through open, scroll, and mode change. `FrameTimingMetric` reads the
 * platform's per-frame CPU and overrun times; `tools/frametime_gate.py` holds
 * the P95 against the budget for the device class the run reports (8.3 ms on a
 * 120 Hz Pixel, 16.7 ms otherwise) and the maximum against 50 ms.
 *
 * ## What is wired and what is not
 *
 * The scene is requested through the `HUD_SCENE` extra. Today's app draws the
 * surface from the adapter's events and has no fixture route, so the extra is
 * read by nothing yet: it is the contract the glass-android wave implements
 * (the phone's panel stack, the Glass HUD plan §9.6; the phone's home is the console now, T-0667) together with the `panel_surface`
 * and `mode_toggle` view ids this test looks for. Until then the benchmark
 * measures the voice screen as it opens, which is still a real number for the
 * reactor's cost. The test is deliberately complete rather than a stub so that
 * wiring the scene is the only change between now and a measured HUD.
 *
 * ## Why this is not run on the JVM
 *
 * Robolectric has no GPU and no choreographer worth timing; the frame numbers
 * would be the host's. This needs a device or an emulator, which no CI runner
 * has (the `android-device` tag in .gitlab/ci/quality-hud.yml is for the day
 * one does), so the V2_TASKS row stays BLOCKED with that reason.
 */
@RunWith(AndroidJUnit4::class)
class HudFrameTimingBenchmark {

    @get:Rule
    val benchmark = MacrobenchmarkRule()

    @Test
    fun eightPanelScene() = benchmark.measureRepeated(
        packageName = PACKAGE,
        metrics = listOf(FrameTimingMetric()),
        // Partial AOT with the app's baseline profile is what a user has after
        // Play or the in-app updater installs a release; `None` would time the
        // interpreter, `Full` a build nobody ships.
        compilationMode = CompilationMode.Partial(),
        startupMode = StartupMode.WARM,
        iterations = 5,
        setupBlock = {
            pressHome()
            val intent = Intent().apply {
                setClassName(PACKAGE, "$PACKAGE.MainActivity")
                putExtra(HUD_SCENE, "panels=8;reactor=attention")
            }
            startActivityAndWait(intent)
            // The surface, when the phone has one; the voice screen otherwise.
            device.wait(Until.hasObject(By.res(PACKAGE, "panel_surface")), SCENE_TIMEOUT_MS)
        },
    ) {
        val surface = device.findObject(By.res(PACKAGE, "panel_surface"))
        if (surface != null) {
            // Open/close through the stack, then the mode change (§9.6: the
            // phone's stack collapses to a carousel and back).
            surface.fling(Direction.DOWN)
            surface.fling(Direction.UP)
            surface.scroll(Direction.DOWN, 1.0f)
            surface.scroll(Direction.UP, 1.0f)
        }
        device.findObject(By.res(PACKAGE, "mode_toggle"))?.let { toggle ->
            toggle.click()
            device.waitForIdle()
            toggle.click()
        }
        device.waitForIdle()
    }

    private companion object {
        const val PACKAGE = "ai.jarvis.app"
        const val HUD_SCENE = "ai.jarvis.app.HUD_SCENE"
        const val SCENE_TIMEOUT_MS = 5_000L
    }
}
