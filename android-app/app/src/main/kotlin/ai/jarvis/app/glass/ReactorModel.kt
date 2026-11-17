package ai.jarvis.app.glass

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.reactor.Derived
import ai.jarvis.app.reactor.ReactorInputs
import ai.jarvis.app.reactor.derive
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlin.math.max
import kotlin.math.min

/**
 * What a surface knows about its reactor, as Compose state: the inputs the
 * conversation publishes ([ai.jarvis.app.assist.JarvisConversation.Ui.onReactor]),
 * the microphone level, the cold start's progress, and the caption. One of
 * these per surface, written from the main thread by the surface's Ui
 * callbacks and read by [ReactorView].
 *
 * The state to draw is [derived], never chosen here: `ReactorState.derive`
 * is the one precedence rule (GLASS_HUD §10) and a surface that picked its
 * own state would be a surface that could disagree with the console about
 * what an approval looks like.
 */
class ReactorModel {
    var inputs: ReactorInputs by mutableStateOf(ReactorInputs())

    /** The microphone level, 0..1, after [AMPLITUDE_GAIN]. */
    var level: Float by mutableFloatStateOf(0f)

    /** The cold start's progress when the adapter gives one; null otherwise. */
    var warmingProgress: Float? by mutableStateOf(null)

    /** The caption the conversation sent with its last phase (`onMode`'s label). */
    var caption: String by mutableStateOf("")

    val derived: Derived get() = derive(inputs)

    /**
     * The microphone level as the reactor wants it. [AMPLITUDE_GAIN] is
     * applied here rather than by the caller so every surface gets it and
     * the VAD keeps the raw value it needs: a smoothed RMS spends
     * conversational speech between 0.02 and 0.10, which through the core's
     * level term is a change under two percent — wired end to end, and
     * invisible. The web applies the same gain before its own reactor
     * (`Math.min(micLevel * 4, 1)`).
     */
    fun setAmplitude(raw: Float) {
        level = min(1f, max(0f, raw * AMPLITUDE_GAIN))
    }

    /** A surface with only a phase to offer: the enrolment screen, the companion's card. */
    fun setPhase(phase: Phase, linkUp: Boolean = true, warming: Boolean = false) {
        inputs = ReactorFeed.inputsFrom(phase, linkUp = linkUp, warming = warming)
    }

    companion object {
        const val AMPLITUDE_GAIN = 4f
    }
}
