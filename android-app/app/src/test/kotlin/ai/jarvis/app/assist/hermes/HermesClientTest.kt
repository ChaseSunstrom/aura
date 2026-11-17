package ai.jarvis.app.assist.hermes

import ai.jarvis.app.channel.Backoff
import okhttp3.Request
import okhttp3.WebSocket
import okio.ByteString
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The client against a fake socket and a manual clock: the handshake, the
 * session, the dispatch rules, the reconnect and the rejected key.
 */
class HermesClientTest {

    /** Everything the client put on the wire, as parsed frames. */
    /** Shared with the car client's test (HermesCarHoldTest). */
    internal class FakeSocket(private val req: Request) : WebSocket {
        val sent = ArrayList<JSONObject>()
        var closed: Int? = null
        var cancelled = false
        override fun request(): Request = req
        override fun queueSize(): Long = 0
        override fun send(text: String): Boolean { sent.add(JSONObject(text)); return true }
        override fun send(bytes: ByteString): Boolean = true
        override fun close(code: Int, reason: String?): Boolean { closed = code; return true }
        override fun cancel() { cancelled = true }
        fun last(): JSONObject = sent.last()
        fun lastMethod(): String = last().getString("method")
    }

    internal class ManualClock : Clockwork {
        val delayed = ArrayList<Pair<Runnable, Long>>()
        override fun post(task: Runnable) = task.run()
        override fun postDelayed(task: Runnable, delayMs: Long) { delayed.add(task to delayMs) }
        override fun remove(task: Runnable) { delayed.removeAll { it.first === task } }
        fun fire(index: Int) { val (task, _) = delayed.removeAt(index); task.run() }
    }

    private class Heard : HermesClient.Listener {
        val sessions = ArrayList<Pair<String, Boolean>>()
        val events = ArrayList<String>()
        val notices = ArrayList<String>()
        val prefills = ArrayList<String>()
        val errors = ArrayList<String>()
        val disconnects = ArrayList<Pair<String, Boolean>>()
        override fun onSession(sessionId: String, resumed: Boolean) { sessions.add(sessionId to resumed) }
        override fun onEvent(type: String, sessionId: String, payload: JSONObject) { events.add(type) }
        override fun onDisconnected(reason: String, willRetry: Boolean) { disconnects.add(reason to willRetry) }
        override fun onNotice(text: String) { notices.add(text) }
        override fun onPrefill(text: String) { prefills.add(text) }
        override fun onError(message: String) { errors.add(message) }
    }

    private val clock = ManualClock()
    private val heard = Heard()
    private val sockets = ArrayList<FakeSocket>()
    private val requests = ArrayList<Request>()
    private val client = HermesClient(
        serverUrl = "http://adapter.lan:8643",
        token = "devkey",
        listener = heard,
        clockwork = clock,
        socketFactory = { request, _ -> FakeSocket(request).also { sockets.add(it); requests.add(request) } },
        backoff = Backoff(baseMs = 500, maxMs = 30_000),
        random = { 0.0 },
    )

    private fun socket() = sockets.last()
    private fun ready() = client.onMessage(socket(), """{"jsonrpc":"2.0","method":"event","params":{"type":"gateway.ready","payload":{}}}""")
    private fun reply(id: Int, result: String) = client.onMessage(socket(), """{"jsonrpc":"2.0","id":$id,"result":$result}""")
    private fun fail(id: Int, code: Int, message: String) =
        client.onMessage(socket(), """{"jsonrpc":"2.0","id":$id,"error":{"code":$code,"message":"$message"}}""")

    @Test
    fun `the socket is dialled at slash ws with the bearer on the upgrade`() {
        client.connect()
        assertEquals("http://adapter.lan:8643/ws", requests[0].url.toString())
        assertEquals("Bearer devkey", requests[0].header("Authorization"))
        assertTrue("a handshake watchdog is armed", clock.delayed.any { it.second == HermesClient.HANDSHAKE_TIMEOUT_MS })
    }

    @Test
    fun `gateway ready creates a session and the session is announced`() {
        client.connect()
        ready()
        assertEquals("session.create", socket().lastMethod())
        val id = socket().last().getInt("id")
        reply(id, """{"session_id":"s-1","messages":[]}""")
        assertEquals(listOf("s-1" to false), heard.sessions)
        assertEquals("s-1", client.sessionId)
        assertTrue(heard.events.isEmpty())
    }

    @Test
    fun `a remembered session is resumed, and created when the resume fails`() {
        client.connect(resume = "old")
        ready()
        assertEquals("session.resume", socket().lastMethod())
        assertEquals("old", socket().last().getJSONObject("params").getString("session_id"))
        val id = socket().last().getInt("id")
        fail(id, 4004, "unknown session")
        assertEquals("session.create", socket().lastMethod())
        reply(socket().last().getInt("id"), """{"session_id":"new"}""")
        assertEquals(listOf("new" to false), heard.sessions)
    }

    /**
     * Hermes resumes by the STORED id and answers with a live one; the live id is what
     * every request carries (probed 2026-10-07: resume with the live id is 4007 "session
     * not found"). Remembering the live id made every phone conversation a new session.
     */
    @Test
    fun `a session is remembered by its stored id and resumed by it, talking on the live one`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"89b3195a","stored_session_id":"20261007_151301_eec3ac"}""")
        assertEquals("89b3195a", client.sessionId)
        assertEquals("20261007_151301_eec3ac", client.storedSessionId)
        // A dropped socket resumes the same conversation, by the stored id.
        client.onFailure(socket(), RuntimeException("reset"), null)
        clock.fire(0)
        ready()
        assertEquals("session.resume", socket().lastMethod())
        assertEquals("20261007_151301_eec3ac", socket().last().getJSONObject("params").getString("session_id"))
        reply(socket().last().getInt("id"), """{"session_id":"c0ffee11","stored_session_id":"20261007_151301_eec3ac","messages":[]}""")
        assertEquals("c0ffee11", client.sessionId)
        assertEquals(listOf("89b3195a" to false, "c0ffee11" to true), heard.sessions)
        client.submitPrompt("and text her")
        assertEquals("c0ffee11", socket().last().getJSONObject("params").getString("session_id"))
    }

    @Test
    fun `a resume that succeeds keeps the id`() {
        client.connect(resume = "old")
        ready()
        reply(socket().last().getInt("id"), """{"session_id":"old","messages":[]}""")
        assertEquals(listOf("old" to true), heard.sessions)
    }

    @Test
    fun `events after ready reach the listener`() {
        client.connect(); ready()
        client.onMessage(socket(), """{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta","session_id":"s","seq":1,"payload":{"text":"hi"}}}""")
        assertEquals(listOf("message.delta"), heard.events)
    }

    private fun event(type: String, payload: String) = client.onMessage(socket(),
        """{"jsonrpc":"2.0","method":"event","params":{"type":"$type","session_id":"s-1","seq":9,"payload":$payload}}""")

    private fun prompts() = socket().sent.count { it.optString("method") == "prompt.submit" }

    /**
     * 2026-10-06 15:10: "What is on my calendar tomorrow morning?" came back as Hermes's
     * "Your request was not processed. Send it again" -- a turn reaped mid-restart. With no
     * tool run, it is sent once more, and the notice is not what the person hears.
     */
    @Test
    fun `a turn Hermes dropped before any tool ran is sent once more`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        client.submitPrompt("What is on my calendar tomorrow morning?")
        event("message.complete", """{"text":"${HermesClient.FAILED_TURN_NOTICE}","status":"complete"}""")
        assertEquals(2, prompts())
        assertTrue(heard.events.none { it == "message.complete" })
        // Once: a second failure is shown, not retried forever.
        event("message.complete", """{"text":"${HermesClient.FAILED_TURN_NOTICE}","status":"complete"}""")
        assertEquals(2, prompts())
        assertEquals(1, heard.events.count { it == "message.complete" })
    }

    /** T-0644: a spoken turn says so even with no ticket; a typed one never does; a resend keeps it. */
    @Test
    fun `a spoken prompt carries voice true, a typed one does not, and the resend keeps it`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        client.submitPrompt("hello")
        assertFalse(socket().last().getJSONObject("params").has("voice"))
        client.submitPrompt("lights off", null, voice = true)
        assertTrue(socket().last().getJSONObject("params").getBoolean("voice"))
        assertFalse(socket().last().getJSONObject("params").has("speaker_ticket"))
        event("message.complete", """{"text":"${HermesClient.FAILED_TURN_NOTICE}","status":"complete"}""")
        assertTrue(socket().last().getJSONObject("params").getBoolean("voice"))
    }

    @Test
    fun `a dropped turn that had run a tool is not sent again`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        client.submitPrompt("Text Isa that I'm here")
        event("tool.start", """{"name":"phone_action"}""")
        event("message.complete", """{"text":"${HermesClient.FAILED_TURN_NOTICE}"}""")
        assertEquals(1, prompts())
    }

    @Test
    fun `a socket lost mid-turn resends the prompt once on reconnect`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1","stored_session_id":"st-1"}""")
        client.submitPrompt("Lock the front door")
        client.onFailure(socket(), RuntimeException("reset"), null)
        clock.fire(0); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-2","stored_session_id":"st-1"}""")
        assertEquals("prompt.submit", socket().lastMethod())
        assertEquals("Lock the front door", socket().last().getJSONObject("params").getString("text"))
    }

    /**
     * AUDIT B5: a prompt typed while the socket was down failed at once (NOT_CONNECTED) and
     * the caller said so, yet the resume sent it anyway, a turn the owner thought was dropped.
     */
    @Test
    fun `a prompt that never left the phone is not sent behind the caller on resume`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1","stored_session_id":"st-1"}""")
        client.onFailure(socket(), RuntimeException("reset"), null)
        var answer: HermesRpc.Reply? = null
        client.submitPrompt("Lock the front door") { answer = it }
        assertEquals(HermesRpc.NOT_CONNECTED, (answer as HermesRpc.Reply.Error).code)
        clock.fire(0); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-2","stored_session_id":"st-1"}""")
        assertEquals("session.resume", socket().lastMethod())
    }

    @Test
    fun `a lost turn whose history shows a tool is not sent again`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1","stored_session_id":"st-1"}""")
        client.submitPrompt("Text Isa I'm here")
        client.onFailure(socket(), RuntimeException("reset"), null)
        clock.fire(0); ready()
        reply(
            socket().last().getInt("id"),
            """{"session_id":"s-2","stored_session_id":"st-1","messages":[{"role":"user","text":"Text Isa I'm here"},{"role":"tool","name":"phone_action"}]}""",
        )
        assertEquals("session.resume", socket().lastMethod())
    }

    @Test
    fun `a lost turn the server still runs is not sent again`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1","stored_session_id":"st-1"}""")
        client.submitPrompt("Text Isa I'm here")
        client.onFailure(socket(), RuntimeException("reset"), null)
        clock.fire(0); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-2","stored_session_id":"st-1","running":true}""")
        assertEquals("session.resume", socket().lastMethod())
    }

    @Test
    fun `prompts and answers carry the session id`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        client.submitPrompt("hello")
        var params = socket().last().getJSONObject("params")
        assertEquals("prompt.submit", socket().lastMethod())
        assertEquals("s-1", params.getString("session_id"))
        assertEquals("hello", params.getString("text"))

        client.respondApproval("r1", "once")
        params = socket().last().getJSONObject("params")
        assertEquals("approval.respond", socket().lastMethod())
        assertEquals("r1", params.getString("request_id"))
        assertEquals("once", params.getString("choice"))

        client.respondClarify("c1", "the inbox")
        params = socket().last().getJSONObject("params")
        assertEquals("clarify.respond", socket().lastMethod())
        assertEquals("the inbox", params.getString("answer"))

        client.interrupt()
        assertEquals("session.interrupt", socket().lastMethod())
        assertEquals("s-1", socket().last().getJSONObject("params").getString("session_id"))
    }

    @Test
    fun `a send result shows the notice and then submits the message`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        val outcomes = ArrayList<HermesDispatch.Outcome>()
        client.dispatchCommand("goal", "tidy the notes") { outcomes.add(it) }
        val dispatch = socket().last()
        assertEquals("command.dispatch", dispatch.getString("method"))
        assertEquals("goal", dispatch.getJSONObject("params").getString("name"))
        assertEquals("tidy the notes", dispatch.getJSONObject("params").getString("arg"))
        reply(dispatch.getInt("id"), """{"type":"send","notice":"Goal set (20-turn budget): tidy the notes","message":"tidy the notes"}""")
        assertEquals(listOf("Goal set (20-turn budget): tidy the notes"), heard.notices)
        assertEquals("prompt.submit", socket().lastMethod())
        assertEquals("tidy the notes", socket().last().getJSONObject("params").getString("text"))
        assertTrue(outcomes.single() is HermesDispatch.Outcome.Send)
    }

    @Test
    fun `an exec result is shown and sends nothing`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        client.dispatchCommand("goal", "clear")
        val before = socket().sent.size
        reply(socket().last().getInt("id"), """{"type":"exec","output":"Goal cleared."}""")
        assertEquals(listOf("Goal cleared."), heard.notices)
        assertEquals(before, socket().sent.size)
    }

    @Test
    fun `a plugin command goes out with the session in its arg and its reply is shown`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        client.dispatchCommand("jgoal", "gate add ./gradlew test")
        val params = socket().last().getJSONObject("params")
        assertEquals("jgoal", params.getString("name"))
        assertEquals("@s-1 gate add ./gradlew test", params.getString("arg"))
        assertEquals("s-1", params.getString("session_id"))
        val before = socket().sent.size
        reply(socket().last().getInt("id"), """{"type":"plugin","output":"Gate 1 added: ./gradlew test"}""")
        assertEquals(listOf("Gate 1 added: ./gradlew test"), heard.notices)
        assertEquals(before, socket().sent.size)
        // The built-in goal command is sent as typed.
        client.dispatchCommand("goal", "pause")
        assertEquals("pause", socket().last().getJSONObject("params").getString("arg"))
    }

    @Test
    fun `a prefill result fills the composer`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        client.dispatchCommand("jgoal", "show")
        reply(socket().last().getInt("id"), """{"type":"prefill","message":"draft"}""")
        assertEquals(listOf("draft"), heard.prefills)
        assertTrue(heard.notices.isEmpty())
    }

    @Test
    fun `a 4018 on a plugin command is explained`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        client.dispatchCommand("jgoal", "subgoal also tag them")
        fail(socket().last().getInt("id"), 4018, "unknown command")
        assertEquals(1, heard.errors.size)
        assertTrue(heard.errors[0], heard.errors[0].contains("4018"))
        assertTrue(heard.errors[0], heard.errors[0].contains("jgoal"))
    }

    @Test
    fun `a dropped socket fails the waiting request, tells the listener and reconnects with backoff`() {
        client.connect(); ready()
        reply(socket().last().getInt("id"), """{"session_id":"s-1"}""")
        var reply: HermesRpc.Reply? = null
        client.submitPrompt("hi") { reply = it }
        client.onFailure(socket(), RuntimeException("reset"), null)
        assertEquals(HermesRpc.DISCONNECTED, (reply as HermesRpc.Reply.Error).code)
        assertEquals(1, heard.disconnects.size)
        assertTrue("will retry", heard.disconnects[0].second)
        assertFalse(client.connected)
        val pending = clock.delayed.single()
        assertEquals("first retry is the floor", 500L, pending.second)
        // The reconnect dials a new socket and resumes the same session.
        clock.fire(0)
        assertEquals(2, sockets.size)
        ready()
        assertEquals("session.resume", socket().lastMethod())
        assertEquals("s-1", socket().last().getJSONObject("params").getString("session_id"))
    }

    // T-1187: the 2026-11-12 drive's socket opened after a drop and never saw gateway.ready.

    private fun deadlines() = clock.delayed.filter { it.second == HermesClient.HANDSHAKE_TIMEOUT_MS }
    private fun redial() { clock.fire(clock.delayed.indexOfFirst { it.second != HermesClient.HANDSHAKE_TIMEOUT_MS }) }

    private fun assertReadyAgain(dials: Int) {
        redial()
        assertEquals(dials, sockets.size)
        ready()
        assertEquals("session.create", socket().lastMethod())
        reply(socket().last().getInt("id"), """{"session_id":"s-2"}""")
        assertEquals("s-2", client.sessionId)
        assertTrue(client.connected)
    }

    @Test
    fun `a socket that opens and stays silent is dropped at the ready deadline and a fresh one reaches ready`() {
        client.connect()
        assertEquals(10_000L, HermesClient.HANDSHAKE_TIMEOUT_MS)
        clock.fire(clock.delayed.indexOf(deadlines().single()))
        assertTrue("the silent socket is cut", sockets[0].cancelled)
        assertEquals(listOf(true), heard.disconnects.map { it.second })
        assertReadyAgain(2)
    }

    @Test
    fun `a frame that is not gateway ready does not end the deadline`() {
        client.connect()
        event("approval.request", "{}")
        assertEquals("the deadline still stands", 1, deadlines().size)
        clock.fire(clock.delayed.indexOf(deadlines().single()))
        assertTrue(sockets[0].cancelled)
        assertReadyAgain(2)
    }

    @Test
    fun `a socket closed or failed before ready is followed by a fresh connect that reaches ready`() {
        client.connect()
        client.onClosed(socket(), 1013, "upstream not ready")
        assertTrue("the old deadline went with its socket", deadlines().isEmpty())
        redial()
        client.onFailure(socket(), RuntimeException("reset"), null)
        assertReadyAgain(3)
    }

    @Test
    fun `gateway ready cancels the deadline`() {
        client.connect(); ready()
        assertTrue(deadlines().isEmpty())
    }

    @Test
    fun `a deadline left from an older socket never closes a newer one`() {
        client.connect()
        val old = deadlines().single().first
        client.onFailure(socket(), RuntimeException("reset"), null)
        redial()
        // The old runnable fires late (a clock that did not honour remove()): the new socket lives.
        old.run()
        assertFalse(sockets[1].cancelled)
        assertEquals(1, heard.disconnects.size)
        ready()
        assertTrue(client.connected)
    }

    @Test
    fun `a rejected key does not retry`() {
        client.connect()
        client.onClosed(socket(), HermesClient.CLOSE_BAD_KEY, "unauthorized")
        assertEquals(listOf(HermesClient.BAD_KEY to false), heard.disconnects)
        assertTrue("nothing scheduled", clock.delayed.none { it.second != HermesClient.HANDSHAKE_TIMEOUT_MS })
        assertEquals(1, sockets.size)
    }

    @Test
    fun `close stops everything and fails the outstanding requests`() {
        client.connect(); ready()
        var reply: HermesRpc.Reply? = null
        client.request("commands.catalog") { reply = it }
        client.close()
        assertEquals(1000, socket().closed)
        assertEquals(HermesRpc.DISCONNECTED, (reply as HermesRpc.Reply.Error).code)
        assertTrue(clock.delayed.isEmpty())
        // A late failure from the closed socket is ignored.
        client.onFailure(socket(), RuntimeException("late"), null)
        assertTrue(heard.disconnects.isEmpty())
    }

    @Test
    fun `a settings reply's value is read loosely and a refusal is null`() {
        // `jsettings get` answers a plugin result whose output is a JSON
        // string; a refusal is `{"error": …}` inside it, and an older plugin
        // may answer a plain line. None of them may be a crash.
        assertEquals("overview", HermesClient.settingsValue("""{"key":"hud.mode","value":"overview","source":"default"}"""))
        assertEquals(true, HermesClient.settingsValue("""{"key":"hud.lock_layout","value":true}"""))
        assertTrue(HermesClient.settingsValue("""{"key":"hud.layouts","value":{"android_phone":{}}}""") is JSONObject)
        assertNull(HermesClient.settingsValue("""{"error":"unknown key"}"""))
        assertNull(HermesClient.settingsValue("""{"key":"x","value":null}"""))
        assertNull(HermesClient.settingsValue("not json"))
        assertNull(HermesClient.settingsValue(null))
    }

    @Test
    fun `an unusable address is an error, not a dial`() {
        val bad = HermesClient("nonsense", "k", heard, clock, { _, _ -> throw AssertionError("must not dial") })
        bad.connect()
        assertEquals(1, heard.errors.size)
        assertNull(bad.sessionId)
    }

    @Test
    fun `the car asks for its speech at the rate its guidance channel plays, the phone at the voice's`() {
        // T-0920: a third fewer bytes over the phone's link for the car's lines.
        val car = HermesClient.speakPcmBody("Turn left.", "aura", ai.jarvis.app.car.voice.HermesCarJarvis.CAR_SPEECH_HZ)
        assertEquals(16_000, car.getInt("rate"))
        assertTrue(car.getBoolean("first_clause"))
        assertEquals("aura", car.getString("voice"))
        val phone = HermesClient.speakPcmBody("Hello.", "")
        assertFalse(phone.has("rate"))
        assertFalse(phone.has("voice"))
    }
}
