package ai.aura.app.assist.hermes

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HermesStatusTest {

    /** The adapter's real answer, captured on 2026-09-02. */
    private val ready = """{"ok":true,"model":{"model":"qwen3.8-27b","state":"ready","warming_up":false,"loaded":["qwen3.8-27b"]},"hermes":{"api_server":{"ok":true,"version":"0.21.0"},"backend":{"ok":true,"version":"0.21.0"}},"llama_swap":{"ok":true,"url":"http://jarvis-models:8000"},"latency_ms":6}"""

    /**
     * `tests/contracts/status-words.json` (T-0583): the adapter's word and reason, printed. Read
     * with no guard: a moved table fails this test, as it fails the console's and the desktop's.
     */
    @Test
    fun `status word - every row of the table is printed from the adapter's field`() {
        val table = org.json.JSONObject(java.io.File("../../tests/contracts/status-words.json").readText())
        val cases = table.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            // The rest of the report disagrees with the field on purpose: the label reads only the field.
            val body = org.json.JSONObject()
                .put("ok", true)
                .put("model", org.json.JSONObject().put("model", "m").put("state", "ready"))
                .put("status", org.json.JSONObject().put("word", c.getString("word")).put("reason", c.getString("reason")))
            val s = HermesStatus.parse(body.toString())!!
            assertEquals(c.getString("name"), c.getString("label"), s.label)
            assertEquals(c.getString("name"), c.getString("label"), s.caption)
            assertEquals("m · " + c.getString("label"), s.readout)
        }
        assertEquals(table.getJSONObject("no_answer").getString("label"), HermesStatus.NO_ANSWER_LABEL)
    }

    @Test
    fun `status word - absent from an older adapter, the wording is what it was`() {
        val s = HermesStatus.parse("""{"ok":true,"model":{"model":"m","state":"starting","warming_up":true}}""")!!
        assertEquals("", s.label)
        assertEquals(HermesStatus.WARMING_LABEL, s.caption)
        val odd = HermesStatus.parse("""{"ok":true,"model":{"model":"m","state":"ready"},"status":"fine"}""")!!
        assertEquals("Ready", odd.caption)
    }

    @Test
    fun `a ready model`() {
        val s = HermesStatus.parse(ready)!!
        assertTrue(s.ok)
        assertEquals("qwen3.8-27b", s.model)
        assertEquals("ready", s.state)
        assertFalse(s.warmingUp)
        assertTrue(s.backendOk)
        assertTrue(s.apiOk)
        assertEquals("qwen3.8-27b · Ready", s.readout)
    }

    @Test
    fun `the status says who is primary, and nothing means JARVIS`() {
        val switched = HermesStatus.parse("""{"ok":true,"model":{"model":"m","state":"ready"},"persona":{"active":"AURA","since":"2026-09-10T18:29:49Z"}}""")!!
        assertEquals("aura", switched.persona)
        val silent = HermesStatus.parse("""{"ok":true,"model":{"model":"m","state":"ready"}}""")!!
        assertEquals("", silent.persona)
    }

    @Test
    fun `a model that is starting is warming up`() {
        val s = HermesStatus.parse("""{"ok":true,"model":{"model":"m","state":"starting","warming_up":true}}""")!!
        assertTrue(s.warmingUp)
        assertEquals(HermesStatus.WARMING_LABEL, s.caption)
        assertEquals("m · Warming up model", s.readout)
    }

    @Test
    fun `waiting caption - no answer when there is no status, the warming caption while warming, nothing when ready`() {
        assertEquals(HermesStatus.NO_ANSWER_LABEL, HermesStatus.waitingCaption(null))
        val warming = HermesStatus.parse("""{"ok":true,"model":{"model":"m","state":"starting","warming_up":true}}""")
        assertEquals(HermesStatus.WARMING_LABEL, HermesStatus.waitingCaption(warming))
        val ready = HermesStatus.parse("""{"ok":true,"model":{"model":"m","state":"ready"}}""")
        assertNull(HermesStatus.waitingCaption(ready))
    }

    @Test
    fun `an unreachable llama-swap is not warming`() {
        val s = HermesStatus.parse("""{"ok":false,"model":{"model":"m","state":"unreachable","warming_up":false},"hermes":{"backend":{"ok":false}}}""")!!
        assertFalse(s.warmingUp)
        assertFalse(s.backendOk)
        assertEquals("Unreachable", s.caption)
    }

    @Test
    fun `not a status`() {
        assertNull(HermesStatus.parse(null))
        assertNull(HermesStatus.parse(""))
        assertNull(HermesStatus.parse("<html>"))
        assertNull(HermesStatus.parse("""{"ok":true}"""))
    }

    @Test
    fun `poll every five seconds while a turn waits on its first token, every thirty otherwise`() {
        assertEquals(5_000L, HermesStatus.pollIntervalMs(turnPending = true, tokenSeen = false))
        assertEquals(30_000L, HermesStatus.pollIntervalMs(turnPending = true, tokenSeen = true))
        assertEquals(30_000L, HermesStatus.pollIntervalMs(turnPending = false, tokenSeen = false))
        assertEquals(30_000L, HermesStatus.pollIntervalMs(turnPending = false, tokenSeen = true))
    }
}
