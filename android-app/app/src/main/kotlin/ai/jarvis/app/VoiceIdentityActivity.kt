package ai.jarvis.app

import ai.jarvis.app.assist.MicStreamer
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.config.LEVELS
import ai.jarvis.app.config.VoiceIdentityClient
import ai.jarvis.app.assist.Phase
import ai.jarvis.app.glass.ReactorView
import ai.jarvis.app.ui.theme.ActiveTheme
import android.Manifest
import androidx.activity.ComponentActivity
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.io.ByteArrayOutputStream
import kotlin.concurrent.thread
import ai.jarvis.app.ui.theme.JarvisTokens
import ai.jarvis.app.ui.v2.ActionButton
import ai.jarvis.app.ui.v2.Body
import ai.jarvis.app.ui.v2.Field
import ai.jarvis.app.ui.v2.Hairline
import ai.jarvis.app.ui.v2.Headline
import ai.jarvis.app.ui.v2.HoldButton
import ai.jarvis.app.ui.v2.Label
import ai.jarvis.app.ui.v2.Projection
import ai.jarvis.app.ui.v2.Stage
import ai.jarvis.app.ui.v2.Tone
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView

/**
 * Teaching Jarvis your voice, and checking it still knows it.
 *
 * ## Why this screen has phrases on it
 *
 * Not decoration. The profile's denominator is how much the owner varies
 * between utterances, so a set of five calm, identical-sounding phrases teaches
 * it that the owner *never* varies — and then the first question you ask, or
 * the first order you snap, reads as a stranger. The server measured this:
 * narrow enrolment overlaps the nearest impostor, and enrolment that moves
 * pitch and length separates them cleanly.
 *
 * So the phrases come from the server (`prompts` in the status payload) rather
 * than being typed in here, both surfaces read the same list, and each one is
 * chosen to move something — a question, an order, a count, a long sentence.
 *
 * ## Why the numbers are on screen
 *
 * Because the threshold is not knowable in advance and the owner is the one who
 * has to set it. After enrolment this shows what the owner's own worst sample
 * scored and what threshold that implies, and TEST MY VOICE scores a fresh
 * utterance against it. A biometric gate whose threshold was guessed is a gate
 * that locks you out on the first cold morning, and the only defence is being
 * able to see the numbers before you turn it on.
 *
 * ## Who (M71)
 *
 * A sample is enrolled under a NAME. The box above the phrases is who is
 * reading them; empty means the server's default person, so a house with one
 * voice types nothing. A new name is a new person, up to the server's
 * `max_people`; the phrase list follows THAT person's sample count, so two
 * people enrolling on one phone are not asked each other's phrases. Everyone
 * enrolled is listed, each with their own FORGET, and TEST MY VOICE says who
 * it heard rather than only whether.
 *
 * Nothing here enables enforcement. That is a line in `configuration.yaml` on
 * the server, deliberately: turning on the thing that can refuse you should not
 * be a switch you can hit by accident on a phone.
 */
class VoiceIdentityActivity : ComponentActivity() {

    private lateinit var config: JarvisConfig

    /**
     * Nullable, not `lateinit`.
     *
     * It was `lateinit`, assigned after the unconfigured early return — and
     * that return disabled the record button only, because the other two are
     * built by `JarvisUi.button`, which does not touch `isEnabled`. So on a
     * phone that had never been paired, FORGET MY VOICE was live, read an
     * uninitialised property inside a worker thread, and killed the process.
     *
     * Both halves are fixed: every button is disabled below, and this is
     * nullable so a button added to this screen later cannot bring the crash
     * back — [offMainThread] short-circuits instead.
     */
    private var client: VoiceIdentityClient? = null

    private val main = Handler(Looper.getMainLooper())

    /** The glass reactor, showing the enrolment's phase and the microphone's level. */
    private lateinit var orb: ReactorView

    /** The phrase to say now. */
    private var prompt by mutableStateOf("")

    /** What to do now, in words. */
    private var statusLine by mutableStateOf("")

    /** The numbers behind it, and the last sample's verdict first. */
    private var detail by mutableStateOf("")

    /** What each control may do now; set only by [syncButtons]. */
    private var recordText by mutableStateOf(RECORD_START)
    private var recordEnabled by mutableStateOf(false)
    private var testEnabled by mutableStateOf(false)
    private var forgetText by mutableStateOf(FORGET_ONE)
    private var forgetEnabled by mutableStateOf(false)
    private var redoEnabled by mutableStateOf(false)
    private var nameEnabled by mutableStateOf(false)

    /** One row per phrase: what it says and whether it has been given. */
    private var steps by mutableStateOf<List<Step>>(emptyList())

    /** A phrase as the list shows it, and as TalkBack says it. */
    private data class Step(val text: String, val said: String, val tone: Tone?)

    /** Who is reading the phrases (M71). Empty is the server's default person. */
    private var nameField by mutableStateOf("")

    private var status by mutableStateOf<VoiceIdentityClient.Status?>(null)

    /**
     * The name the next sample goes to: what was typed, trimmed and collapsed
     * the way the server's `normalise_label` does it, else the server's default.
     */
    private val label: String
        get() {
            val typed = nameField.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
            return typed.ifEmpty { status?.defaultLabel ?: "owner" }
        }

    /**
     * Which phrase to read next.
     *
     * **Derived from the server's sample count, not counted here.** It was a
     * plain field starting at 0 and reset only by FORGET MY VOICE, so rotating
     * the phone, taking a call, or coming back to finish enrolment tomorrow
     * restarted the list from the top — while the server's count kept climbing.
     * The user re-read phrases they had already given, which is the one thing
     * this screen must not ask for: the profile's whole value is that the
     * samples differ from each other.
     *
     * Persisting a local counter would have been the obvious fix and the wrong
     * one. Two devices can enrol into one profile, `FORGET MY VOICE` is not the
     * only way samples go away, and a local number is a second opinion about
     * something the server already knows exactly. `samples` IS the index: with
     * three stored, the next phrase to read is the fourth.
     *
     * [redo] is the one thing that can move it backwards, and only by one.
     *
     * Since M71 it is the NAMED person's count. Two people enrolling on one
     * phone must not be asked each other's phrases, and a name nobody has
     * enrolled under yet starts at the top.
     */
    private val promptIndex: Int
        get() = ((status?.personNamed(label)?.samples ?: 0) - redo).coerceAtLeast(0)

    /**
     * How far REDO has stepped the phrase list back.
     *
     * Not a sample count and never negative: it exists so "say that one again"
     * re-offers the phrase just read. Cleared whenever a sample is accepted,
     * because the list has moved on.
     */
    private var redo = 0

    /** What the server said about the last sample. Cleared when a new one starts. */
    private var lastNote: String? = null

    private var mic: MicStreamer? = null
    private var capture: ByteArrayOutputStream? = null
    private var busy = false

    /** What the current capture is for. */
    private enum class Mode { ENROL, TEST }

    private var mode = Mode.ENROL

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        config = JarvisConfig(this)
        orb = ReactorView(this).apply { setPhase(Phase.IDLE) }
        setContent { Screen() }

        if (!config.isConfigured) {
            statusLine = "Pair this phone with ${ActiveTheme.name} first."
            // All three, via the shared helper. Disabling the record button
            // alone left TEST and FORGET live on a screen that has no server
            // to talk to.
            syncButtons()
            return
        }
        client = VoiceIdentityClient(config.serverUrl, config.token)
        syncButtons()
        refresh()
    }

    override fun onDestroy() {
        stopCapture()
        super.onDestroy()
    }

    // --- UI -----------------------------------------------------------------

    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun Screen() {
        Stage {
            Column(
                Modifier
                    .fillMaxSize()
                    .safeDrawingPadding()
                    .verticalScroll(rememberScrollState())
                    .padding(JarvisTokens.Space.SCREEN.dp),
                verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
            ) {
                Headline("Your voice")
                Body("Teach ${ActiveTheme.name} what you sound like, so it answers you and not the television.", dim = true)
                Body(
                    "${ActiveTheme.name} can be told to answer only you. Say each line below in the " +
                        "voice you would actually use — the question as a question, the " +
                        "order as an order. That variation is what the check is built from; " +
                        "five identical calm phrases teach it that you never sound different, " +
                        "and then it stops recognising you when you do.",
                    dim = true,
                )

                // The glass reactor: no chrome, no scrim, no entrance to forget.
                //
                // The old View here shipped invisible — it drew everything through an
                // entrance progress that started at 0 and only three methods moved,
                // and this screen called none of them (reported as *"in the enrolment
                // on the phone, there's no animation or ANY indicator if Jarvis is
                // listening"*). `glass.Reactor` draws from its first frame and has no
                // chrome to turn off, so neither of the two mistakes this slot has
                // had — a wordmark over the orb, a 160 dp hole — can be made again.
                AndroidView({ orb }, Modifier.fillMaxWidth().height(JarvisTokens.Size.SHEET_MIN.dp))

                // WHO, before WHAT. Typing a name changes which phrase is next, so the
                // box sits above the phrase it decides.
                Field("Who is this", nameField, ::onName, placeholder = "owner", enabled = nameEnabled)
                Body(
                    "Leave it empty for the first voice. A new name is a new person, and " +
                        "${ActiveTheme.name} is told who is speaking.",
                    dim = true,
                )

                // No phrase until the server has answered: an empty projection
                // under SAY THIS read as a broken box on an unpaired phone.
                if (prompt.isNotEmpty()) {
                    Label("Say this")
                    Projection(Modifier.fillMaxWidth()) { Body(prompt) }
                }

                // THE STEP LIST, which this screen did not have.
                //
                // Progress was one line of text — "3 of 20 samples · gate is observe" —
                // so the only way to know which phrases had been given was to remember.
                // With the phrase list restarting from the top on every rotation (see
                // [promptIndex]) that was not a small gap: the screen and the server
                // disagreed about where enrolment had got to, and nothing on screen
                // showed the disagreement. Re-bound in [renderSteps], because the
                // phrases arrive from the server and can change under a running screen.
                for (step in steps) {
                    Body(
                        step.text,
                        Modifier.semantics { contentDescription = step.said },
                        dim = step.tone == null,
                        tone = step.tone,
                    )
                }

                // TAP, not hold.
                //
                // It was push-to-talk, on the reasoning that a VAD which clips the last
                // word poisons a profile. That reasoning is about *automatic* ending and
                // it survives intact — nothing here listens for silence. What does not
                // survive is making the user hold a button through a whole spoken
                // sentence while watching an orb that was not drawing: hold-to-talk
                // gives no feedback of its own, so the one broken indicator was the
                // entire interface. Tap to start, tap to stop, and the countdown below
                // is a backstop rather than the normal way a capture ends.
                ActionButton(recordText, onClick = ::toggleRecording, enabled = recordEnabled, filled = true)

                // What to do now, in words; then the numbers behind it, quieter.
                if (statusLine.isNotEmpty()) Body(statusLine)
                if (detail.isNotEmpty()) Body(detail, dim = true)

                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                    verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                ) {
                    ActionButton("Say that one again", onClick = ::redo, enabled = redoEnabled)
                    ActionButton("Test my voice", onClick = ::startTest, enabled = testEnabled)
                }

                // Everyone enrolled, one row each with its own FORGET (M71): the
                // console's people list, on the phone.
                Label("Enrolled")
                People()
                // Deletes voices for good, so held rather than tapped.
                HoldButton(forgetText, onConfirm = { forget() }, tone = Tone.DANGER, enabled = forgetEnabled)

                Body(
                    "Enrolling does not switch anything on. Whether ${ActiveTheme.name} refuses other " +
                        "voices is set on the server (voice: speaker: mode), and the honest " +
                        "order is: enrol, leave it in observe for a few days, read the scores, " +
                        "then enforce.",
                    dim = true,
                )
            }
        }
    }

    /** The phrase list follows the named person's count. */
    private fun onName(typed: String) {
        nameField = typed
        redo = 0
        status?.let { render(it) }
    }

    /**
     * One row per enrolled person: the name, their own numbers, and a FORGET
     * that deletes that person only. The console draws the same rows.
     */
    @OptIn(ExperimentalLayoutApi::class)
    @Composable
    private fun People() {
        val fresh = status ?: return
        if (fresh.people.isEmpty()) {
            Body("Nobody is enrolled yet — the gate is inert until somebody reads the phrases.", dim = true)
            return
        }
        Projection(Modifier.fillMaxWidth()) {
            fresh.people.forEachIndexed { index, person ->
                if (index > 0) Hairline()
                val numbers = buildString {
                    append("${person.samples} of ${person.maxSamples}")
                    if (person.threshold > 0) append(String.format(" · threshold %.2f", person.threshold))
                    val worst = person.worstSelfScore
                    if (person.thresholdMeasured && worst != null) append(String.format(" · worst %.2f", worst))
                    if (!person.enrolled) append(" · needs ${(fresh.minSamples - person.samples).coerceAtLeast(1)} more")
                }
                // The level first: it is the word that decides what a recognised
                // voice may have done. A tap cycles it (owner → member → guest), the
                // same write the console's People screen makes; a house from before
                // levels shows it blank until the first tap names one.
                val level = person.level.ifBlank { "level" }
                val next = LEVELS[(LEVELS.indexOf(person.level) + 1).mod(LEVELS.size)]
                // One thing to TalkBack: who, their level, their numbers.
                Column(Modifier.semantics(mergeDescendants = true) { contentDescription = "${person.label}, $level: $numbers" }) {
                    Body(person.label)
                    Body(numbers, dim = true)
                }
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                    verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
                ) {
                    ActionButton(level.replaceFirstChar { it.uppercase() }, onClick = { setLevel(person.label, next) })
                    // Free whenever nothing is recording or in flight: a half-enrolled name can go too.
                    HoldButton("Forget", onConfirm = { forget(person.label) }, tone = Tone.DANGER, enabled = testEnabled)
                }
            }
        }
    }

    // --- capture ------------------------------------------------------------

    /** True between [startCapture] and [stopCapture]. The mic is the state. */
    private val recording: Boolean get() = mic != null

    /** The record button's one job, whichever half of it is showing. */
    private fun toggleRecording() {
        if (recording) finishCapture() else startCapture(Mode.ENROL)
    }

    /**
     * Ends a capture that the user did not.
     *
     * Not a silence detector — see the record button. This is for the phone put
     * down mid-phrase, and it fires far enough out that a person reading a line
     * aloud will never meet it.
     */
    private val autoStop = Runnable { finishCapture() }

    /**
     * The one moving thing that says how much time is left.
     *
     * Posted on the same handler as [autoStop] and cancelled by the same call,
     * so a capture that ends early cannot leave a countdown ticking against a
     * dead microphone.
     */
    private val countdown = object : Runnable {
        override fun run() {
            if (!recording) return
            val left = captureEndsAt - SystemClock.uptimeMillis()
            val seconds = ((left + 999L) / 1000L).coerceAtLeast(0L)
            // The status line, not the orb's caption. The caption is part of
            // the orb's chrome, which this screen turns off because at 200dp
            // the wordmark lands on top of the orb — so a countdown drawn there
            // would not appear at all.
            statusLine = "$listeningPrompt  ${seconds}s"
            main.postDelayed(this, 200L)
        }
    }

    /** The first half of the listening line; the countdown appends to it. */
    private var listeningPrompt = ""

    private var captureEndsAt = 0L

    private fun startTest() {
        if (busy || recording) return
        val current = status
        if (current == null || !current.usable) {
            toast("Enrol at least ${current?.minSamples ?: 3} phrases first.")
            return
        }
        prompt = "Say anything at all, in your ordinary voice."
        startCapture(Mode.TEST)
    }

    private fun startCapture(which: Mode) {
        if (busy || recording) return
        // Recorded BEFORE the permission check, because the grant callback
        // resumes this call and has only `mode` to tell it which capture the
        // user asked for. Set it after, and TEST-then-grant silently enrolled
        // an "say anything at all" utterance as a training sample.
        mode = which
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
            return
        }
        // Not listening while you enrol (M79): the phrase about to be read is
        // not a command. Sent before the recorder opens, off the main thread,
        // and never waited for.
        Thread { client?.enrolling() }.start()
        capture = ByteArrayOutputStream()
        val streamer = MicStreamer(
            onPcm = { buffer, length ->
                val sink = capture ?: return@MicStreamer
                // Bounded: a capture left running by a phone in a pocket must
                // not grow the heap until the process dies.
                if (sink.size() < MAX_SAMPLE_BYTES) sink.write(buffer, 0, length)
            },
            onLevel = { level -> orb.setAmplitude(level) },
            onUnavailable = { reason ->
                main.post {
                    stopCapture()
                    statusLine = reason
                    detail = ""
                }
            },
        )
        // Assigned BEFORE start, so the failure path below has something to
        // release. Started inside `also` and assigned after, a throw left the
        // half-opened AudioRecord with no reference anywhere: `stopCapture`
        // stopped the `mic` field, which was still null.
        mic = streamer
        try {
            streamer.start()
        } catch (t: Throwable) {
            stopCapture()
            statusLine = "Could not open the microphone."
            return
        }

        // Only once the mic is genuinely open. Showing LISTENING beside a
        // microphone that failed to start is the same lie the dead orb told.
        val window = if (which == Mode.TEST) TEST_WINDOW_MS else ENROL_WINDOW_MS
        captureEndsAt = SystemClock.uptimeMillis() + window
        orb.setPhase(Phase.LISTENING)
        listeningPrompt = if (which == Mode.TEST) {
            "Listening — say anything, then tap again."
        } else {
            "Listening — say the line, then tap again."
        }
        statusLine = listeningPrompt
        detail = ""
        // The previous sample's verdict is about the previous sample.
        lastNote = null
        main.postDelayed(autoStop, window)
        main.post(countdown)
        syncButtons()
    }

    private fun finishCapture() {
        val pcm = capture?.toByteArray()
        val which = mode
        stopCapture()
        if (pcm == null || pcm.size < MIN_SAMPLE_BYTES) {
            statusLine = "That was too short — tap, say the whole line, then tap again."
            return
        }
        // The orb keeps moving across the round trip. A screen that goes still
        // the moment you stop talking reads as a screen that has stopped
        // working, which is most of what the original bug felt like.
        orb.setPhase(Phase.THINKING)
        statusLine = if (which == Mode.TEST) "Checking…" else "Learning your voice…"
        if (which == Mode.TEST) submitTest(pcm) else submitEnrolment(pcm)
    }

    private fun stopCapture() {
        // Named callbacks, not `removeCallbacksAndMessages(null)`. That cleared
        // EVERY message on the main handler, including the `main.post` a worker
        // thread uses to hand a server response back — and that post is what
        // clears `busy` and re-enables the buttons. Losing one left the screen
        // permanently inert with no error anywhere.
        main.removeCallbacks(autoStop)
        main.removeCallbacks(countdown)
        captureEndsAt = 0L
        mic?.stop()
        mic = null
        capture = null
        orb.setAmplitude(0f)
        orb.setPhase(Phase.IDLE)
        syncButtons()
    }

    // --- server round trips -------------------------------------------------
    private fun refresh() {
        // The loading state, said in words: the buttons are disabled for the
        // round trip, and a screen that is silent while disabled reads as a
        // screen that has stopped working.
        if (status == null) {
            statusLine = "Asking ${ActiveTheme.name} who is enrolled…"
            detail = ""
        }
        offMainThread({ it.status() }) { render(it) }
    }

    private fun submitEnrolment(pcm: ByteArray) =
        offMainThread({ it.enrol(pcm, label) }) { enrolment ->
            // The phrase list advances because the SERVER's count advanced —
            // see [promptIndex]. Nothing is counted here.
            redo = 0
            // The per-sample verdict the API exists to provide, said out loud
            // for the first time. Null when there is nothing wrong with it.
            lastNote = enrolment.note()
            render(enrolment.status)
        }

    /** Make [who] an owner, member or guest: the house's People write, from the phone. */
    private fun setLevel(who: String, level: String) = offMainThread({ it.setLevel(who, level) }) { fresh ->
        toast("$who is now a $level.")
        render(fresh)
    }

    /** Forget one person by name, or everyone when [who] is null. */
    private fun forget(who: String? = null) = offMainThread({ it.forget(who) }) { fresh ->
        redo = 0
        lastNote = null
        toast(if (who == null) "Every voiceprint deleted." else "$who's voiceprint deleted.")
        render(fresh)
    }

    /**
     * "That one was not my best — let me say it again."
     *
     * ## What this can and cannot do
     *
     * It re-offers the phrase just read, so the next sample is another go at
     * the same line. It does **not** delete the sample already stored, because
     * `/api/voice/speaker` has no way to: the four endpoints are status, enrol,
     * verify and forget-everything, and there is no per-sample delete to call.
     * Saying so on screen is the honest half of this button — a REDO that
     * quietly left the bad sample in the profile would be worse than none.
     *
     * A sample the server REFUSED — too quiet, no measurable pitch — was never
     * added, so for that case redo genuinely is a clean second attempt, and
     * that is the case this button is mostly for.
     */
    private fun redo() {
        if (busy || recording) return
        val phrases = status?.prompts.orEmpty()
        if (promptIndex <= 0 || phrases.isEmpty()) {
            toast("There is no earlier phrase to go back to.")
            return
        }
        redo += 1
        lastNote = "Reading that line again. The sample you already gave stays in " +
            "the profile — the server has no way to remove just one — so this " +
            "adds to it rather than replacing it. FORGET MY VOICE is the only " +
            "clean restart."
        status?.let { render(it) }
    }

    private fun submitTest(pcm: ByteArray) = offMainThread({ it.verify(pcm) }) { result ->
        val verdict = result.optJSONObject("verdict")
        val accepted = verdict?.optBoolean("accepted") ?: false
        val score = verdict?.optDouble("score") ?: Double.NaN
        val threshold = verdict?.optDouble("threshold") ?: Double.NaN
        val blocked = result.optBoolean("would_block", false)
        // WHO (M71): the verdict names the person it accepted, and on a
        // refusal the nearest enrolled person — so a false reject of the owner
        // reads as "nearest: owner", not as a stranger with no explanation.
        val who = verdict?.let { textOf(it, "label") }.orEmpty()
        val nearest = verdict?.let { textOf(it, "nearest") }.orEmpty()
        statusLine = when {
            accepted && who.isNotEmpty() -> "Recognised as $who"
            accepted -> "Recognised"
            nearest.isNotEmpty() -> "Not recognised (nearest: $nearest)"
            else -> "Not recognised"
        }
        // WHICH PART (M105): a block that refused on its own names itself in
        // the reason ("pitch-mismatch"), and the three block scores say which
        // part of the voice the gate found far, or near — the same line the
        // console draws, in the order the gate weighs them.
        val reason = verdict?.let { textOf(it, "reason") }.orEmpty()
        val vetoed = if (reason.endsWith("-mismatch")) reason.removeSuffix("-mismatch") else ""
        val blocks = verdict?.optJSONObject("blocks")
        val blockLine = listOf("timbre", "variability", "pitch")
            .filter { blocks?.has(it) == true }
            .joinToString(" · ") { String.format("%s %.2f", it, blocks!!.optDouble(it)) }
        detail = buildString {
            append(String.format("score %.2f against threshold %.2f", score, threshold))
            if (vetoed.isNotEmpty()) append("\nRefused on $vetoed: that part of the voice is far from the enrolment.")
            if (blockLine.isNotEmpty()) append("\n$blockLine")
            append(if (blocked) "\nWith enforcement on, that turn would have been refused."
                   else "\nThat turn would have been allowed.")
            if (!accepted && !blocked) {
                append("\n(The gate is not enforcing, so nothing was blocked.)")
            }
        }
        refresh()
    }

    /**
     * Run one network call off the main thread and hand the result back on it.
     *
     * Buttons are disabled for the duration rather than debounced: two
     * enrolments in flight at once would race on the profile, and the second
     * response would overwrite the first's view of how many samples exist.
     */
    private fun <T> offMainThread(
        call: (VoiceIdentityClient) -> VoiceIdentityClient.Result<T>,
        onOk: (T) -> Unit,
    ) {
        if (busy) return
        // No client means this phone is not paired. Taking the lambda a client
        // rather than closing over one is what makes that a message instead of
        // an UninitializedPropertyAccessException on a worker thread.
        val live = client
        if (live == null) {
            statusLine = "Pair this phone with ${ActiveTheme.name} first."
            detail = ""
            return
        }
        busy = true
        syncButtons()
        thread {
            val result = call(live)
            main.post {
                busy = false
                // Whatever the answer, the orb stops pretending to think.
                if (!recording) orb.setPhase(Phase.IDLE)
                syncButtons()
                when (result) {
                    is VoiceIdentityClient.Result.Ok -> onOk(result.value)
                    is VoiceIdentityClient.Result.Failed -> {
                        // The headline is the client's, not a constant. "Could
                        // not reach Jarvis" was shown for a 404 from a server
                        // that answered in 20 ms, which sent people to look at
                        // their network instead of at their jarvis-core
                        // version. See VoiceIdentityClient.failureFor.
                        statusLine = result.headline
                        detail = result.message
                        if (!recording) orb.setPhase(Phase.ERROR)
                    }
                }
            }
        }
    }

    /** A JSON string, or "" for absent AND for JSON null — `optString` says "null" for the latter. */
    private fun textOf(json: org.json.JSONObject, key: String): String {
        val value = json.opt(key) ?: return ""
        return if (value == org.json.JSONObject.NULL) "" else value.toString()
    }

    /**
     * One place that decides what may be pressed, called from every transition.
     *
     * It replaces a boolean `setButtonsEnabled`, which could not express the
     * state this screen now has: while a capture is live the record button must
     * stay pressable — it is the only way to stop — and the other two must not,
     * because TEST mid-enrolment used to rewrite the prompt and then bail out
     * of `startCapture`, leaving the screen recording an enrolment sample under
     * a caption asking for a test one.
     */
    private fun syncButtons() {
        val paired = client != null
        recordEnabled = paired && (recording || !busy)
        recordText = if (recording) RECORD_STOP else RECORD_START
        testEnabled = paired && !busy && !recording
        forgetEnabled = paired && !busy && !recording && (status?.enrolled == true)
        // The word says what it deletes: one voice, or the whole household.
        forgetText = if ((status?.people?.size ?: 0) > 1) FORGET_ALL else FORGET_ONE
        nameEnabled = paired && !busy && !recording
        // Only once there is an earlier phrase to go back to. Live from the
        // FIRST accepted sample, which is exactly when "that was too quiet"
        // first becomes possible to think.
        redoEnabled = paired && !busy && !recording && promptIndex > 0
    }

    /**
     * One row per phrase, marked with what has happened to it.
     *
     * Text glyphs rather than icons: they survive any font and any accessibility
     * scale, and they copy into a bug report as-is. The glyphs are drawings, though,
     * and TalkBack reads "[ok]" as bracket-o-k-bracket, so each row says it in
     * English instead — and the current one says so, because "which line am I meant
     * to be reading" is the entire question this list answers.
     */
    private fun renderSteps(fresh: VoiceIdentityClient.Status) {
        val phrases = fresh.prompts
        val next = promptIndex
        val rows = phrases.mapIndexed { index, phrase ->
            val given = index < next
            val current = index == next
            Step(
                text = "${when { given -> "[ok]"; current -> "[>>]"; else -> "[  ]" }}  $phrase",
                said = when {
                    given -> "Given: $phrase"
                    current -> "Say this now: $phrase"
                    else -> "Still to say: $phrase"
                },
                tone = when {
                    given -> Tone.OK
                    current -> Tone.ACCENT
                    else -> null
                },
            )
        }
        val done = "Every phrase has been given. More samples only help if ${ActiveTheme.name} " +
            "stops recognising you — say something different each time."
        steps = if (phrases.isNotEmpty() && next >= phrases.size) rows + Step(done, done, null) else rows
    }

    private fun render(fresh: VoiceIdentityClient.Status) {
        status = fresh
        // Cache what the server is doing, so a turn starting later does not
        // need a round trip to find out whether on-device transcription would
        // bypass the gate. See JarvisConfig.speakerGateEnforcing.
        config.speakerGateEnforcing = fresh.mode == "enforce" && fresh.enrolled
        val prompts = fresh.prompts
        // Read AFTER `status` is assigned: promptIndex is derived from it.
        val index = promptIndex
        prompt = when {
            prompts.isEmpty() -> "Say a sentence in your ordinary voice."
            index < prompts.size -> prompts[index]
            else -> "That is enough — add more only if it stops recognising you."
        }
        renderSteps(fresh)
        // The named person's own count, not the first person's; and the
        // household when there is one.
        val person = fresh.personNamed(label)
        statusLine = when {
            fresh.people.size > 1 -> {
                val total = fresh.people.sumOf { it.samples }
                "${fresh.people.size} people · $total samples · gate is ${fresh.mode}"
            }
            person != null && person.enrolled -> "${person.samples} of ${person.maxSamples} samples · gate is ${fresh.mode}"
            person != null -> "$label: ${person.samples} of ${fresh.minSamples} phrases so far"
            fresh.enrolled -> "$label is new — ${fresh.minSamples} phrases needed · gate is ${fresh.mode}"
            else -> "Not enrolled — ${fresh.minSamples} phrases needed"
        }
        val worst = fresh.worstSelfScore
        detail = when {
            person == null || !person.enrolled ->
                "Nothing is checked for $label until there are ${fresh.minSamples} samples."
            fresh.configuredThreshold != null -> String.format(
                "The server names a threshold of %.2f in configuration.yaml, and that is what is " +
                    "in force; enrolment suggests %.2f.",
                fresh.configuredThreshold, fresh.suggestedThreshold,
            )
            !fresh.usable ->
                "Nothing is checked until there are ${fresh.minSamples} samples."
            // A threshold that was never measured must not be reported as one.
            // The screen used to print "enrolment suggests 4.00" on a profile
            // whose leave-one-out scores were every one of them infinite —
            // 4.00 is the server's default — while telling the owner to read
            // the scores before enforcing. There were no scores.
            !fresh.thresholdMeasured || worst == null -> String.format(
                "%d samples is enough to check against, but not enough to measure " +
                    "against: scoring one of your samples needs the other %d. Record " +
                    "%d more and this will say what your own worst sample scores. " +
                    "Until then %.2f is the server's default, not a measurement.",
                fresh.samples,
                fresh.minSamples,
                (fresh.measureSamples - fresh.samples).coerceAtLeast(1),
                fresh.suggestedThreshold,
            )
            else -> String.format(
                "Your own worst sample scores %.2f. Enrolment suggests a threshold of " +
                    "%.2f, which is what is in force unless the server names one.",
                worst, fresh.suggestedThreshold,
            )
        }
        // The per-sample verdict goes FIRST and above the profile-wide numbers.
        // It is about the thing the user just did, and it is the reason this API
        // is one sample per request; burying it under a threshold calculation
        // would be throwing it away a second time.
        lastNote?.let { detail = "$it\n\n${detail}" }
        syncButtons()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_MIC) return
        if (grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) {
            statusLine = "${ActiveTheme.name} cannot learn a voice it is not allowed to hear."
            return
        }
        // The tap that raised the dialog was a tap meaning "listen now".
        // Answering it and then finding the screen exactly as it was — no orb,
        // no countdown, no recording — reads as a grant that did not take.
        startCapture(mode)
    }

    private fun toast(message: String) = ai.jarvis.app.ui.ScreenNote.show(this, message)

    private companion object {
        const val REQ_MIC = 6001

        /** 16 kHz mono 16-bit. Half a second is below anything usable. */
        const val MIN_SAMPLE_BYTES = 16000 * 2 / 2

        /** 25 s, comfortably under the server's own limit. */
        const val MAX_SAMPLE_BYTES = 16000 * 2 * 25

        /**
         * How long a test capture runs before ending itself.
         *
         * Both windows are backstops now rather than the mechanism: the user
         * taps to stop. They are still generous, because a window that expires
         * mid-sentence truncates the sample, and a truncated ENROLMENT sample
         * is written into the profile and skews it — the same reason nothing
         * here listens for silence.
         */
        const val TEST_WINDOW_MS = 15_000L

        /** Under [MAX_SAMPLE_BYTES] (25 s), so the cap is never what ends one. */
        const val ENROL_WINDOW_MS = 20_000L

        const val RECORD_START = "Tap to speak"
        const val RECORD_STOP = "Tap to stop"

        /** What the bottom button deletes: one voice, or the household. */
        const val FORGET_ONE = "Forget my voice"
        const val FORGET_ALL = "Forget everyone"
    }
}
