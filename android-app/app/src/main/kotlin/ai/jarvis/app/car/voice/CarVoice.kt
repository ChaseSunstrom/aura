package ai.jarvis.app.car.voice

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.ui.theme.ActiveTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** What the car screen's Jarvis tab shows of the car's voice: the reactor's phase and the words. */
data class CarVoiceView(
    val phase: Phase = Phase.IDLE,
    val caption: String = "",
    val heard: String = "",
    val answer: String = "",
    /** The microphone has closed and the words are not back yet: a listen's tail, not yet a turn. */
    val awaitingWords: Boolean = false,
)

/** What happened to the car's voice, from the stream (CarVoiceStream) and the turn (CarTurn). */
sealed interface CarVoiceEvent {
    data object Listening : CarVoiceEvent
    /** The microphone closed; the transcript is being waited for. */
    data object Hearing : CarVoiceEvent
    data class Heard(val text: String) : CarVoiceEvent
    data object Nothing : CarVoiceEvent
    data object Refused : CarVoiceEvent
    data object Offline : CarVoiceEvent
    /** The transcribe call went out and failed, the network held: said as that, not as offline (T-1079). */
    data object Failed : CarVoiceEvent
    /** The listening session is over, however it ended: a phase still on Listening goes idle. */
    data object Ended : CarVoiceEvent
    data class Turn(val state: CarTurnState, val answer: String?) : CarVoiceEvent
}

/**
 * The Jarvis tab's reactor follows the car's own turns (ADR-0047): pressed on the
 * steering wheel, it listens; heard, it thinks; answering, it speaks -- the same states
 * the phone's reactor walks through, from the car's microphone rather than the phone's.
 * What was said and answered stays on the tab until the next turn begins.
 */
fun reduce(view: CarVoiceView, event: CarVoiceEvent): CarVoiceView = when (event) {
    CarVoiceEvent.Listening -> CarVoiceView(phase = Phase.LISTENING, caption = "Listening")
    // Not Listening once the microphone is shut: the wait for the words was counted as
    // listening, so a slow transcript kept the music from the car for all of it (T-1111).
    CarVoiceEvent.Hearing ->
        if (view.phase == Phase.LISTENING) view.copy(phase = Phase.THINKING, caption = "", awaitingWords = true) else view
    is CarVoiceEvent.Heard -> view.copy(phase = Phase.THINKING, heard = event.text, caption = "", awaitingWords = false)
    CarVoiceEvent.Nothing -> view.copy(phase = Phase.IDLE, caption = "Heard nothing", awaitingWords = false)
    CarVoiceEvent.Refused ->
        view.copy(phase = Phase.IDLE, caption = "${ActiveTheme.name} did not recognise your voice", awaitingWords = false)
    CarVoiceEvent.Offline -> view.copy(phase = Phase.ERROR, caption = "${ActiveTheme.name} is offline", awaitingWords = false)
    CarVoiceEvent.Failed -> view.copy(phase = Phase.ERROR, caption = "${ActiveTheme.name} could not hear that", awaitingWords = false)
    CarVoiceEvent.Ended ->
        if (view.phase == Phase.LISTENING || view.awaitingWords) view.copy(phase = Phase.IDLE, caption = "", awaitingWords = false)
        else view
    is CarVoiceEvent.Turn -> when (event.state) {
        CarTurnState.Thinking -> view.copy(phase = Phase.THINKING, caption = "")
        CarTurnState.Approval -> view.copy(phase = Phase.THINKING, caption = "Waiting for you on the car screen")
        CarTurnState.Speaking -> view.copy(phase = Phase.SPEAKING, answer = event.answer ?: view.answer, caption = "")
        CarTurnState.Idle -> view.copy(phase = Phase.IDLE, caption = if (view.phase == Phase.ERROR) view.caption else "")
    }.copy(awaitingWords = false)
}

/** The process's one car voice, which the Jarvis tab collects. */
object CarVoice {
    private val mutable = MutableStateFlow(CarVoiceView())
    val view: StateFlow<CarVoiceView> = mutable.asStateFlow()

    /**
     * The car microphone's level while a turn listens, 0..1 of full scale, every 20 ms
     * chunk; the Jarvis tab's reactor swells with it (brief §1 car, AUDIT B9). Its own
     * flow, not a field of [view], so the tab's text is not redrawn fifty times a second.
     */
    private val loudness = MutableStateFlow(0f)
    val level: StateFlow<Float> = loudness.asStateFlow()

    fun level(value: Float) {
        loudness.value = value
    }

    fun post(event: CarVoiceEvent) {
        synchronized(this) { mutable.value = reduce(mutable.value, event) }
        // Anything after Listening means the microphone is closed: the swell goes with it.
        if (event !is CarVoiceEvent.Listening) loudness.value = 0f
    }

    /**
     * A turn on the phone itself (the wake word, the assist card): the car's
     * reactor wears it too (the owner, 2026-09-25: "if its thinking on the phone
     * it isn't showing it on the head"). The phone's own phases, as they come.
     */
    fun mirror(phase: Phase, caption: String) {
        synchronized(this) { mutable.value = mutable.value.copy(phase = phase, caption = caption) }
        // A mirrored phase that is never followed by another is a phone conversation that
        // ended without saying so; the car sat on "Listening" for good (the owner,
        // 2026-09-25: "stuck in listening on my car, even though it wasn't on my phone").
        runCatching {
            main.removeCallbacks(expire)
            if (phase != Phase.IDLE) main.postDelayed(expire, MIRROR_EXPIRES_MS)
        }
    }

    /**
     * A line for the driver that belongs to no turn ("Reconnect for this to take effect",
     * "The car would not open its microphone"): the caption says it for [NOTE_MS] and the
     * phase stays where it is. An event after it replaces it, as any caption is replaced.
     * Does not cover: a screen that draws no caption (a car app's own pane) shows nothing.
     */
    fun note(text: String) {
        synchronized(this) { mutable.value = mutable.value.copy(caption = text) }
        runCatching {
            main.postDelayed({
                synchronized(this) {
                    if (mutable.value.caption == text) mutable.value = mutable.value.copy(caption = "")
                }
            }, NOTE_MS)
        }
    }

    /** Long enough to read a short phrase at a glance, short enough not to linger. */
    private const val NOTE_MS = 2_600L

    /** Lazy: a JVM test that never mirrors never needs a looper. */
    private val main by lazy { android.os.Handler(android.os.Looper.getMainLooper()) }
    private val expire = Runnable { mirror(Phase.IDLE, "") }
    private const val MIRROR_EXPIRES_MS = 45_000L

    /** Back to a quiet tab: tests share the process, and one's turn would show in the next's golden. */
    internal fun reset() {
        mutable.value = CarVoiceView()
        loudness.value = 0f
    }
}
