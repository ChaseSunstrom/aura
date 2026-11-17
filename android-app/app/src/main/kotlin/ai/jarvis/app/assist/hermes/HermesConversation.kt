package ai.jarvis.app.assist.hermes

import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.assist.ConversationRegistry
import ai.jarvis.app.assist.JarvisConversation
import ai.jarvis.app.assist.MicStreamer
import ai.jarvis.app.assist.OnDeviceStt
import ai.jarvis.app.assist.OnPhoneSpeech
import ai.jarvis.app.assist.TtsPlayer
import ai.jarvis.app.assist.VoiceActivity
import ai.jarvis.app.audio.CaptureProfile
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.glass.ReactorFeed
import ai.jarvis.app.ui.ApprovalBridge
import ai.jarvis.app.ui.ApprovalCard
import ai.jarvis.app.assist.Phase
import ai.jarvis.app.assist.ScreenContext
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import kotlin.concurrent.thread

/**
 * The voice turn on Hermes, through the jarvis-adapter — the engine
 * [JarvisConversation] runs when the server kind is HERMES.
 *
 * Same loop as the pipeline engine, different plumbing:
 *
 *   LISTENING → (VAD end-of-speech) → transcribe (`POST /api/audio/transcribe`,
 *   the turn's PCM as a WAV data URL) → `prompt.submit` (or `command.dispatch
 *   goal` for a "keep working on…" sentence) → events stream the reply into
 *   the same transcript, reply, tool and activity views → `message.complete`
 *   → `POST /api/audio/speak` → play → LISTENING …
 *
 * The microphone is local and always was, so LISTENING is announced the
 * moment it opens rather than after a server handshake; what the server owns
 * is the session ([HermesClient]) and the model, whose cold start the status
 * poller turns into a "warming up model" caption. Barge-in stops playback and,
 * if a turn is still running, sends `session.interrupt`. Approvals go through
 * [ApprovalBridge]'s consent door with the request's own choices; clarifying
 * questions through [ClarifyPrompt]; the goal loop's state through
 * [JarvisConversation.Ui.onGoal]. Every callback on the main thread.
 */
class HermesConversation(
    private val context: Context,
    private val config: JarvisConfig,
    private val ui: JarvisConversation.Ui,
    private val inactivityMs: Long,
    speechAlreadyUnderway: Boolean,
    private val continuous: Boolean,
    private val captureProfile: CaptureProfile,
    /** The conversation is over, by its own decision. The owner posts `onIdle`. */
    private val onEnded: () -> Unit,
    /**
     * A reply is about to play (true) or has finished (false): where the owner ducks
     * the music, only then (the owner, 2026-09-25: "only should do it when speaking
     * and slightly before and after").
     */
    private val onSpeaking: (Boolean) -> Unit = {},
) : HermesClient.Listener {

    private val main = Handler(Looper.getMainLooper())
    private var client: HermesClient? = null
    private var mic: MicStreamer? = null
    private var tts: TtsPlayer? = null

    val hud = HudState()
    private val vad = VoiceActivity(speechAlreadyUnderway = speechAlreadyUnderway)

    /**
     * The turn's own phase: what gates barge-in (only while SPEAKING), the
     * end-of-speech detector (only while LISTENING) and whether a reply that
     * arrives late may still play. This is the conversation's flag and nothing
     * else's — the reactor derives its state from [hud] and the two booleans
     * below through [publishReactor], never from this.
     */
    private var state = Phase.IDLE
    private var running = false

    // No audio focus of its own: JarvisConversation, the only thing that builds this,
    // holds it (ducking) for the whole turn. A second holder made the two duck each
    // other -- AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK, read as a loss -- and every turn
    // ended the moment it began (the owner's 1442 log, 2026-09-25).
    private var held = false
    private var turnActive = false
    private var sawSpeech = false
    /** A turn was submitted in this conversation: a silent turn after it is the follow-up window lapsing. */
    private var exchanged = false
    /** A prompt sent before the session opened (a Share, a sentence typed at once): sent in onSession. */
    private var waiting: Triple<String, String?, Boolean>? = null

    /** Set by the assist screen's conversation alone: the wake word, the car and an ask never take the screen. */
    var takesScreen = false
    /**
     * Turns in a row that gave Jarvis nothing to answer: silence, a voice the speaker
     * gate refused, a transcriber that failed. Each one used to go straight back to
     * listening and restart the inactivity clock, so road noise the detector took for
     * speech, or an owner the gate kept refusing, kept the conversation on Listening
     * for good (the owner, 1447: "still a bug where it gets stuck in listening mode").
     */
    private var emptyTurns = 0
    /** Opened by a head that requires a recognised voice ([ai.jarvis.app.assist.WakePersona.verifyAt]); cleared by the first. */
    private var unverified = false
    private var reportedDeafness = false

    /** The turn's audio: a pre-roll before speech, everything after it, bounded. */
    private val turnAudio = ArrayDeque<ByteArray>()
    private var turnAudioBytes = 0

    /** True while the server is working on a prompt; barge-in interrupts it. */
    private var turnRunning = false

    /** How many times a status poll has found the model warming, for the caption. */
    private var warming = false

    /** What the orb says while the model loads: the adapter's own label when it sends one (T-0583). */
    private var warmingLabel = HermesStatus.WARMING_LABEL

    /** The last status poll got no answer while a turn waited, so the caption says so; cleared when one answers. */
    private var answerMissing = false
    private var lastModel = ""

    /**
     * False from a disconnect that will retry until the session is back:
     * the socket is down, which the reactor shows as offline (§10 row 11)
     * whatever [hud] last heard.
     */
    private var linkUp = true

    /** The "Ask why" line waiting for the running turn to end (T-1054). */
    private val askWhy = AskWhyQueue()

    /** The caption, and the reactor's inputs with it, in one call. */
    private fun mode(phase: Phase, label: String) {
        ui.onMode(phase, label)
        publishReactor()
    }

    /**
     * Everything the reactor needs, gathered from [hud] and the state this
     * engine owns, handed to the surface as one value (GLASS_HUD §10).
     *
     * Called after every caption change, every event that changed the HUD
     * and every status poll — the three moments an input can move. Cheap:
     * a dozen field reads and one small object, on the main thread.
     */
    private fun publishReactor() {
        ui.onReactor(
            ReactorFeed.inputsFrom(
                hud = hud,
                linkUp = linkUp,
                warming = warming,
                speaking = state == Phase.SPEAKING,
                listening = state == Phase.LISTENING,
            ),
        )
    }

    private val clearTools = Runnable { hud.tools.clear(); ui.onTools(hud.tools) }

    private val inactivity: Runnable = Runnable {
        if (!isListening()) return@Runnable
        if (continuous) {
            if (vad.peak <= VoiceActivity.DEAD_MIC_LEVEL && !reportedDeafness) {
                reportedDeafness = true
                ui.onError(DEAD_MIC)
            }
            main.postDelayed(inactivity, inactivityMs)
            return@Runnable
        }
        if (vad.silenceIsAnEnd(sawSpeech, exchanged)) end() else {
            ui.onError(if (vad.peak <= VoiceActivity.DEAD_MIC_LEVEL) DEAD_MIC else NOTHING_HEARD)
            main.postDelayed({ if (running) end() }, ERROR_LINGER_MS)
        }
    }

    private val turnCap = Runnable { if (isListening() && sawSpeech) endTurnAudio() }

    private val handshake = Runnable {
        if (running && (client?.sessionId == null || !linkUp)) {
            ui.onError(NO_SESSION.format(config.hermesUrl))
            main.postDelayed({ if (running) end() }, ERROR_LINGER_MS)
        }
    }

    private val poll = Runnable { pollStatus() }

    val isRunning: Boolean get() = running
    val isHeldForQuestion: Boolean get() = held

    fun start() {
        if (running) return
        running = true
        tts = TtsPlayer(context, config.hermesKey, config.hermesUrl).also {
            it.communicationRoute = captureProfile.useVoiceCommunication
        }
        client = HermesClient(config.hermesUrl, config.hermesKey, this).also {
            it.connect(ConversationRegistry.current(context))
        }
        emptyTurns = 0
        exchanged = false
        unverified = ai.jarvis.app.assist.WakePersona.takeVerify(android.os.SystemClock.uptimeMillis())
        openMic()
        beginNextTurn()
        main.postDelayed(handshake, HANDSHAKE_MS)
        main.post(poll)
    }

    fun stop() {
        if (!running) return
        running = false
        askWhy.drop()
        if (state == Phase.SPEAKING) onSpeaking(false)
        main.removeCallbacks(inactivity)
        main.removeCallbacks(turnCap)
        main.removeCallbacks(handshake)
        main.removeCallbacks(clearTools)
        main.removeCallbacks(poll)
        mic?.stop(); mic = null
        tts?.stop(); tts = null
        client?.close(); client = null
        turnAudio.clear(); turnAudioBytes = 0
        waiting = null
        vad.reset()
        ConversationRegistry.touch(context)
        state = Phase.IDLE
    }

    /** Over, by this engine's own decision: stop, then tell the owner. */
    private fun end() {
        if (!running) return
        stop()
        onEnded()
    }

    /** A sentence typed on the voice screen takes the same path as one spoken. */
    fun sendTyped(text: String) {
        val typed = text.trim()
        if (typed.isEmpty() || !running) return
        emptyTurns = 0
        bargeIn()
        turnActive = false
        main.removeCallbacks(inactivity)
        main.removeCallbacks(turnCap)
        ui.onTranscript(typed)
        submit(typed)
    }

    /**
     * A slash command by name — `goal`, `jgoal` — from the goal
     * card. Its result is shown as a notice; a `send` result starts a turn.
     */
    fun dispatch(name: String, arg: String) {
        val link = client ?: return
        if (link.sessionId == null) {
            ui.onError("Not connected to Hermes yet.")
            return
        }
        link.dispatchCommand(name, arg) { outcome ->
            if (outcome is HermesDispatch.Outcome.Send && running) startedTurn()
        }
    }

    /**
     * Take a HUD panel down from the phone. Removed here optimistically so the ×
     * is instant, then `hud @<sid> dismiss <id>` goes to the plugin, whose own
     * `hud.panel.dismiss` reconciles every client.
     */
    fun dismissPanel(id: String) {
        if (hud.dismissPanelLocally(id)) ui.onPanels(hud.panels)
        client?.dismissPanel(id)
    }

    /** The person's arrangement, to the hud-layout plugin (`hud.layout.user`). */
    fun reportLayout(surface: String, activity: String, layoutJson: String) {
        client?.reportLayout(surface, activity, layoutJson)
    }

    fun settingsGet(key: String, onValue: (Any?) -> Unit) {
        val link = client
        if (link == null) onValue(null) else link.settingsGet(key) { value -> main.post { onValue(value) } }
    }

    fun settingsSet(key: String, valueJson: String) {
        client?.settingsSet(key, valueJson)
    }

    /** A panel's local ttl passed with no dismiss from the emitter: remove it here, no server call. */
    fun expirePanel(id: String) {
        if (hud.dismissPanelLocally(id)) ui.onPanels(hud.panels)
    }

    /** An `approval` panel's REVIEW: raise the same consent door an `approval.request` does. */
    fun raisePanelApproval(panel: Panel) {
        val p = panel.payload
        val id = p.optString("request_id")
        if (id.isEmpty()) return
        val params = p.optString("command").ifEmpty { p.optJSONObject("args")?.toString() ?: p.optString("args") }
        raiseApproval(
            HudState.Approval(
                requestId = id,
                tool = p.optString("tool"),
                params = params,
                reason = p.optString("reason"),
                choices = HudState.choicesOf(p),
            )
        )
    }

    /** A `clarify` panel's ANSWER: raise the same question prompt a `clarify.request` does. */
    fun raisePanelClarify(panel: Panel) {
        val p = panel.payload
        val id = p.optString("request_id")
        if (id.isEmpty()) return
        val options = ArrayList<String>()
        p.optJSONArray("options")?.let { arr ->
            for (i in 0 until arr.length()) {
                val v = arr.opt(i)
                if (v != null && v != JSONObject.NULL) options.add(v.toString())
            }
        }
        askClarify(HudState.Clarify(id, p.optString("question"), options))
    }

    fun endTurnFromButton(): Boolean {
        if (!running || held || !isListening()) return false
        endTurnAudio()
        return true
    }

    fun holdForQuestion(): Boolean {
        if (!running || held) return false
        held = true
        turnActive = false
        main.removeCallbacks(inactivity)
        main.removeCallbacks(turnCap)
        mic?.stop(); mic = null
        tts?.stop()
        return true
    }

    fun resumeAfterQuestion() {
        if (!running || !held) return
        held = false
        openMic()
        vad.reset()
        beginNextTurn()
    }

    // --- the microphone ------------------------------------------------------

    private fun openMic() {
        mic = MicStreamer(
            onPcm = ::capture,
            onLevel = ::onMicLevel,
            captureProfile = { captureProfile },
            onUnavailable = { reason ->
                ui.onError(reason)
                main.postDelayed({ if (running) end() }, ERROR_LINGER_MS)
            },
        ).also { it.start() }
    }

    /** Capture thread. Keeps the turn's audio; nothing is sent until it ends. */
    private fun capture(buf: ByteArray, len: Int) {
        if (!turnActive || len <= 0) return
        synchronized(turnAudio) {
            if (sawSpeech && turnAudioBytes >= MAX_TURN_BYTES) return
            turnAudio.addLast(buf.copyOf(len))
            turnAudioBytes += len
            // Before speech: a short pre-roll only, so the first syllable is
            // kept and a minute of quiet room is not.
            if (!sawSpeech) {
                while (turnAudioBytes > PRE_ROLL_BYTES && turnAudio.size > 1) {
                    turnAudioBytes -= turnAudio.removeFirst().size
                }
            }
        }
    }

    /** Whether a sound during Jarvis's reply is the person cutting in, or Jarvis heard back. */
    private val barge = BargeIn()

    private fun onMicLevel(level: Float) {
        ui.onAmplitude(level)
        val now = SystemClock.elapsedRealtime()
        if (state == Phase.SPEAKING) {
            if (barge.hear(now, level, vad.floor)) {
                bargeIn()
                beginNextTurn()
            }
            return
        }
        if (!isListening()) return
        when (vad.onLevel(now, level)) {
            VoiceActivity.Verdict.STARTED -> {
                sawSpeech = true
                main.removeCallbacks(inactivity)
                main.removeCallbacks(turnCap)
                main.postDelayed(turnCap, MAX_TURN_MS)
            }
            VoiceActivity.Verdict.ENDED -> endTurnAudio()
            VoiceActivity.Verdict.SPEAKING, VoiceActivity.Verdict.QUIET -> Unit
        }
    }

    /** Talking over the reply, or a new push-to-talk: stop playback, stop the turn. */
    private fun bargeIn() {
        tts?.stop()
        if (turnRunning) {
            turnRunning = false
            hud.turnInterrupted()
            client?.interrupt()
        }
    }

    private fun isListening() = running && !held && state == Phase.LISTENING && turnActive

    private fun beginNextTurn() {
        if (state == Phase.SPEAKING) onSpeaking(false)
        if (!running || held) return
        synchronized(turnAudio) { turnAudio.clear(); turnAudioBytes = 0 }
        sawSpeech = false
        vad.newTurn()
        turnActive = true
        state = Phase.LISTENING
        main.removeCallbacks(turnCap)
        ui.onTranscript("")
        ui.onResponse("")
        mode(Phase.LISTENING, "Listening")
        if (inactivityMs > 0) {
            main.removeCallbacks(inactivity)
            main.postDelayed(inactivity, inactivityMs)
        }
    }

    /** End of speech: the turn's audio goes to the recogniser. */
    private fun endTurnAudio() {
        if (!turnActive) return
        turnActive = false
        main.removeCallbacks(turnCap)
        main.removeCallbacks(inactivity)
        val pcm = synchronized(turnAudio) {
            val out = ByteArray(turnAudioBytes)
            var at = 0
            for (chunk in turnAudio) { System.arraycopy(chunk, 0, out, at, chunk.size); at += chunk.size }
            turnAudio.clear(); turnAudioBytes = 0
            out
        }
        state = Phase.THINKING
        mode(Phase.THINKING, "Processing")
        val link = client ?: return
        thread(name = "jarvis-hermes-stt", isDaemon = true) {
            val outcome = link.transcribe(WavAudio.wrapPcm16(pcm))
            val heard = (outcome as? HermesClient.Transcribed.Got)?.heard
                ?: heardOffline(pcm, networkDown = outcome == HermesClient.Transcribed.Offline)
            main.post { if (running) onTranscribed(heard) }
        }
    }

    /**
     * The server did not transcribe: the bundled model hears the captured audio when it is on
     * the phone, otherwise the screen says why (OnDeviceStt, tests/contracts/offline-voice.json).
     * Runs on the stt thread: loading the model takes seconds. Null keeps the old behaviour, a
     * return to listening. The bundled model is tried either way, but only a [networkDown] turn
     * is told "without the server": a server that answered and failed has already said why
     * through [onError], and calling that offline was the wrong thing to tell the owner (T-1079).
     */
    private fun heardOffline(pcm: ByteArray, networkDown: Boolean): HermesClient.Heard? {
        val model = if (OnPhoneSpeech.ready(context)) OnPhoneSpeech.shared(context) else null
        val hearer = OnDeviceStt.choose(
            audioInHand = true,
            // No words from the server, whatever the reason: the bundled model is the way on.
            serverReachable = false,
            bundledModel = model != null,
            platformRecognizer = false,
            gateEnforcing = config.speakerGateEnforcing,
        )
        if (hearer == OnDeviceStt.Hearer.BUNDLED && model != null) {
            val samples = FloatArray(pcm.size / 2) {
                ((pcm[2 * it + 1].toInt() shl 8) or (pcm[2 * it].toInt() and 0xff)).toShort() / 32768f
            }
            val words = runCatching { model.transcribe(samples) }.getOrDefault("")
            return HermesClient.Heard(words, dropped = false, label = null, ticket = null)
        }
        if (networkDown) OnDeviceStt.says(hearer)?.let { line -> main.post { if (running) ui.onResponse(line) } }
        return null
    }

    /** Back to listening after a turn that gave nothing, or over once [MAX_EMPTY_TURNS] have. */
    private fun listenAgainOrEnd() {
        emptyTurns++
        main.postDelayed({ if (running) { if (emptyTurns >= MAX_EMPTY_TURNS) end() else beginNextTurn() } }, ERROR_LINGER_MS)
    }

    private fun onTranscribed(heard: HermesClient.Heard?) {
        if (heard == null) {
            // The client already said why. Back to listening rather than out.
            listenAgainOrEnd()
            return
        }
        if (ai.jarvis.app.assist.WakePersona.refused(heard.dropped, heard.ticket, heard.transcript, unverified)) {
            // The house's speaker gate refused the voice and the adapter dropped the words
            // before any prompt existed, or "Aura" opened the turn and the voice earned no
            // ticket (PROMPT §4), so nothing is sent. Said as that, not as silence:
            // "I didn't catch that" would send the person closer to the microphone.
            ui.onTranscript("")
            ui.onResponse(NOT_RECOGNISED)
            ai.jarvis.app.assist.VoiceClaim.offer(context, heard.claim, "on this phone")
            listenAgainOrEnd()
            return
        }
        if (heard.transcript.isEmpty()) {
            // Silence: said here, on the phone, and nothing is submitted.
            ui.onTranscript("")
            ui.onResponse(DIDNT_CATCH)
            listenAgainOrEnd()
            return
        }
        emptyTurns = 0
        unverified = false
        ui.onTranscript(heard.transcript)
        // The surface's own words ("answer", "reroute" in the car) are done there, not asked
        // of Jarvis: a turn would take seconds and could answer the call with a tool.
        ui.onHeard(heard.transcript)?.let { line ->
            if (!running) return
            ui.onResponse(line)
            speak(line)
            return
        }
        submit(heard.transcript, heard.ticket, voice = true)
    }

    /**
     * A prompt, or a goal when the sentence opens like one; a spoken one carries the gate's
     * ticket and `voice`, which says it was spoken even when the gate gave no ticket.
     */
    private fun submit(text: String, speakerTicket: String? = null, voice: Boolean = false) {
        val link = client ?: return
        if (link.sessionId == null || !linkUp) {
            // Still connecting, or redialling with the old session id still set: this used to say
            // "not connected" and drop the line (AUDIT B5). onSession sends it; the handshake timer
            // ends a link that does not come back, and dismissing the overlay drops it.
            waiting = Triple(text, speakerTicket, voice)
            state = Phase.THINKING
            if (link.sessionId == null) mode(Phase.THINKING, "Connecting") else {
                mode(Phase.THINKING, "Reconnecting")
                main.removeCallbacks(handshake)
                main.postDelayed(handshake, HANDSHAKE_MS)
            }
            return
        }
        exchanged = true
        state = Phase.THINKING
        mode(Phase.THINKING, if (warming) warmingLabel else "Processing")
        startedTurn()
        // The screen JARVIS was called up over goes with the first thing said, once (T-0824):
        // taken here, where the prompt is sent, and only by the conversation the call opened.
        val seen = if (takesScreen) ScreenContext.take() else null
        if (HermesDispatch.isGoal(text)) {
            // A goal is kept and worked on for turns: the screen's text is not put in one.
            link.dispatchCommand("goal", text)
        } else {
            link.submitPrompt(ScreenContext.frame(text, seen), speakerTicket, voice) { reply ->
                if (reply is HermesRpc.Reply.Error) {
                    turnRunning = false
                    ui.onError("Hermes refused the prompt: ${reply.message} (${reply.code})")
                    main.postDelayed({ if (running) beginNextTurn() }, ERROR_LINGER_MS)
                }
            }
        }
    }

    private fun startedTurn() {
        hud.turnStarted()
        turnRunning = true
        main.removeCallbacks(clearTools)
        ui.onTools(hud.tools)
        ui.onReasoning("")
        // Faster polling while the first token is awaited: a cold model is
        // the one time the caption has something to say.
        main.removeCallbacks(poll)
        main.postDelayed(poll, HermesStatus.POLL_PENDING_MS)
    }

    /** One line said on request (`hud.say`): played where the state allows, and never queued behind a reply. */
    private fun sayLine(text: String, voice: String) {
        val link = client ?: return
        val player = tts ?: return
        thread(name = "jarvis-hermes-say", isDaemon = true) {
            if (!running || state == Phase.SPEAKING) return@thread
            var lineOut: TtsPlayer.PcmLine? = null
            val streamed = link.speakStream(
                text, voice,
                onStart = { rate -> lineOut = player.PcmLine(rate) },
                onPiece = { piece -> lineOut?.write(piece) },
            )
            if (streamed) {
                lineOut?.finish {}
                return@thread
            }
            lineOut?.close()
            val audio = link.speak(text, voice) ?: return@thread
            main.post {
                if (!running || state == Phase.SPEAKING) return@post
                player.playBytes(audio) {}
            }
        }
    }

    /**
     * The reply is complete: speak it, then listen again.
     *
     * Streamed first (`speak-pcm`, the first word while the rest is still being
     * made), the whole clip when the voice does not stream. The pieces are fed
     * on the network thread; a barge-in `stop()`s the player from the main
     * thread and the feed ends on its own.
     */
    private fun speak(text: String) {
        val link = client ?: return
        if (text.isBlank()) {
            beginNextTurn()
            return
        }
        state = Phase.SPEAKING
        onSpeaking(true)
        barge.reset(SystemClock.elapsedRealtime())
        mode(Phase.SPEAKING, "Responding")
        // The primary's voice: the persona and its clip share a name, and an
        // empty name is the adapter's default, JARVIS. Read once, here, so a
        // switch mid-line does not change the voice halfway through a sentence.
        val voice = hud.persona
        thread(name = "jarvis-hermes-tts", isDaemon = true) {
            val player = tts
            if (player != null) {
                var lineOut: TtsPlayer.PcmLine? = null
                val streamed = link.speakStream(
                    text, voice,
                    onStart = { rate -> lineOut = player.PcmLine(rate) },
                    onPiece = { piece -> lineOut?.write(piece) },
                )
                if (streamed) {
                    lineOut?.finish { main.post { if (running && state == Phase.SPEAKING) beginNextTurn() } }
                    return@thread
                }
                lineOut?.close()
            }
            val audio = link.speak(text, voice)
            main.post {
                if (!running || state != Phase.SPEAKING) return@post
                if (audio == null || player == null) {
                    beginNextTurn()
                } else {
                    player.playBytes(audio) { if (running && state == Phase.SPEAKING) beginNextTurn() }
                }
            }
        }
    }

    // --- the model's state -------------------------------------------------------

    private fun pollStatus() {
        val link = client ?: return
        if (!running) return
        thread(name = "jarvis-hermes-status", isDaemon = true) {
            val status = link.status()
            main.post { if (running) onStatus(status) }
        }
    }

    private fun onStatus(status: HermesStatus?) {
        if (status != null) {
            warming = status.warmingUp
            if (warming) warmingLabel = status.caption
            if (status.model.isNotEmpty()) lastModel = status.model
            // A switch that happened before this phone connected: the status says who.
            if (status.persona.isNotEmpty() && status.persona != hud.persona) {
                hud.persona = status.persona
                ui.onPersona(hud.persona)
            }
            ui.onModel(lastModel, warming)
        }
        if (state == Phase.THINKING && !hud.tokenSeen) {
            val waiting = HermesStatus.waitingCaption(status)
            when {
                waiting != null -> mode(Phase.THINKING, waiting)
                // The adapter answers again: a turn still waiting gets its plain caption back, not the no-answer text.
                answerMissing -> mode(Phase.THINKING, "Processing")
                else -> publishReactor()
            }
            answerMissing = status == null
        } else {
            answerMissing = false
            publishReactor()
        }
        main.removeCallbacks(poll)
        main.postDelayed(poll, HermesStatus.pollIntervalMs(hud.turnPending, hud.tokenSeen))
    }

    // --- HermesClient.Listener (main thread) --------------------------------------

    override fun onSession(sessionId: String, resumed: Boolean) {
        main.removeCallbacks(handshake)
        linkUp = true
        // The stored id is the one a later conversation can resume (HermesClient.storedSessionId).
        ConversationRegistry.remember(context, client?.storedSessionId ?: sessionId)
        Log.i(TAG, if (resumed) "resumed session" else "new session")
        // Back from RECONNECTING: the caption says what the state already is.
        if (state == Phase.LISTENING) mode(Phase.LISTENING, "Listening")
        // Seed the panel surface once the session is attached, so a late joiner
        // (or a reconnect) sees the panels already up — docs/hermes-hud-protocol.md
        // § Commands. The events are the authority; a staler row cannot clobber one.
        client?.listPanels { rows -> if (hud.seedPanels(rows)) ui.onPanels(hud.panels) }
        // Woken by a persona's own name ("Hey Aura"): that persona answers.
        ai.jarvis.app.assist.WakePersona.take()?.let { name -> client?.dispatchCommand(PERSONA_COMMAND, name) }
        // The HUD's settings, each on its own so one refusal does not stop
        // the others: a gateway without the settings plugin gives a HUD with
        // no saved layouts, not a HUD that never finished starting.
        for (key in HUD_SETTING_KEYS) settingsGet(key) { value -> if (running) ui.onSetting(key, value) }
        // The routines this phone watches, made anywhere (`aura-hud/routines.py`).
        client?.fetchRoutines { json -> json?.let { ai.jarvis.app.routines.RoutineWatcher.sync(context, it) } }
        // The signing key behind sensitive approvals (T-0945): registered once, quietly.
        client?.registerSigningKey(context)
        waiting?.let { (text, ticket, voice) -> waiting = null; submit(text, ticket, voice) }
    }

    override fun onEvent(type: String, sessionId: String, payload: JSONObject) {
        // Music on this phone (`phone_media`), whichever surface the turn is on.
        if (type == ai.jarvis.app.assist.PhoneMedia.EVENT) {
            main.post { ai.jarvis.app.assist.PhoneMedia.handle(context, payload) }
            return
        }
        if (type == ai.jarvis.app.assist.PhoneAction.EVENT) {
            ai.jarvis.app.assist.PhoneAction.handle(context, payload) { id, body -> client?.answerPhone(id, body) }
            return
        }
        if (type == ai.jarvis.app.routines.RoutineWatcher.SYNC_EVENT) {
            ai.jarvis.app.routines.RoutineWatcher.sync(context, payload)
            return
        }
        if (type == SAY_EVENT) {
            // `hud_say`: a line the person asked to hear, in the named voice,
            // whatever else is going on -- except over their own sentence.
            val line = payload.optString("text").trim()
            if (line.isNotEmpty() && !(turnActive && sawSpeech)) sayLine(line, payload.optString("voice"))
            return
        }
        if (type == ai.jarvis.app.car.voice.HermesCarJarvis.NAVIGATE_EVENT) {
            // `car_navigate`: the car's own map draws it; any other screen opens it where the
            // person is, and the waiting call is told where (or why not). The popup dropped it
            // while Jarvis said the phone had the route open (2026-11-09). Main thread.
            if (!running) return
            val answer = if (ui.onNavigate(payload)) JSONObject().put("status", "ok").put("opened", "car")
                else ai.jarvis.app.car.dash.CarHud.routeEvent(context, payload)
            payload.optString("id").takeIf { it.isNotEmpty() }?.let { client?.answerPhone(it, answer) }
            return
        }
        val changed = hud.apply(type, payload)
        // Settled elsewhere (the console answered, or the adapter's two minutes ran out): the
        // consent screen and its notification come down. Carries no session id for the house's asks.
        if (type == "approval.resolved" || type == "approval.response") {
            ApprovalBridge.dismissResolved(payload.optString("request_id"))
        }
        if (changed.isEmpty()) return
        // A turn the goal loop started by itself is still a turn a new push-to-talk interrupts.
        if (hud.turnPending && !turnRunning) {
            turnRunning = true
            main.removeCallbacks(clearTools)
            ui.onReasoning("")
        }
        if (HudState.Change.MODEL in changed) {
            lastModel = hud.model
            ui.onModel(hud.model, warming)
        }
        if (HudState.Change.PERSONA in changed) ui.onPersona(hud.persona)
        if (HudState.Change.TEXT in changed) ui.onResponse(hud.text)
        if (HudState.Change.REASONING in changed) ui.onReasoning(hud.reasoning)
        if (HudState.Change.TOOLS in changed) {
            main.removeCallbacks(clearTools)
            ui.onTools(hud.tools)
            if (type == "tool.start") ui.onWork()
            if (!hud.tools.running && type == "tool.complete") main.postDelayed(clearTools, hud.tools.holdMs())
        }
        if (HudState.Change.ACTIVITY in changed) ui.onActivity(hud.activity)
        if (HudState.Change.GOAL in changed) ui.onGoal(hud.goal)
        if (HudState.Change.PANELS in changed) ui.onPanels(hud.panels)
        if (HudState.Change.APPROVAL in changed && type == "approval.request") {
            hud.approvals.lastOrNull { it.requestId == payload.optString("request_id") }?.let(::raiseApproval)
        }
        if (HudState.Change.CLARIFY in changed && type == "clarify.request") {
            hud.clarifies.lastOrNull { it.requestId == payload.optString("request_id") }?.let(::askClarify)
        }
        if (HudState.Change.STATUS in changed && hud.status.isNotEmpty() && state == Phase.THINKING) {
            mode(Phase.THINKING, hud.status.lowercase().replaceFirstChar { it.uppercase() }.take(STATUS_CHARS))
        }
        if (HudState.Change.COMPLETE in changed) {
            turnRunning = false
            askWhy.turnEnded(client?.sessionId, ::sendAskWhy)
            // Spoken unless the person is mid-sentence into the microphone —
            // a goal-loop turn completing while they talk is shown, not
            // read over the top of them — or a reply is already playing.
            val personTalking = turnActive && sawSpeech
            if (state != Phase.SPEAKING && !personTalking) speak(hud.text)
        }
        if (HudState.Change.ERROR in changed) {
            turnRunning = false
            askWhy.drop()
            ui.onError(hud.lastError)
            main.postDelayed({ if (running && state != Phase.LISTENING) beginNextTurn() }, ERROR_LINGER_MS)
        }
        if (HudState.Change.HIGHLIGHT in changed) hud.highlight?.let(ui::onHighlight)
        publishReactor()
    }

    private fun raiseApproval(approval: HudState.Approval) {
        val link = client ?: return
        val session = link.sessionId
        // A sensitive ask (T-0945) is answered only behind the fingerprint, on the consent screen:
        // the overlay's in-place buttons would allow it with no signature.
        if (approval.needsPhone) {
            link.raisePhoneConfirm(context, approval) { hud.approvalAnswered(approval.requestId) }
            return
        }
        // The floating overlay answers it in place when it can; the consent door otherwise.
        val inline = ui.onApproval(approval) { choice ->
            hud.approvalAnswered(approval.requestId)
            link.respondApproval(approval.requestId, choice)
        }
        if (inline) return
        ApprovalBridge.raiseHermesRequest(
            app = context,
            requestId = approval.requestId,
            tool = approval.tool,
            params = approval.params,
            reason = approval.reason,
            choices = approval.choices,
            waitsSeconds = ApprovalBridge.TIMEOUT_MS / 1000L,
            why = {
                // Bound to the session the ask came on: the line means nothing in another one.
                val line = AskWhy.line(ApprovalCard.parse(approval.reason)?.what ?: approval.tool)
                if (client?.sessionId == session && linkUp) {
                    askWhy.submit(line, session, turnRunning, ::sendAskWhy)
                }
            },
        ) { choice ->
            hud.approvalAnswered(approval.requestId)
            link.respondApproval(approval.requestId, choice)
        }
    }

    /**
     * The one `prompt.submit` of an "Ask why" (T-1054). Sent as the person's own line, with no
     * screen context and no speaker ticket, and dropped when the link is not up.
     */
    private fun sendAskWhy(line: String) {
        val link = client ?: return
        if (!running || !linkUp || link.sessionId == null) return
        startedTurn()
        link.submitPrompt(line) { reply ->
            if (reply is HermesRpc.Reply.Error) turnRunning = false
        }
    }

    private fun askClarify(clarify: HudState.Clarify) {
        val link = client ?: return
        ui.onResponse(clarify.question)
        val shown = ClarifyPrompt.show(context, clarify.question, clarify.options) { answer ->
            hud.clarifyAnswered(clarify.requestId)
            link.respondClarify(clarify.requestId, answer)
        }
        if (!shown) Log.w(TAG, "could not show the clarifying question here; it stays pending")
    }

    override fun onDisconnected(reason: String, willRetry: Boolean) {
        turnRunning = false
        askWhy.drop()
        if (!running) return
        linkUp = false
        if (willRetry) {
            mode(Phase.THINKING, "Reconnecting")
        } else {
            ui.onError(reason)
            main.postDelayed({ if (running) end() }, ERROR_LINGER_MS)
        }
    }

    override fun onNotice(text: String) {
        ui.onResponse(text)
    }

    override fun onPrefill(text: String) {
        ui.onTranscript(text)
    }

    override fun onError(message: String) {
        ui.onError(message)
    }

    companion object {
        /** The personas plugin's slash command: `persona <Name>` switches who answers. */
        private const val PERSONA_COMMAND = "persona"
        private const val TAG = "JarvisHermesTurn"

        /** Same cap as the pipeline engine's, for the same Whisper-window reason. */
        private const val MAX_TURN_MS = 12_000L
        private const val MAX_TURN_BYTES = 16_000 * 2 * 14
        /** Kept from before the start edge latched, so the first word survives the debounce. */
        private const val PRE_ROLL_BYTES = 16_000 * 2 * 3 / 5

        private const val ERROR_LINGER_MS = 2_000L
        /** Empty turns in a row that end the conversation (`emptyTurns`). */
        internal const val MAX_EMPTY_TURNS = 3
        private const val HANDSHAKE_MS = 10_000L
        private const val STATUS_CHARS = 40
        /** The `hud_say` tool's event: one line to say, `{text, voice}`; the console reads the same. */
        const val SAY_EVENT = "hud.say"

        const val DIDNT_CATCH = "I didn't catch that."

        /** The console's own line for a voice the house did not recognise (`voice.svelte.ts` NOT_RECOGNISED). */
        const val NOT_RECOGNISED = "I don't recognise that voice."

        /** The `hud` group of the settings contract, read after every session attach. */
        val HUD_SETTING_KEYS: List<String> = listOf(
            "hud.layouts", "hud.mode", "hud.lock_layout", "hud.reduced_motion", "hud.reduced_transparency", "hud.sync_layouts",
        )

        private const val NOTHING_HEARD =
            "I did not hear anything. Try speaking a little closer to the phone."
        private val DEAD_MIC: String get() =
            "The microphone produced no sound at all. On GrapheneOS check " +
                "Settings → Apps → ${ActiveTheme.appLabel} → Sensors, which is separate from the " +
                "Microphone permission, then make sure nothing else is holding the mic."
        private const val NO_SESSION =
            "%s did not open a Hermes session. Check that this is the jarvis-adapter's address " +
                "and that the key is valid."
    }
}
