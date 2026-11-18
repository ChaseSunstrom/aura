package ai.aura.app.ui.v2

import ai.aura.app.ui.theme.JarvisThemes
import ai.aura.app.ui.theme.JarvisTokens
import ai.aura.app.ui.theme.PersonaAccent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The v2 kit: what each part looks like (one golden of every part on the stage, in the default
 * pack and in JARVIS's, which a change to the stage under a starfield must not move, T-0949),
 * and the one part with behaviour a person relies on, hold-to-confirm.
 *
 * Does not cover a touch through another window: that is the host's
 * `filterTouchesWhenObscured`, pinned by ApprovalActivity's own test.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class V2KitTest {

    @get:Rule
    val compose = createComposeRule()

    @After
    fun back() = wear(JarvisThemes.DEFAULT_ID)

    // In a snapshot: a bare write reaches Compose's global observer and later Compose tests
    // never get idle (IslandRestGoldenTest).
    private fun wear(pack: String) =
        Snapshot.withMutableSnapshot { PersonaAccent.set(androidx.test.core.app.ApplicationProvider.getApplicationContext(), pack) }

    @Test
    fun `every part of the kit on the stage`() = kit(JarvisThemes.DEFAULT_ID, "v2-kit")

    @Test
    fun `every part of the kit in the JARVIS pack`() = kit(JarvisThemes.JARVIS.id, "v2-kit-jarvis")

    private fun kit(pack: String, name: String) {
        wear(pack)
        compose.setContent {
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
                    Projection(Modifier.fillMaxWidth(), tone = Tone.WARN, bracket = JarvisTokens.Size.GUTTER.dp, stroke = JarvisTokens.Space.MICRO.dp) {
                        Label("Needs your OK", tone = Tone.WARN)
                        Headline("Text Sam “on my way”?")
                        HoldButton("Hold to send", {}, Modifier.fillMaxWidth())
                        Row(horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp)) {
                            ActionButton("Change", {})
                            ActionButton("Don't send", {})
                            ActionButton("Off", {}, enabled = false)
                        }
                        Hairline()
                        RuleChip("Ask every time", selected = true, onClick = {})
                        RuleChip("Allow when I'm home", selected = false, onClick = {})
                    }
                    Projection(Modifier.fillMaxWidth(), pinned = true) { Body("A pinned projection has its edge.") }
                    ScreenState(Screen.OFFLINE, "The house is not answering.", action = "Retry", onAction = {})
                    ScreenState(Screen.ERROR, "Pairing failed: the code ran out.")
                }
            }
        }
        compose.onRoot().captureRoboImage("src/test/screenshots/$name.png")
    }

    @Test
    fun `a hold confirms only once it has lasted the whole hold`() {
        var confirmed = 0
        compose.mainClock.autoAdvance = false
        compose.setContent { Stage { HoldButton("Hold to send", { confirmed++ }) } }

        compose.onNodeWithText("HOLD TO SEND").performTouchInput { down(center) }
        compose.mainClock.advanceTimeBy(V2.HOLD_MS / 2L)
        compose.onNodeWithText("HOLD TO SEND").performTouchInput { up() }
        compose.mainClock.advanceTimeBy(V2.HOLD_MS.toLong())
        assertEquals("a press let go halfway confirmed", 0, confirmed)

        compose.onNodeWithText("HOLD TO SEND").performTouchInput { down(center) }
        compose.mainClock.advanceTimeBy(V2.HOLD_MS + 200L)
        compose.onNodeWithText("HOLD TO SEND").performTouchInput { up() }
        compose.mainClock.advanceTimeBy(200L)
        assertEquals("a full hold did not confirm exactly once", 1, confirmed)
    }

    @Test
    fun `sliding off the button cancels the hold`() {
        var confirmed = 0
        compose.mainClock.autoAdvance = false
        compose.setContent { Stage { HoldButton("Hold to send", { confirmed++ }) } }

        compose.onNodeWithText("HOLD TO SEND").performTouchInput {
            down(center)
            moveTo(center.copy(y = center.y + height * 4))
        }
        compose.mainClock.advanceTimeBy(V2.HOLD_MS + 200L)
        assertEquals("a press dragged off the button still confirmed", 0, confirmed)
    }

    @Test
    fun `turning the gate off mid-hold cancels it`() {
        var confirmed = 0
        var enabled by mutableStateOf(true)
        compose.mainClock.autoAdvance = false
        compose.setContent { Stage { HoldButton("Hold to send", { confirmed++ }, enabled = enabled) } }

        compose.onNodeWithText("HOLD TO SEND").performTouchInput { down(center) }
        compose.mainClock.advanceTimeBy(V2.HOLD_MS / 2L)
        enabled = false
        compose.mainClock.advanceTimeBy(V2.HOLD_MS.toLong())
        assertEquals("the hold ran on after its gate closed", 0, confirmed)
    }

    // Two tests, not one that flips `enabled`: a state write from the test thread is a global
    // snapshot change, and a composition an earlier class leaked (CarShellScreenshotTest describes
    // how one outlives teardown) records it and never applies it, so the next waitForIdle
    // timed out after 60 s in the full suite while this class alone passed.
    @Test
    fun `TalkBack offers no long click while disabled`() {
        compose.setContent { Stage { HoldButton("Hold to send", {}, enabled = false) } }
        compose.onAllNodes(SemanticsMatcher.keyIsDefined(SemanticsActions.OnLongClick)).assertCountEquals(0)
    }

    @Test
    fun `TalkBack confirms through the long click`() {
        var confirmed = 0
        compose.setContent { Stage { HoldButton("Hold to send", { confirmed++ }) } }
        compose.onNode(SemanticsMatcher.keyIsDefined(SemanticsActions.OnLongClick)).performSemanticsAction(SemanticsActions.OnLongClick)
        assertEquals(1, confirmed)
    }
}
