package ai.aura.app.car.voice

import ai.aura.app.assist.hermes.HudState
import kotlinx.coroutines.async
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.channels.ReceiveChannel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.io.ByteArrayOutputStream

/** Where the car's current turn with Jarvis is. */
enum class CarTurnState { Idle, Thinking, Approval, Speaking }

/** Jarvis, as the car talks to it: a question in, the finished answer out, and its voice. */
interface CarJarvis {
    /** The finished reply, or null when Jarvis could not be reached or the turn failed. */
    suspend fun ask(text: String, ticket: String?, voice: Boolean = false, onApproval: suspend (HudState.Approval) -> Boolean): String?

    /** Stop the running turn on the server. */
    fun interrupt()

    /** Jarvis's voice for [text] as 16-bit mono PCM pieces; false when it could not be made. */
    fun speak(text: String, onStart: (rateHz: Int) -> Unit, onPiece: (ByteArray) -> Unit): Boolean
}

/** The car's speakers: the guidance channel, which the car ducks its music under. */
interface CarSpeaker {
    suspend fun play(pcm: ByteArray, rateHz: Int): Boolean

    /**
     * A voice still being made, played as it arrives; true when anything played. The
     * default waits for the whole line, for a speaker that cannot stream.
     */
    suspend fun stream(rateHz: Int, pieces: ReceiveChannel<ByteArray>): Boolean {
        val all = ByteArrayOutputStream()
        for (p in pieces) all.write(p)
        return all.size() > 0 && play(all.toByteArray(), rateHz)
    }

    /** A short line in the car's own voice, for when Jarvis cannot give one. */
    suspend fun say(text: String)
}

/**
 * One question from the car to Jarvis and its answer through the car's speakers
 * (ADR-0047). The grammar has already had the transcript (CarVoiceStream): "pause"
 * and "next track" never get here.
 *
 * An approval the tool asks for during the turn goes to [approve] -- a two-button
 * sheet on the car screen -- and is denied if nobody answers within
 * [approvalTimeoutMs]: production approvals are manual, and a question left open
 * while the car moves must fail closed.
 *
 * Does not keep a session of its own across a lost link: [cancel] ends the turn
 * where it stands, and nothing is re-spoken after a reconnect.
 */
class CarTurn(
    private val jarvis: CarJarvis,
    private val speaker: CarSpeaker,
    private val approve: suspend (HudState.Approval) -> Boolean,
    private val scope: CoroutineScope,
    private val approvalTimeoutMs: Long = 30_000L,
    /** Where Jarvis's voice is fetched: a blocking HTTP stream. A test hands its own. */
    private val io: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.IO,
    /** Each state as it is entered, with the answer when speaking starts (the Jarvis tab's reactor). */
    private val onUpdate: (CarTurnState, String?) -> Unit = { s, a -> CarVoice.post(CarVoiceEvent.Turn(s, a)) },
    /**
     * Open the car's microphone again: Jarvis's reply asked something (the owner,
     * 2026-09-25: "cant ask follow up questions"). Called after the reply has played.
     */
    private val followUp: () -> Unit = {},
    /** Milliseconds that only go forward; a test hands its own. */
    private val clock: () -> Long = { System.nanoTime() / 1_000_000 },
    /**
     * Her voice on the phone for one sentence, as (rate, 16-bit mono PCM); null when it is not
     * to be used or could not be made. A reply voiced at the house reached the car choppy and
     * cut off over mobile data (the owner, 2026-11-12); this needs no link at all. Null here
     * (the default) is the house voice only.
     */
    private val own: (suspend (String) -> Pair<Int, ByteArray>?)? = null,
) {
    private val mutable = MutableStateFlow(CarTurnState.Idle)
    val state: StateFlow<CarTurnState> = mutable.asStateFlow()

    private var job: Job? = null

    /** The last reply spoken, and when it stopped: what [repeat] says again. */
    private var said: String? = null
    private var saidAt = 0L

    /**
     * Ask [text]; a turn already running is ended first. A blank [text] is not a
     * question -- the phone-microphone path hands over "" when Jarvis could not hear
     * it -- and is dropped here, where every caller routes.
     */
    fun ask(text: String, ticket: String?, voice: Boolean = false) {
        if (text.isBlank()) return
        cancel()
        job = scope.launch {
            try {
                enter(CarTurnState.Thinking)
                val reply = jarvis.ask(text, ticket, voice) { approval ->
                    enter(CarTurnState.Approval)
                    val answer = withTimeoutOrNull(approvalTimeoutMs) { approve(approval) } ?: false
                    enter(CarTurnState.Thinking)
                    answer
                }
                if (reply == null) {
                    speaker.say(OFFLINE)
                    return@launch
                }
                if (reply.isBlank() || reply.trim() == QUIET) return@launch
                voice(reply)
                if (asks(reply)) {
                    enter(CarTurnState.Idle)
                    followUp()
                }
            } finally {
                enter(CarTurnState.Idle)
            }
        }
    }

    /** Says [reply] in the car, and remembers it for [repeat] whether or not it was cut off. */
    private suspend fun voice(reply: String) {
        said = reply
        try {
            enter(CarTurnState.Speaking, reply)
            val rest = own?.let { phone(reply, it) } ?: reply
            if (rest.isNotBlank()) house(rest)
        } finally {
            saidAt = clock()
        }
    }

    /**
     * Says [reply] in the phone's voice, a sentence at a time so the first is heard while the
     * next is made.
     *
     * @return what was not said, for the house: all of it when the first sentence could not be
     *   made or nothing played, the rest when a later one failed, "" when it was all said.
     */
    private suspend fun phone(reply: String, render: suspend (String) -> Pair<Int, ByteArray>?): String = coroutineScope {
        val parts = sentences(reply)
        val first = parts.firstOrNull()?.let { timed(it, FIRST_SENTENCE_MILLIS, render) } ?: return@coroutineScope reply
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        pieces.trySend(first.second)
        val left = async {
            try {
                var before = first.second
                for (i in 1 until parts.size) {
                    // A sentence is due when the one before it has been heard: a phone that got
                    // slow (hot, projecting) would leave the car silent and ducked mid-reply.
                    val due = before.size / 2 * 1000L / first.first + LATE_SENTENCE_MILLIS
                    val next = timed(parts[i], due, render)
                    // One stream has one rate: a sentence at another is the house's too.
                    if (next == null || next.first != first.first) return@async parts.drop(i).joinToString(" ")
                    pieces.trySend(next.second)
                    before = next.second
                }
                ""
            } finally {
                pieces.close()
            }
        }
        if (speaker.stream(first.first, pieces)) left.await() else {
            pieces.cancel()
            left.cancel()
            reply
        }
    }

    /**
     * [render] of [text], or null when it is not made within [millis]. The render runs in the
     * turn's scope, not the caller's, because it blocks and cannot be stopped: a late one is
     * left to finish and its sound dropped, and the next sentence the phone makes waits for it.
     */
    private suspend fun timed(text: String, millis: Long, render: suspend (String) -> Pair<Int, ByteArray>?): Pair<Int, ByteArray>? {
        val made = scope.async(io) { render(text) }
        return withTimeoutOrNull(millis) { made.await() }.also { if (it == null) made.cancel() }
    }

    /** Says [reply] in the voice made at the house, or the car's own when that cannot be had. */
    private suspend fun house(reply: String) = coroutineScope {
        // Played while it is made: the first sentence reaches the car while the
        // rest is synthesised, instead of the whole line waiting for its last sample.
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        val rate = CompletableDeferred<Int>()
        val made = async(io) {
            try {
                jarvis.speak(reply, onStart = { rate.complete(it) }, onPiece = { pieces.trySend(it) })
            } finally {
                pieces.close()
                rate.complete(DEFAULT_RATE)
            }
        }
        val played = speaker.stream(rate.await(), pieces)
        if (!made.await() || !played) {
            pieces.cancel()
            if (!played) speaker.say(reply)
        }
    }

    /**
     * Say the last reply again: the wheel's previous key, for [REPEAT_WINDOW_MILLIS] after she
     * stopped (the owner, 2026-11-11). False, and nothing happens, when she is mid-turn, has
     * said nothing, or stopped longer ago than that: the key is then the music's.
     *
     * Does not ask the model again and does not reopen the microphone after a question.
     */
    fun repeat(): Boolean {
        val reply = said ?: return false
        if (mutable.value != CarTurnState.Idle || clock() - saidAt >= REPEAT_WINDOW_MILLIS) return false
        cancel()
        job = scope.launch {
            try {
                voice(reply)
            } finally {
                enter(CarTurnState.Idle)
            }
        }
        return true
    }

    private fun enter(state: CarTurnState, answer: String? = null) {
        if (mutable.value == state && answer == null) return
        mutable.value = state
        onUpdate(state, answer)
    }

    /** End the turn where it stands: nothing more is said, and the server is told to stop. */
    fun cancel() {
        val running = job ?: return
        job = null
        if (running.isActive) {
            running.cancel()
            runCatching { jarvis.interrupt() }
        }
        enter(CarTurnState.Idle)
    }

    companion object {
        /** Said aloud, so the spoken name ("Aura is offline"); a clip per name and voice. */
        internal val OFFLINE: String get() = "${ai.aura.app.ui.theme.ActiveTheme.spokenName} is offline"
        private const val DEFAULT_RATE = 24_000

        /** How long after a reply the previous key repeats it rather than restarting the song. */
        const val REPEAT_WINDOW_MILLIS = 20_000L

        /**
         * What the phone tells Jarvis when a drive starts (hermes/skills/drive-routine).
         * Worded as an event, not a question, so a Jarvis with nothing to do answers [QUIET].
         * It carries the whole procedure: the turn used to read the skill first, and that
         * look-up was 2-20 s of every drive start (state.db, 2026-09-28..10-07). Saved
         * routines are not run here: the phone fires each `car_connected` one itself.
         *
         * Who has messaged comes from the notifications, which the phone answers without a
         * prompt; texts are confirm-tier and would ask at every drive start. The radio show
         * is offered, never started, and only when there is a long drive to fill (the owner,
         * 2026-10-08: not proactive with the planned features).
         */
        const val DRIVE_START =
            "[event: drive started] Sir has just connected to the car; nobody asked anything. " +
                "Without reading a skill: phone_action read_calendar {days_ahead: 1, limit: 3} and " +
                "phone_action read_notifications {limit: 5}. If an event starts within two hours and has " +
                "a location, place_search that location and car_navigate to it. Then say at most two short " +
                "sentences: the route if you set one, and who has messaged him from a messaging app (names " +
                "only, never what they wrote). When the route is over twenty minutes, end by offering a radio " +
                "show. No route and no messages: reply with exactly (quiet)."

        /**
         * What the phone tells Jarvis when the drive is over (hermes/skills/drive-routine;
         * the owner, 2026-10-08: a post-drive debrief that asks what it got wrong). Sent once
         * per real drive, [DriveEnd], to the phone and not the car, whose session is gone.
         * `later_release` is how "parked" becomes an event instead of a thirty-minute guess.
         */
        const val DRIVE_END =
            "[event: drive ended] Sir's drive is over; nobody asked anything. Without reading a skill: " +
                "later_release {when: parked}, then loops_list. Then say at most two short sentences: what " +
                "you have started on from his later queue, if any was released, and what is still open from " +
                "this drive, by name. If you mis-heard or failed him on this drive, end by asking exactly: " +
                "Anything I got wrong on that drive? Nothing released, nothing open and nothing wrong: " +
                "reply with exactly (quiet)."

        /** The first sentence may load the model; past this the house says the reply. */
        const val FIRST_SENTENCE_MILLIS = 4_000L

        /** How long after the sentence before it was heard a sentence may still arrive. */
        const val LATE_SENTENCE_MILLIS = 1_500L

        // A full stop after one of these is not a sentence's end ("St. Mary's", "Dr. Lee").
        private val ABBREVIATIONS = setOf("St.", "Dr.", "Mr.", "Mrs.", "Ms.", "Mt.", "Jr.", "Sr.", "Ave.", "Rd.", "Blvd.", "vs.", "No.")

        /**
         * [reply] cut where a sentence ends: "3.5", an abbreviation, an initial, and a stop
         * before a lower-case word ("9 p.m. tomorrow") are not cut. A cut it misses only makes
         * a longer piece, heard a little later: a reply with no stops at all is one piece.
         */
        fun sentences(reply: String): List<String> {
            val out = mutableListOf<String>()
            val now = StringBuilder()
            val words = reply.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
            for ((i, word) in words.withIndex()) {
                if (now.isNotEmpty()) now.append(' ')
                now.append(word)
                val initial = word.length == 2 && word[0].isUpperCase()
                val goesOn = words.getOrNull(i + 1)?.first()?.isLowerCase() == true
                if (word.last() in ".!?" && word !in ABBREVIATIONS && !initial && !goesOn) {
                    out += now.toString()
                    now.clear()
                }
            }
            if (now.isNotEmpty()) out += now.toString()
            return out
        }

        /**
         * Whether a car reply is said in the phone's voice (`tests/contracts/car-state.json`,
         * `voice.uses`): never when the house's is asked for or hers is not on the phone; "phone"
         * insists on one measured slower than it speaks, anything else needs it to keep up.
         */
        fun usesPhone(mode: String, ready: Boolean, realtime: Boolean): Boolean = when (mode) {
            "house" -> false
            "phone" -> ready
            else -> ready && realtime
        }

        /** A reply that ends on a question waits for the answer: its last sentence asks. */
        fun asks(reply: String): Boolean = reply.trimEnd().trimEnd('"', '\u201D', ')').endsWith("?")

        /** Jarvis's answer when there is nothing to say; the car says nothing either. */
        const val QUIET = "(quiet)"
    }
}
