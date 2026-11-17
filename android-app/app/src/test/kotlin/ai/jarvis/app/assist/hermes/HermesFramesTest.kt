package ai.jarvis.app.assist.hermes

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The JSON-RPC framing, pinned to `docs/hermes-hud-protocol.md`. */
class HermesFramesTest {

    @Test
    fun `a request carries the version, the id, the method and the params`() {
        val frame = JSONObject(HermesFrames.request(7, "prompt.submit", JSONObject().put("session_id", "s1").put("text", "hi")))
        assertEquals("2.0", frame.getString("jsonrpc"))
        assertEquals(7, frame.getInt("id"))
        assertEquals("prompt.submit", frame.getString("method"))
        assertEquals("s1", frame.getJSONObject("params").getString("session_id"))
        assertEquals("hi", frame.getJSONObject("params").getString("text"))
    }

    @Test
    fun `a request with no params still sends an empty object`() {
        val frame = JSONObject(HermesFrames.request(1, "session.create"))
        assertEquals(0, frame.getJSONObject("params").length())
    }

    @Test
    fun `frames are one line`() {
        assertTrue(HermesFrames.request(1, "session.list").none { it == '\n' })
    }

    @Test
    fun `a result is a response with its id`() {
        val frame = HermesFrames.parse("""{"jsonrpc":"2.0","id":3,"result":{"session_id":"abc"}}""")
        assertTrue(frame is HermesFrames.Frame.Response)
        frame as HermesFrames.Frame.Response
        assertEquals(3, frame.id)
        assertEquals("abc", frame.result.getString("session_id"))
    }

    @Test
    fun `an array result is wrapped so callers always get an object`() {
        val frame = HermesFrames.parse("""{"jsonrpc":"2.0","id":4,"result":[{"id":"a"},{"id":"b"}]}""")
        frame as HermesFrames.Frame.Response
        assertEquals(2, frame.result.getJSONArray(HermesFrames.VALUE).length())
    }

    @Test
    fun `an error is a failure with code and message`() {
        val frame = HermesFrames.parse("""{"jsonrpc":"2.0","id":2,"error":{"code":4001,"message":"no active session"}}""")
        assertEquals(HermesFrames.Frame.Failure(2, 4001, "no active session"), frame)
    }

    @Test
    fun `an event carries type, session, seq and payload`() {
        val frame = HermesFrames.parse(
            """{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta","session_id":"s","seq":9,"payload":{"text":"Hel"}}}"""
        )
        frame as HermesFrames.Frame.Event
        assertEquals("message.delta", frame.type)
        assertEquals("s", frame.sessionId)
        assertEquals(9, frame.seq)
        assertEquals("Hel", frame.payload.getString("text"))
    }

    @Test
    fun `the adapter's own event has no session and still parses`() {
        val frame = HermesFrames.parse(
            """{"jsonrpc":"2.0","method":"event","params":{"type":"adapter.error","payload":{"error":"backend unavailable"}}}"""
        )
        frame as HermesFrames.Frame.Event
        assertEquals("adapter.error", frame.type)
        assertEquals("", frame.sessionId)
        assertEquals("backend unavailable", frame.payload.getString("error"))
    }

    @Test
    fun `anything else is unknown, not a crash`() {
        assertTrue(HermesFrames.parse("not json") is HermesFrames.Frame.Unknown)
        assertTrue(HermesFrames.parse("""{"jsonrpc":"2.0"}""") is HermesFrames.Frame.Unknown)
        assertTrue(HermesFrames.parse("""{"method":"event"}""") is HermesFrames.Frame.Unknown)
        assertTrue(HermesFrames.parse("""{"method":"event","params":{}}""") is HermesFrames.Frame.Unknown)
    }
}
