package ai.aura.app.car.voice

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.assist.hermes.HermesClient
import ai.aura.app.assist.hermes.HermesRpc
import ai.aura.app.assist.hermes.HudState
import ai.aura.app.config.JarvisConfig
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject

/**
 * Jarvis over the adapter's HUD socket, for the car (ADR-0047): the same
 * `prompt.submit`, the same events and the same approvals the phone and the console
 * use, on a session of the car's own. It reaches the adapter over mobile data or
 * WireGuard -- the car's Wi-Fi carries only the car's socket (SocketRoute).
 *
 * Does not share the phone's live conversation object; both are sessions of one
 * Hermes, so the console shows the car's turns beside the phone's.
 */
class HermesCarJarvis(
    private val context: android.content.Context,
    config: JarvisConfig,
    private val scope: CoroutineScope,
    private val onStep: (String) -> Unit = {},
    newClient: (HermesClient.Listener) -> HermesClient = { HermesClient(config.hermesUrl, config.hermesKey, it) },
) : CarJarvis, HermesClient.Listener {

    private val client = newClient(this)
    private val personaOf: () -> String = { config.persona }
    private val hud = HudState()
    /** Completed on each attach; a dropped link swaps in a fresh one, so [ask] waits out a redial (AUDIT B5). */
    @Volatile private var session = CompletableDeferred<Unit>()

    @Volatile private var connecting = false
    @Volatile private var pending: CompletableDeferred<String?>? = null
    @Volatile private var onApproval: (suspend (HudState.Approval) -> Boolean)? = null

    /**
     * What Jarvis heard in [wav], with its speaker ticket; [HermesClient.Transcribed.Offline] only when
     * the network failed, and a [HermesClient.Transcribed.Failed] with its reason for anything else
     * (an exception here used to read as offline, T-1079).
     */
    fun transcribe(wav: ByteArray): HermesClient.Transcribed {
        val outcome = try {
            client.transcribe(wav, HermesClient.SURFACE_CAR)
        } catch (e: Exception) {
            android.util.Log.w("HermesCarJarvis", "transcribe threw ${e.javaClass.simpleName}")
            return HermesClient.Transcribed.Failed("the transcribe call threw ${e.javaClass.simpleName}")
        }
        val heard = (outcome as? HermesClient.Transcribed.Got)?.heard
        // Refused in the car (six of six on 2026-10-07): the tray offers "That was me".
        if (heard != null && heard.dropped) ai.aura.app.assist.VoiceClaim.offer(context, heard.claim, "in the car")
        return outcome
    }

    /** The adapter answers `/api/status` with the paired key. Blocking: call it off the main thread. */
    fun reachable(): Boolean = client.status() != null

    override suspend fun ask(text: String, ticket: String?, voice: Boolean, onApproval: suspend (HudState.Approval) -> Boolean): String? {
        if (!connecting) {
            connecting = true
            client.connect()
        }
        withTimeoutOrNull(CONNECT_MS) { session.await() } ?: run {
            onStep("car voice: no ${ActiveTheme.name} session within ${CONNECT_MS / 1000} s")
            return null
        }
        val answer = CompletableDeferred<String?>()
        pending = answer
        this.onApproval = onApproval
        // Where the car is, so "the nearest coffee" has a nearest (`place_search`).
        val prompt = ai.aura.app.car.dash.CarHud.whereNow(context)?.let { "$text\n\n$it" } ?: text
        client.submitPrompt(prompt, ticket, voice) { reply ->
            // A drop (DISCONNECTED) is onDisconnected's to answer: it waits for the resent turn.
            if (reply is HermesRpc.Reply.Error && reply.code != HermesRpc.DISCONNECTED) {
                onStep("car voice: ${ActiveTheme.name} refused the prompt: ${reply.message}")
                answer.complete(null)
            }
        }
        return try {
            withTimeoutOrNull(TURN_MS) { answer.await() }
        } finally {
            pending = null
            this.onApproval = null
        }
    }

    override fun interrupt() {
        runCatching { client.interrupt() }
        pending?.complete(null)
    }

    /**
     * In the primary persona's own voice: the session's `persona.switch`, else the phone's
     * last-known one. An empty voice was the server's default, so the car spoke as JARVIS
     * whoever was chosen (the owner, 2026-09-27: "it isnt using the right voice").
     * At the guidance channel's 16 kHz, not the voice's 24: a third fewer bytes over the
     * phone's link, which ran near real time when the car's speech cut out (T-0917, T-0920).
     */
    override fun speak(text: String, onStart: (Int) -> Unit, onPiece: (ByteArray) -> Unit): Boolean =
        runCatching { client.speakStream(text, voice(), onStart, onPiece, CAR_SPEECH_HZ) }.getOrDefault(false)

    /** The voice [speak] uses now; the car's cached lines are kept under it. */
    fun voice(): String = synchronized(hud) { hud.persona }.ifEmpty { personaOf() }

    fun close() {
        runCatching { client.close() }
        connecting = false
        pending?.complete(null)
    }

    // --- the socket's events --------------------------------------------------------

    override fun onSession(sessionId: String, resumed: Boolean) {
        session.complete(Unit)
        client.fetchRoutines { json -> json?.let { ai.aura.app.routines.RoutineWatcher.sync(context, it) } }
        // Same key as the phone's own conversation: a car turn that raises a sensitive ask has it signed on the phone.
        client.registerSigningKey(context)
    }

    /** A `once` routine this link ran is spent on the server too. */
    fun routineDone(id: String) {
        runCatching { client.routineDone(id) }
    }

    override fun onEvent(type: String, sessionId: String, payload: JSONObject) {
        if (type == ai.aura.app.assist.PhoneMedia.EVENT) {
            android.os.Handler(android.os.Looper.getMainLooper()).post { ai.aura.app.assist.PhoneMedia.handle(context, payload) }
            return
        }
        if (type == ai.aura.app.assist.PhoneAction.EVENT) {
            ai.aura.app.assist.PhoneAction.handle(context, payload) { id, body -> client.answerPhone(id, body) }
            return
        }
        if (type == ai.aura.app.routines.RoutineWatcher.SYNC_EVENT) {
            ai.aura.app.routines.RoutineWatcher.sync(context, payload)
            return
        }
        if (type == ai.aura.app.assist.hermes.HermesConversation.SAY_EVENT) {
            // `hud_say` in a car turn: the car's own voice (its guidance channel), which needs no
            // screen share; dropped here, it was heard only through the car page's web view (T-0743).
            val line = payload.optString("text").trim()
            if (line.isNotEmpty()) {
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    ai.aura.app.car.dash.CarShell.onSay?.invoke(line)
                }
            }
            return
        }
        if (type == NAVIGATE_EVENT) {
            // Jarvis chose a destination (`car_navigate`): a route, or a new one.
            android.os.Handler(android.os.Looper.getMainLooper()).post {
                ai.aura.app.car.dash.CarHud.route(
                    context,
                    ai.aura.app.car.dash.CarShell.active(),
                    payload.optString("app"),
                    payload.optDouble("lat"),
                    payload.optDouble("lon"),
                    payload.optString("label"),
                )
            }
            return
        }
        val changed = synchronized(hud) { hud.apply(type, payload) }
        if (HudState.Change.APPROVAL in changed && type == "approval.request") {
            val approval = synchronized(hud) { hud.approvals.lastOrNull { it.requestId == payload.optString("request_id") } }
            val ask = onApproval
            if (approval != null && approval.needsPhone) {
                // A sensitive ask (T-0945) is signed behind the fingerprint on the phone, so the car
                // never hears a yes to it: say where to confirm, hand it to the phone's consent screen
                // and send nothing from here.
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    ai.aura.app.car.dash.CarShell.onSay?.invoke(CONFIRM_ON_PHONE)
                }
                client.raisePhoneConfirm(context, approval)
            } else if (approval != null) {
                scope.launch {
                    val ok = ask?.invoke(approval) ?: false
                    client.respondApproval(approval.requestId, choiceFor(approval, ok))
                }
            }
        }
        if (type == "approval.resolved" || type == "approval.response") {
            // Answered elsewhere or timed out: the phone's card, consent screen and notification come down.
            ai.aura.app.ui.ApprovalBridge.dismissResolved(payload.optString("request_id"))
        }
        if (HudState.Change.COMPLETE in changed) pending?.complete(synchronized(hud) { hud.text })
        if (HudState.Change.ERROR in changed) pending?.complete(null)
    }

    override fun onDisconnected(reason: String, willRetry: Boolean) {
        onStep("car voice: ${ActiveTheme.name} link down ($reason)")
        // Only a link that will redial is waited for; one that gave up answers null at once, as before.
        if (willRetry && session.isCompleted) session = CompletableDeferred()
        val waiting = pending ?: return
        if (!willRetry) {
            client.forgetTurn()
            waiting.complete(null)
            return
        }
        // A blip: the client resends the turn on resume (or the server is still running it), so
        // wait CONNECT_MS for that answer. Past it the driver hears the offline line and the turn
        // is forgotten, so it never runs behind them with nobody to hear the reply (T-0559).
        val back = session
        scope.launch {
            if (withTimeoutOrNull(CONNECT_MS) { back.await() } == null && waiting.complete(null)) client.forgetTurn()
        }
    }

    override fun onNotice(text: String) = Unit

    override fun onPrefill(text: String) = Unit

    override fun onError(message: String) {
        onStep("car voice: $message")
    }

    companion object {
        /** `aura-hud/places.py` `NAVIGATE_EVENT`; docs/hermes-hud-protocol.md "The car". */
        const val NAVIGATE_EVENT = "hud.car.navigate"

        /** The car's guidance channel's rate (its bring-up line: "GUIDANCE(3) at 16000 Hz"). */
        const val CAR_SPEECH_HZ = 16_000
        /** The fixed line for a sensitive ask: the car never allows one (T-0945). */
        const val CONFIRM_ON_PHONE = "That needs your fingerprint. Confirm it on your phone."
        private const val CONNECT_MS = 10_000L
        private const val TURN_MS = 120_000L

        /** Yes is the first choice that is not a refusal; no is the refusal, else the last choice. */
        fun choiceFor(approval: HudState.Approval, approved: Boolean): String {
            val deny = approval.choices.firstOrNull { it.equals("deny", ignoreCase = true) } ?: approval.choices.lastOrNull() ?: "deny"
            return if (approved) approval.choices.firstOrNull { !it.equals("deny", ignoreCase = true) } ?: deny else deny
        }
    }
}
