package ai.aura.app.assist.hermes

import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.tailnet.JarvisHttp
import ai.aura.app.channel.Backoff
import ai.aura.app.config.ServerEndpoint
import ai.aura.app.config.ServerKind
import ai.aura.app.config.ServerUrl
import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * The HUD socket to the jarvis-adapter, and the adapter's REST beside it.
 *
 * One WebSocket to `<base>/ws` with the bearer key on the upgrade, speaking
 * JSON-RPC 2.0 ([HermesFrames], [HermesRpc]); reconnecting through
 * [Backoff] when it drops, re-attaching to the same session with
 * `session.resume`; and the three HTTP calls the voice turn needs —
 * transcribe, speak, status — on the same origin with the same key.
 *
 * `docs/hermes-hud-protocol.md` is the contract. Two rules from it live here:
 * the first frame after connect is `gateway.ready`; and a `command.dispatch`
 * result of type `send` MUST be followed by `prompt.submit` of its message,
 * which [dispatchCommand] does without asking.
 *
 * Every [Listener] call is delivered through [Clockwork] — the main thread on
 * a phone — so a surface never hears from OkHttp's reader thread. The socket
 * factory and the clock are seams so a JVM test can drive the whole client
 * with a fake socket and a manual clock; the defaults are OkHttp and the main
 * looper.
 */
class HermesClient(
    private val serverUrl: String,
    private val token: String,
    private val listener: Listener,
    private val clockwork: Clockwork = MainClockwork(),
    private val socketFactory: ((Request, WebSocketListener) -> WebSocket)? = null,
    private val backoff: Backoff = Backoff(baseMs = RECONNECT_BASE_MS, maxMs = RECONNECT_MAX_MS),
    private val random: () -> Double = { Math.random() },
    private val now: () -> Long = System::currentTimeMillis,
) : WebSocketListener() {

    interface Listener {
        /** A session is attached: created, or resumed with history replayed. */
        fun onSession(sessionId: String, resumed: Boolean)

        /** Every event the server sent, after the client's own bookkeeping. */
        fun onEvent(type: String, sessionId: String, payload: JSONObject)

        /** The socket went away. [willRetry] false only after [close] or a rejected key. */
        fun onDisconnected(reason: String, willRetry: Boolean)

        /** A notice or output from a dispatched command, to show. */
        fun onNotice(text: String)

        /** A `prefill` result: put this in the composer, do not send. */
        fun onPrefill(text: String)

        /** Something failed, in a sentence. */
        fun onError(message: String)
    }

    private val http: OkHttpClient by lazy {
        JarvisHttp.builder()
            .pingInterval(20, TimeUnit.SECONDS)
            // A voice socket idles for minutes; the audio calls wait on a
            // model that may be cold-starting.
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .connectTimeout(10, TimeUnit.SECONDS)
            .followRedirects(false)
            .followSslRedirects(false)
            .build()
    }

    private val rpc = HermesRpc { text -> ws?.send(text) ?: false }
    private var ws: WebSocket? = null

    /** The live session this client talks on: every request and event carries it. */
    @Volatile
    var sessionId: String? = null
        private set

    /**
     * The same session's stored id, the one `session.resume` takes and so the one to
     * remember. Hermes answers a resume by the live id with 4007 "session not found"
     * (probed 2026-10-07), so remembering the live id made every phone conversation,
     * and every reconnect, a new session with none of the last one's context.
     */
    @Volatile
    var storedSessionId: String? = null
        private set

    /** True between [connect] and [close]. Reconnects happen only while true. */
    @Volatile
    private var wanted = false

    /** True once any frame arrived on the current socket; [ready] is the one that waits for `gateway.ready`. */
    @Volatile
    var connected = false
        private set

    private var attempts = 0
    private val reconnect = Runnable { if (wanted) dial() }

    /** True once `gateway.ready` arrived on the current socket; any other frame does not count. */
    @Volatile
    private var ready = false

    /**
     * The deadline on the current socket's `gateway.ready`. One per dial, bound to its socket:
     * a deadline left from an older socket finds [ws] is not its own and does nothing. It was
     * once cancelled by any frame, so a socket that carried a pending approval and then no
     * ready (2026-11-12 drive) sat open to the end, every later turn failing.
     */
    private var handshakeWatchdog: Runnable? = null

    private fun armHandshakeWatchdog(socket: WebSocket) {
        handshakeWatchdog?.let { clockwork.remove(it) }
        val deadline = Runnable {
            if (wanted && !ready && ws === socket) {
                Log.w(TAG, "no gateway.ready in ${HANDSHAKE_TIMEOUT_MS / 1000} s; reconnecting")
                try { socket.cancel() } catch (_: Exception) {}
                onGone("the adapter did not answer", retry = true)
            }
        }
        handshakeWatchdog = deadline
        clockwork.postDelayed(deadline, HANDSHAKE_TIMEOUT_MS)
    }

    private fun disarmHandshakeWatchdog() {
        handshakeWatchdog?.let { clockwork.remove(it) }
        handshakeWatchdog = null
    }

    /** Open the socket and attach to [resume] if given, else create a session. */
    fun connect(resume: String? = null) {
        if (wanted) return
        wanted = true
        sessionId = null
        storedSessionId = resume?.takeIf { it.isNotBlank() }
        attempts = 0
        dial()
    }

    fun close() {
        wanted = false
        clockwork.remove(reconnect)
        disarmHandshakeWatchdog()
        ready = false
        val socket = ws
        ws = null
        connected = false
        try { socket?.close(1000, null) } catch (_: Exception) {}
        rpc.failAll("closed")
    }

    private fun dial() {
        val url = ServerEndpoint.websocketUrl(serverUrl, ServerKind.HERMES) ?: run {
            deliver { listener.onError("That server address is not usable: $serverUrl") }
            wanted = false
            return
        }
        connected = false
        ready = false
        Log.i(TAG, "connecting to the adapter (attempt $attempts)")
        ws = try {
            val factory = socketFactory
            if (factory != null) {
                factory(upgradeRequest(url), this)
            } else {
                // Written out at the call rather than through [upgradeRequest],
                // because `tools/websocket_auth_test.py` reads the header off
                // every `newWebSocket(` call site in the app — the rule that
                // caught the companion client opening a socket with no token.
                http.newWebSocket(
                    Request.Builder()
                        .url(url)
                        // The adapter authenticates the UPGRADE: no header, no frames.
                        .header("Authorization", "Bearer $token")
                        .build(),
                    this,
                )
            }
        } catch (t: Throwable) {
            Log.w(TAG, "could not open the socket", t)
            onGone("could not open the socket: ${t.javaClass.simpleName}", retry = true)
            return
        }
        ws?.let { armHandshakeWatchdog(it) }
    }

    /** The upgrade a test's fake socket sees: the same URL and header as the real dial. */
    private fun upgradeRequest(url: String): Request = Request.Builder()
        .url(url)
        .header("Authorization", "Bearer $token")
        .build()

    // --- requests ------------------------------------------------------------

    /** One request, its reply on the main thread. */
    fun request(method: String, params: JSONObject = JSONObject(), onReply: (HermesRpc.Reply) -> Unit = {}): Int =
        rpc.request(method, params) { reply -> deliver { onReply(reply) } }

    /** Session-scoped params: `{session_id, …}`. */
    private fun sessionParams(vararg fields: Pair<String, Any?>): JSONObject {
        val params = JSONObject().put("session_id", sessionId ?: "")
        for ((key, value) in fields) if (value != null) params.put(key, value)
        return params
    }

    /** Drop the turn a resume would resend: its caller has already been answered (the car's offline line). */
    fun forgetTurn() = clockwork.post { inflight = null }

    /**
     * `prompt.submit`: start a turn. A `speakerTicket` from [transcribeHeard]
     * rides along for a spoken turn; the adapter binds the session to the
     * person it names and strips it before Hermes sees the frame. A typed
     * turn has none and is the key holder's. `surface` says what this phone
     * can show ([SURFACE]); the adapter keeps it per session and strips it
     * too, so a video asked for here is shown here and not on the desktop.
     * `voice` is true for a turn the person spoke, ticket or not (the gate may be
     * off or have refused the voice): the adapter runs it at the voice reasoning
     * effort and strips the flag (`tests/contracts/voice-flag.json`). A typed turn omits it.
     */
    fun submitPrompt(text: String, speakerTicket: String? = null, voice: Boolean = false, onReply: (HermesRpc.Reply) -> Unit = {}): Int {
        val turn = Inflight(text, speakerTicket, now(), voice = voice)
        inflight = turn
        return send(text, speakerTicket, voice) { reply ->
            // Never left the phone and the caller was told: a resume must not send it behind them (AUDIT B5).
            if (reply is HermesRpc.Reply.Error && reply.code == HermesRpc.NOT_CONNECTED && inflight === turn) inflight = null
            onReply(reply)
        }
    }

    private fun send(text: String, speakerTicket: String?, voice: Boolean, onReply: (HermesRpc.Reply) -> Unit = {}): Int =
        request(
            "prompt.submit",
            sessionParams(
                *listOfNotNull(
                    "text" to text,
                    "surface" to JSONObject(SURFACE),
                    if (speakerTicket.isNullOrBlank()) null else "speaker_ticket" to speakerTicket,
                    if (voice) "voice" to true else null,
                ).toTypedArray(),
            ),
            onReply,
        )

    /** `session.interrupt`: stop the running turn (cooperative). */
    fun interrupt(onReply: (HermesRpc.Reply) -> Unit = {}): Int =
        request("session.interrupt", sessionParams(), onReply)

    /** `session.steer`: guidance at the next tool boundary. */
    fun steer(text: String, onReply: (HermesRpc.Reply) -> Unit = {}): Int =
        request("session.steer", sessionParams("text" to text), onReply)

    /**
     * `approval.respond` with one of the offered choices; [signature] is the base64 ECDSA proof
     * the adapter requires to forward an allow of a `confirm: "phone"` ask (T-0945).
     */
    fun respondApproval(
        requestId: String,
        choice: String,
        signature: String? = null,
        onReply: (HermesRpc.Reply) -> Unit = {},
    ): Int {
        val params = sessionParams("request_id" to requestId, "choice" to choice)
        if (signature != null) params.put(ai.aura.app.ui.ApprovalProof.SIGNATURE_FIELD, signature)
        return request("approval.respond", params, onReply)
    }

    /**
     * Raise a phone-confirmed (`confirm: "phone"`) ask on the consent screen only, where the
     * fingerprint is asked for and the allow is signed (T-0945). The phone and the car both come
     * here, so neither can answer such an ask inline with a plain yes. A reply that refuses the
     * signature (error [SIGNATURE_REFUSED]) leaves the ask open on the adapter, so the screen goes
     * up again with a note; each round needs the person's fingerprint, so this cannot spin.
     * [onSent] runs when an answer leaves the phone. These asks may come from the adapter itself
     * with no session id: nothing here depends on one.
     */
    fun raisePhoneConfirm(
        app: android.content.Context,
        approval: HudState.Approval,
        note: String = "",
        onSent: () -> Unit = {},
    ): Boolean = ai.aura.app.ui.ApprovalBridge.raiseHermesRequest(
        app = app,
        requestId = approval.requestId,
        tool = approval.tool,
        params = approval.params,
        reason = approval.reason,
        choices = approval.choices,
        waitsSeconds = ai.aura.app.ui.ApprovalBridge.TIMEOUT_MS / 1000L,
        challenge = approval.challenge,
        signedSend = { choice, signature ->
            onSent()
            respondApproval(approval.requestId, choice, signature) { reply ->
                if (reply is HermesRpc.Reply.Error && reply.code == SIGNATURE_REFUSED) {
                    raisePhoneConfirm(app, approval, ai.aura.app.ui.ApprovalProof.REFUSED_NOTE, onSent)
                }
            }
        },
        note = note,
    ) { choice ->
        onSent()
        respondApproval(approval.requestId, choice)
    }

    /**
     * Register the phone's signing key with the adapter, off the calling thread: once per adapter
     * and key ([ai.aura.app.ui.ApprovalProof.register]). A 409 or 403 is told to the person
     * through [Listener.onNotice], once. Called on every session, so a first pairing, a new
     * adapter and an adapter that was down all converge without a screen for it.
     */
    fun registerSigningKey(app: android.content.Context) {
        val prefs = app.getSharedPreferences("approval-key", android.content.Context.MODE_PRIVATE)
        Thread({
            val notice = try {
                ai.aura.app.ui.ApprovalProof.register(prefs, serverUrl) { registerApprovalKey(it) }
            } catch (t: Throwable) {
                Log.w(TAG, "approval key registration failed", t)
                null
            }
            if (notice != null) deliver { listener.onNotice(notice) }
        }, "approval-key").start()
    }

    /**
     * `POST /api/approval/key`: the phone's signing key, for the adapter to check sensitive
     * approvals against. The HTTP status, or -1 when the adapter could not be reached.
     */
    fun registerApprovalKey(publicKey: String): Int {
        val body = JSONObject().put(ai.aura.app.ui.ApprovalProof.REGISTER_FIELD, publicKey)
        val request = Request.Builder()
            .url(ServerUrl.normalize(serverUrl) + ai.aura.app.ui.ApprovalProof.REGISTER_PATH)
            .header("Authorization", "Bearer $token")
            .post(body.toString().toRequestBody(JSON))
            .build()
        return try {
            http.newBuilder().readTimeout(STATUS_TIMEOUT_S, TimeUnit.SECONDS).build()
                .newCall(request).execute().use { it.code }
        } catch (t: Throwable) {
            Log.i(TAG, "approval key registration failed: ${t.javaClass.simpleName}")
            -1
        }
    }

    /** `clarify.respond`. */
    fun respondClarify(requestId: String, answer: String, onReply: (HermesRpc.Reply) -> Unit = {}): Int =
        request("clarify.respond", sessionParams("request_id" to requestId, "answer" to answer), onReply)

    /**
     * The phone's answer to a `phone_action` (`/hud @<sid> phone-result <id> <json>`),
     * sent quietly: the plugin's "ok" is for the waiting tool call, not a notice.
     */
    fun answerPhone(id: String, body: JSONObject): Int =
        request("command.dispatch", sessionParams("name" to "hud", "arg" to "@${sessionId.orEmpty()} phone-result $id $body"))

    /**
     * The routines Jarvis saved (`/hud @<sid> routines`, `aura-hud/routines.py`), as the
     * plugin's JSON snapshot; null when the command failed. Pulled on every connect so a
     * routine made at the console reaches the phone that watches it.
     */
    fun fetchRoutines(onJson: (JSONObject?) -> Unit): Int =
        request("command.dispatch", sessionParams("name" to "hud", "arg" to "@${sessionId.orEmpty()} routines")) { reply ->
            val out = (reply as? HermesRpc.Reply.Ok)?.result?.optString("output").orEmpty()
            onJson(runCatching { JSONObject(out) }.getOrNull())
        }

    /** A `once` routine ran: the plugin forgets it (`/hud @<sid> routines done <id>`). */
    fun routineDone(id: String): Int =
        request("command.dispatch", sessionParams("name" to "hud", "arg" to "@${sessionId.orEmpty()} routines done $id"))

    /**
     * `command.dispatch` without the leading `/`, and then whatever its result
     * type says to do: a `send` shows its notice and submits its message as a
     * prompt; an `exec` shows its output; a `prefill` fills the composer; an
     * error is shown; a `plugin` result is output like an `exec`. [onOutcome]
     * hears the interpreted result either way.
     */
    fun dispatchCommand(name: String, arg: String, onOutcome: (HermesDispatch.Outcome) -> Unit = {}): Int =
        request(
            "command.dispatch",
            // A plugin command's handler is not told the session: it goes in the arg.
            sessionParams("name" to name, "arg" to HermesDispatch.argFor(name, arg, sessionId)),
        ) { reply ->
            val outcome = when (reply) {
                is HermesRpc.Reply.Ok -> HermesDispatch.interpret(reply.result)
                is HermesRpc.Reply.Error -> HermesDispatch.interpretError(reply.code, reply.message, name)
            }
            when (outcome) {
                is HermesDispatch.Outcome.Send -> {
                    if (outcome.notice.isNotEmpty()) listener.onNotice(outcome.notice)
                    if (outcome.message.isNotEmpty()) submitPrompt(outcome.message)
                }
                is HermesDispatch.Outcome.Exec -> if (outcome.output.isNotEmpty()) listener.onNotice(outcome.output)
                is HermesDispatch.Outcome.Prefill -> listener.onPrefill(outcome.message)
                is HermesDispatch.Outcome.Error -> listener.onError(outcome.message)
            }
            onOutcome(outcome)
        }

    /**
     * `command.dispatch hud @<sid> list` for a late joiner: the panels the
     * plugin is holding, handed back parsed ([HudPanels.parsePanelList]).
     *
     * Unlike [dispatchCommand], the output is NOT shown as a notice — a JSON
     * list of panels is state to seed the surface with, not a sentence for the
     * reply view. Issued once after a session attaches ([Listener.onSession]).
     */
    fun listPanels(onList: (JSONArray) -> Unit): Int {
        val sid = sessionId
        if (sid.isNullOrEmpty()) {
            deliver { onList(JSONArray()) }
            return -1
        }
        return request(
            "command.dispatch",
            JSONObject().put("session_id", sid).put("name", HUD_COMMAND).put("arg", "@$sid list"),
        ) { reply ->
            val rows = when (reply) {
                is HermesRpc.Reply.Ok -> HudPanels.parsePanelList(reply.result.opt("output"))
                is HermesRpc.Reply.Error -> JSONArray()
            }
            onList(rows)
        }
    }

    /**
     * `command.dispatch hud @<sid> dismiss <id>` — the phone asking a panel be
     * taken down (`*` clears the surface). The surface removes it optimistically;
     * the plugin's own `hud.panel.dismiss` reconciles.
     */
    fun dismissPanel(id: String, onReply: (HermesRpc.Reply) -> Unit = {}): Int {
        val sid = sessionId ?: return -1
        return request(
            "command.dispatch",
            JSONObject().put("session_id", sid).put("name", HUD_COMMAND).put("arg", "@$sid dismiss $id"),
            onReply,
        )
    }

    /**
     * `command.dispatch hud_layout @<sid> user <surface> <activity> <json>` —
     * the person's own arrangement, reported back so it is saved under
     * `hud.layouts[surface][activity]` and JARVIS stops rearranging over it
     * (GLASS_HUD §9.8; `tests/contracts/hud-layout.json` `command.user`).
     * The plugin finds the layout at the first `{`, so the JSON goes last.
     * The reply is not shown: a saved layout is not a sentence for the reply view.
     */
    fun reportLayout(surface: String, activity: String, layoutJson: String, onReply: (HermesRpc.Reply) -> Unit = {}): Int {
        val sid = sessionId ?: return -1
        return request(
            "command.dispatch",
            JSONObject().put("session_id", sid).put("name", LAYOUT_COMMAND).put("arg", "@$sid user $surface $activity $layoutJson"),
            onReply,
        )
    }

    /**
     * `command.dispatch jsettings @<sid> get <key>`: one of Hermes's own
     * settings through the `aura-settings` plugin, answered as a `plugin`
     * result whose `output` is a JSON string `{key, value, source}`. Not
     * `config.get`, which knows only prompt/personality/display keys. The
     * value arrives parsed, or null when the plugin refused the key or is not
     * installed — a HUD without saved layouts, not a HUD that never starts.
     */
    fun settingsGet(key: String, onValue: (Any?) -> Unit): Int {
        val sid = sessionId
        if (sid.isNullOrEmpty()) {
            deliver { onValue(null) }
            return -1
        }
        return request(
            "command.dispatch",
            JSONObject().put("session_id", sid).put("name", SETTINGS_COMMAND).put("arg", "@$sid get $key"),
        ) { reply ->
            val value = when (reply) {
                is HermesRpc.Reply.Ok -> settingsValue(reply.result.opt("output"))
                is HermesRpc.Reply.Error -> null
            }
            onValue(value)
        }
    }

    /** `command.dispatch jsettings @<sid> set <key> <json>`; the value is JSON so a bool stays a bool. */
    fun settingsSet(key: String, valueJson: String, onReply: (HermesRpc.Reply) -> Unit = {}): Int {
        val sid = sessionId ?: return -1
        return request(
            "command.dispatch",
            JSONObject().put("session_id", sid).put("name", SETTINGS_COMMAND).put("arg", "@$sid set $key $valueJson"),
            onReply,
        )
    }

    /**
     * The prompt whose turn has not finished, kept so a turn lost to a restart is not lost to
     * the person. Hermes closes a turn reaped mid-run with [FAILED_TURN_NOTICE] ("Send it
     * again"); on 2026-10-06 four requests in a minute ended that way. One resend, and only
     * when no tool ran: a turn that had texted or unlocked something is not redone.
     */
    private class Inflight(val text: String, val ticket: String?, val at: Long, var sawTool: Boolean = false, var resent: Boolean = false, val voice: Boolean = false)

    @Volatile private var inflight: Inflight? = null

    /** Pure: whether `session.resume`'s messages show a tool row after the last user row. */
    internal fun toolRanAfterLastPrompt(messages: org.json.JSONArray?): Boolean {
        if (messages == null) return false
        for (i in messages.length() - 1 downTo 0) {
            when (messages.optJSONObject(i)?.optString("role")) {
                "user" -> return false
                "tool" -> return true
            }
        }
        return false
    }

    /** Sends [inflight] again if it may be; true when it did. */
    private fun resendOnce(why: String): Boolean {
        val turn = inflight ?: return false
        if (turn.resent || turn.sawTool || now() - turn.at > RESEND_WINDOW_MS) return false
        turn.resent = true
        Log.i(TAG, "resending a turn that $why")
        send(turn.text, turn.ticket, turn.voice)
        return true
    }

    /** Bookkeeping on each event; false when the event is the dropped turn's notice, which is not passed on. */
    private fun track(type: String, payload: JSONObject): Boolean {
        val turn = inflight ?: return true
        when (type) {
            "tool.start", "tool.progress", "approval.request" -> turn.sawTool = true
            "message.complete" -> {
                if (payload.optString("text").trim() == FAILED_TURN_NOTICE && resendOnce("Hermes dropped before answering")) return false
                inflight = null
            }
        }
        return true
    }

    // --- WebSocketListener ----------------------------------------------------

    override fun onOpen(webSocket: WebSocket, response: Response) {
        if (webSocket !== ws) return
        Log.i(TAG, "socket open; waiting for gateway.ready")
    }

    override fun onMessage(webSocket: WebSocket, text: String) {
        if (webSocket !== ws) return
        connected = true
        when (val frame = rpc.receive(text)) {
            is HermesFrames.Frame.Event -> {
                if (frame.type == GATEWAY_READY) {
                    // Only a ready ends the deadline and the backoff: a socket that talks but
                    // never readies must not reset the delay it is retried on.
                    ready = true
                    disarmHandshakeWatchdog()
                    backoff.reset()
                    attempts = 0
                    attach()
                } else if (track(frame.type, frame.payload)) {
                    deliver { listener.onEvent(frame.type, frame.sessionId, frame.payload) }
                }
            }
            is HermesFrames.Frame.Unknown -> Log.d(TAG, "ignoring a frame: ${frame.reason}")
            else -> Unit // replies were matched inside rpc.receive
        }
    }

    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
        if (webSocket !== ws) return
        try { webSocket.close(code, reason) } catch (_: Exception) {}
    }

    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
        if (webSocket !== ws) return
        val badKey = code == CLOSE_BAD_KEY
        onGone(if (badKey) BAD_KEY else "the adapter closed the socket ($code)", retry = !badKey)
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        if (webSocket !== ws) return
        val code = response?.code
        Log.w(TAG, "socket failure (http $code)", t)
        val badKey = code == 401 || code == 403
        val origin = ServerUrl.originOf(serverUrl)?.toString() ?: serverUrl
        onGone(if (badKey) BAD_KEY else "can't reach ${ActiveTheme.name} at $origin", retry = !badKey)
    }

    /** The socket is gone: tell the waiting callers, the listener, and maybe the clock. */
    private fun onGone(reason: String, retry: Boolean) {
        disarmHandshakeWatchdog()
        ws = null
        connected = false
        ready = false
        rpc.failAll(reason)
        val again = retry && wanted
        // A rejected key does not fix itself in a second.
        if (!retry) backoff.penalise()
        deliver { listener.onDisconnected(reason, again) }
        if (again) {
            attempts++
            val delay = backoff.next(random())
            Log.i(TAG, "reconnecting in $delay ms")
            clockwork.remove(reconnect)
            clockwork.postDelayed(reconnect, delay)
        }
    }

    /** After `gateway.ready`: resume the remembered session, or create one. */
    private fun attach() {
        // A server that sends no stored id resumes by the live one: the live id then stands in.
        val remembered = storedSessionId ?: sessionId
        if (remembered != null) {
            rpc.request("session.resume", JSONObject().put("session_id", remembered)) { reply ->
                when (reply) {
                    is HermesRpc.Reply.Ok -> {
                        val live = reply.result.optString("session_id").ifEmpty { remembered }
                        sessionId = live
                        storedSessionId = reply.result.optString("stored_session_id").ifEmpty { storedSessionId }
                        deliver { listener.onSession(live, resumed = true) }
                        // A socket lost mid-turn: the turn died with it (ws_orphan_reap) -- unless
                        // the server says otherwise. The drop that killed the turn can also have
                        // eaten its tool.start, so what this client saw is not enough: a turn
                        // still running, or one whose history shows a tool after the prompt,
                        // may already have texted someone, and is not sent again.
                        if (reply.result.optBoolean("running") || toolRanAfterLastPrompt(reply.result.optJSONArray("messages"))) {
                            inflight?.sawTool = true
                        }
                        resendOnce("was in flight when the socket dropped")
                    }
                    is HermesRpc.Reply.Error -> {
                        Log.i(TAG, "could not resume $remembered (${reply.code}); creating a session")
                        create()
                    }
                }
            }
        } else {
            create()
        }
    }

    private fun create() {
        rpc.request("session.create", JSONObject()) { reply ->
            when (reply) {
                is HermesRpc.Reply.Ok -> {
                    val id = reply.result.optString("session_id")
                    if (id.isEmpty()) {
                        deliver { listener.onError("the adapter created a session with no id") }
                    } else {
                        sessionId = id
                        storedSessionId = reply.result.optString("stored_session_id").ifEmpty { null }
                        deliver { listener.onSession(id, resumed = false) }
                    }
                }
                is HermesRpc.Reply.Error ->
                    deliver { listener.onError("could not start a session: ${reply.message} (${reply.code})") }
            }
        }
    }

    private fun deliver(block: () -> Unit) = clockwork.post(block)

    // --- REST (blocking; call from a worker thread) --------------------------

    /**
     * `POST /api/audio/transcribe` with a WAV. What the transcriber heard, and who: the words, the speaker gate's
     * verdict (`speaker`, when the house had one -- `dropped` means the gate
     * refused the voice and the adapter emptied the transcript before any
     * prompt), and the `speaker_ticket` a recognised voice earns, which
     * [submitPrompt] hands back so the house credits the turn to that person
     * (2026-09-10). Null when the call failed.
     */
    fun transcribeHeard(wav: ByteArray, surface: String = SURFACE_PHONE): Heard? =
        (transcribe(wav, surface) as? Transcribed.Got)?.heard

    /** How a transcribe ended: words, a network that failed, or a failure that was not the network. */
    sealed interface Transcribed {
        data class Got(val heard: Heard) : Transcribed
        /** A refused connection, a timeout or a route that is gone: the only outcome "offline" may name. */
        data object Offline : Transcribed
        /** The call went out and still gave no words: an HTTP error, a body that is not a transcript, anything else. */
        data class Failed(val why: String) : Transcribed
    }

    /** [transcribeHeard] that says why there were no words; each failure is logged and reported once. */
    fun transcribe(wav: ByteArray, surface: String = SURFACE_PHONE): Transcribed {
        // Which microphone: the house keeps a voice's prints per microphone, and a voice
        // refused here can be claimed from the console as a print for this one (the
        // owner, 2026-10-07: enrolled on the console, refused on the phone and in the car).
        val path = "/api/audio/transcribe"
        val body = try {
            JSONObject()
                .put("data_url", WavAudio.dataUrl(wav, WavAudio.MIME))
                .put("mime_type", WavAudio.MIME)
                .put("surface", surface)
        } catch (e: Exception) {
            Log.w(TAG, "$path: the audio could not be packed: ${e.javaClass.simpleName}")
            return Transcribed.Failed("the audio could not be packed (${e.javaClass.simpleName})")
        }
        return when (val posted = posted(path, body, TRANSCRIBE_TIMEOUT_S)) {
            is Posted.Ok -> heardOf(posted.text)?.let { Transcribed.Got(it) } ?: run {
                Log.w(TAG, "$path answered 200 with no transcript")
                Transcribed.Failed("$path answered with something that is not a transcript")
            }
            is Posted.Refused -> Transcribed.Failed("$path answered ${posted.code}")
            Posted.Unreachable -> Transcribed.Offline
            is Posted.Broke -> Transcribed.Failed("$path failed (${posted.kind})")
        }
    }

    /**
     * One transcription: the words, whether the gate dropped them, who it was, the ticket,
     * and for a refused voice close to someone, the adapter's [claim] ("That was me").
     */
    data class Heard(
        val transcript: String,
        val dropped: Boolean,
        val label: String?,
        val ticket: String?,
        val claim: String? = null,
    )


    /**
     * `POST /api/audio/speak`: the reply's audio bytes (whatever codec the relay
     * chose), or null. `voice` names a persona's clip on the adapter; empty, the
     * primary's -- the same field the console sends (`hud_say` names one).
     */
    fun speak(text: String, voice: String = ""): ByteArray? {
        val body = JSONObject().put("text", text)
        if (voice.isNotEmpty()) body.put("voice", voice)
        val answer = post("/api/audio/speak", body, AUDIO_TIMEOUT_S) ?: return null
        return audioOf(answer)
    }

    /**
     * `POST /api/audio/speak-pcm`: the line as it is made, 16-bit mono PCM in
     * pieces, each handed to `onPiece` as it lands (`rate` from the reply's
     * `x-sample-rate`). This is the phone's first word: the whole-line
     * `speak` answers when the last sample exists, about six tenths of the
     * line's length after the request (console measurement, 2026-09-10).
     * False without a byte played -- 404 (a voice that does not stream), 503
     * (still warming), no network -- so the caller can take `speak` instead;
     * true once anything was handed out, whatever happened after, because a
     * line half heard is not one to say again.
     */
    fun speakStream(
        text: String,
        voice: String,
        onStart: (rate: Int) -> Unit,
        onPiece: (ByteArray) -> Unit,
        rateHz: Int = 0,
    ): Boolean {
        val body = speakPcmBody(text, voice, rateHz)
        val url = ServerUrl.normalize(serverUrl) + "/api/audio/speak-pcm"
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .post(body.toString().toRequestBody(JSON))
            .build()
        return try {
            http.newBuilder().readTimeout(AUDIO_TIMEOUT_S, TimeUnit.SECONDS).build()
                .newCall(request).execute().use { response ->
                    if (!response.isSuccessful) return false
                    val stream = response.body?.byteStream() ?: return false
                    onStart(response.header("x-sample-rate")?.toIntOrNull() ?: 24000)
                    val buf = ByteArray(PCM_PIECE_BYTES)
                    var handed = false
                    while (true) {
                        val n = stream.read(buf)
                        if (n < 0) break
                        if (n > 0) {
                            onPiece(buf.copyOf(n))
                            handed = true
                        }
                    }
                    handed
                }
        } catch (t: Throwable) {
            Log.w(TAG, "speak-pcm failed: ${t.javaClass.simpleName}")
            false
        }
    }

    /** `GET /api/status`, or null when it could not be read. */
    fun status(): HermesStatus? {
        val url = ServerUrl.normalize(serverUrl) + "/api/status"
        val request = Request.Builder().url(url).header("Authorization", "Bearer $token").build()
        return try {
            http.newBuilder().readTimeout(STATUS_TIMEOUT_S, TimeUnit.SECONDS).build()
                .newCall(request).execute().use { response ->
                    if (!response.isSuccessful) null else HermesStatus.parse(response.body?.string())
                }
        } catch (t: Throwable) {
            Log.i(TAG, "status failed: ${t.javaClass.simpleName}")
            null
        }
    }

    private fun post(path: String, body: JSONObject, timeoutS: Long): String? =
        (posted(path, body, timeoutS) as? Posted.Ok)?.text

    /** How a POST ended: the body, or which kind of not-a-body (the callers that care tell them apart). */
    private sealed interface Posted {
        data class Ok(val text: String) : Posted
        data class Refused(val code: Int) : Posted
        /** [noNetwork]: nothing reached the server, or nothing came back in time. */
        data object Unreachable : Posted
        data class Broke(val kind: String) : Posted
    }

    private fun posted(path: String, body: JSONObject, timeoutS: Long): Posted {
        val url = ServerUrl.normalize(serverUrl) + path
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .post(body.toString().toRequestBody(JSON))
            .build()
        return try {
            http.newBuilder().readTimeout(timeoutS, TimeUnit.SECONDS).build()
                .newCall(request).execute().use { response ->
                    val text = response.body?.string().orEmpty()
                    if (!response.isSuccessful) {
                        Log.w(TAG, "$path answered ${response.code}")
                        deliver { listener.onError(explain(path, response.code, text)) }
                        Posted.Refused(response.code)
                    } else {
                        Posted.Ok(text)
                    }
                }
        } catch (t: Throwable) {
            Log.w(TAG, "$path failed: ${t.javaClass.simpleName}")
            deliver { listener.onError("$path failed: ${t.javaClass.simpleName}") }
            if (noNetwork(t)) Posted.Unreachable else Posted.Broke(t.javaClass.simpleName)
        }
    }

    private fun explain(path: String, code: Int, body: String): String = when (code) {
        401 -> BAD_KEY
        502 -> "The adapter cannot reach Hermes (502 on $path)."
        else -> "$path answered $code" + (detailOf(body)?.let { ": $it" } ?: "")
    }

    private fun detailOf(body: String): String? = try {
        JSONObject(body).let { it.optString("detail").ifEmpty { it.optString("error") } }.takeIf { it.isNotEmpty() }
    } catch (t: Throwable) {
        null
    }

    companion object {
        /** The JSON-RPC error the adapter answers `approval.respond` with when it refuses a signature; the ask stays open. */
        const val SIGNATURE_REFUSED = -32000

        /** Hermes's own words for a turn it closed without an answer (agent/turn_failure_copy.py). */
        const val FAILED_TURN_NOTICE = "Your request was not processed. Send it again if you still want me to carry it out."

        /** A turn older than this is not resent: the moment it was for has passed. */
        private const val RESEND_WINDOW_MS = 120_000L

        /** The phone's own microphone, named on a transcribe (the house keeps prints per microphone). */
        const val SURFACE_PHONE = "android"
        /** The car's microphone, relayed over the projection. */
        const val SURFACE_CAR = "car"
        private const val TAG = "JarvisHermes"
        private val JSON = "application/json; charset=utf-8".toMediaType()

        const val GATEWAY_READY = "gateway.ready"

        /** The aura-hud plugin's command; its args are `@<sid> list` / `@<sid> dismiss <id|*>`. */
        const val HUD_COMMAND = "hud"

        /**
         * What this phone can show, sent as `params.surface` on every prompt
         * (`tests/contracts/surface-capabilities.json`, the `android` row —
         * `android-app/tools/surface_report_test.py` holds this to it). The
         * registry row verbatim: a client may only take capabilities away,
         * never add them. No `interactive`: the phone does not run a page's script.
         */
        val SURFACE: Map<String, Any> = mapOf(
            "name" to "android",
            "images" to true,
            "video" to true,
            "audio" to true,
            "documents" to true,
            "interactive" to false,
            "downloads" to true,
            "notifications" to true,
            "max_bytes" to 268435456,
        )

        /** The hud-layout plugin's command (`tests/contracts/hud-layout.json` `command.name`). */
        const val LAYOUT_COMMAND = "hud_layout"

        /** The aura-settings plugin's command (`tests/contracts/hermes-settings.json` `command.name`). */
        const val SETTINGS_COMMAND = "jsettings"

        /**
         * A `jsettings get` reply's `value`, out of the `output` JSON string,
         * or null for a refusal (`{"error": …}`), a missing output or a plain
         * line from an older plugin. Read loosely on purpose: the answer to
         * "what is hud.layouts" must never be a crash.
         */
        fun settingsValue(output: Any?): Any? {
            val text = output as? String ?: return null
            return try {
                val o = JSONObject(text)
                if (o.has("error")) null else o.opt("value")?.takeIf { it != JSONObject.NULL }
            } catch (t: Throwable) {
                null
            }
        }

        /** The adapter's close code for a bad key. */
        const val CLOSE_BAD_KEY = 4401

        const val BAD_KEY = "The adapter refused the key. Check the token in Settings."

        /** Faster than the device channel's: a voice surface is being looked at. */
        const val RECONNECT_BASE_MS = 500L
        const val RECONNECT_MAX_MS = 30_000L

        const val HANDSHAKE_TIMEOUT_MS = 10_000L

        /** The model may be cold: minutes, not seconds. */
        const val AUDIO_TIMEOUT_S = 240L
        /**
         * A transcription's own limit (T-1137): a cold Whisper took about 30 s on the 2026-11-12
         * drive, and the car gives a listen up after the audio's length plus 8 s, so a call nobody
         * waits for must not hold its thread for the four minutes speech is allowed.
         */
        const val TRANSCRIBE_TIMEOUT_S = 60L
        /** One read of the streamed line: a fifth of a second at 24 kHz, so the first piece is out fast. */
        const val PCM_PIECE_BYTES = 9600

        /**
         * Every call is the start of a line: the adapter sends its first clause to the voice
         * alone (tests/contracts/first-clause.json), so the first word comes sooner. A [rateHz]
         * is the rate the caller plays at, resampled on the adapter (T-0920); an adapter from
         * before it ignores the field and its `x-sample-rate` still says what came.
         */
        internal fun speakPcmBody(text: String, voice: String, rateHz: Int = 0): JSONObject =
            JSONObject().put("text", text).put("first_clause", true).apply {
                if (voice.isNotEmpty()) put("voice", voice)
                if (rateHz > 0) put("rate", rateHz)
            }
        const val STATUS_TIMEOUT_S = 8L

        /** Pure readers of the two audio answers, pinned by tests. */
        fun transcriptOf(body: String): String? = heardOf(body)?.transcript

        /**
         * True for a failure of the network itself: nothing connected (refused, no route,
         * unknown host), a connection that dropped, or nothing came back in time. Everything
         * else (a TLS fault, a malformed reply, a bug) is not "offline", whatever it looks like.
         * [java.net.SocketTimeoutException] is an [java.io.InterruptedIOException].
         */
        fun noNetwork(t: Throwable): Boolean =
            t is java.net.SocketException || t is java.net.UnknownHostException || t is java.io.InterruptedIOException

        fun heardOf(body: String): Heard? {
            val json = try { JSONObject(body) } catch (e: Exception) { return null }
            if (!json.optBoolean("ok", true)) return null
            val value = json.opt("transcript")
            val transcript = if (value == null || value == JSONObject.NULL) "" else value.toString().trim()
            val speaker = json.optJSONObject("speaker")
            val label = speaker?.opt("label")?.takeIf { it != JSONObject.NULL }?.toString()
            val ticket = json.opt("speaker_ticket")?.takeIf { it != JSONObject.NULL }?.toString()
            val claim = speaker?.opt("claim")?.takeIf { it != JSONObject.NULL }?.toString()
            return Heard(transcript, speaker?.optBoolean("dropped", false) == true, label, ticket, claim)
        }

        /**
         * The speak answer's audio. The doc names the key `audio` and the codec
         * WAV; the live adapter (2026-09-02) answers `data_url` and
         * `audio/mpeg`. Both spellings are read and the codec is the
         * player's problem, so a relay that changes its mind does not
         * silence the phone.
         */
        fun audioOf(body: String): ByteArray? {
            val json = try { JSONObject(body) } catch (e: Exception) { return null }
            if (!json.optBoolean("ok", true)) return null
            val url = json.optString("audio").ifEmpty { json.optString("data_url") }
            return WavAudio.decodeDataUrl(url)?.second
        }
    }
}
