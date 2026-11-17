package ai.jarvis.app.assist.hermes

import ai.jarvis.app.assist.ActivityRows
import ai.jarvis.app.assist.ToolRun
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** A scripted turn through the reducer: text, a tool, an approval, a goal that continues and is achieved. */
class HudStateTest {

    private val hud = HudState()

    private fun ev(type: String, json: String) = hud.apply(type, JSONObject(json), now = 1_000L)

    @Test
    fun `a persona switch names who is primary, lower-cased, once per name`() {
        assertEquals("", hud.persona)
        assertTrue(ev("persona.switch", """{"active":"AURA"}""").contains(HudState.Change.PERSONA))
        assertEquals("aura", hud.persona)
        // The same name again is not a change; a blank one is ignored.
        assertFalse(ev("persona.switch", """{"active":"aura"}""").contains(HudState.Change.PERSONA))
        assertFalse(ev("persona.switch", """{"active":""}""").contains(HudState.Change.PERSONA))
        assertEquals("aura", hud.persona)
    }

    @Test
    fun `session info names the model and the approval mode`() {
        val changed = ev("session.info", """{"model":"qwen3.8-27b","provider":"custom","approval_mode":"smart"}""")
        assertEquals(setOf(HudState.Change.MODEL), changed)
        assertEquals("qwen3.8-27b", hud.model)
        assertEquals("smart", hud.approvalMode)
    }

    @Test
    fun `a scripted turn`() {
        hud.turnStarted()
        assertTrue(hud.turnPending)
        assertFalse(hud.tokenSeen)

        // Thinking first, then text.
        assertEquals(setOf(HudState.Change.REASONING), ev("reasoning.delta", """{"text":"The user wants "}"""))
        assertTrue("a reasoning token counts as a token", hud.tokenSeen)
        ev("reasoning.delta", """{"text":"the notes tidied."}""")
        assertEquals("The user wants the notes tidied.", hud.reasoning)

        assertEquals(setOf(HudState.Change.TEXT), ev("message.delta", """{"text":"Starting"}"""))
        ev("message.delta", """{"text":" with the inbox."}""")
        assertEquals("Starting with the inbox.", hud.text)

        // A tool runs.
        val started = ev("tool.start", """{"tool_id":"t1","name":"read_file","context":"","args":{"path":"/notes/inbox.md"}}""")
        assertEquals(setOf(HudState.Change.TOOLS, HudState.Change.ACTIVITY), started)
        assertEquals(1, hud.tools.rows.size)
        assertEquals(ToolRun.State.RUNNING, hud.tools.rows[0].state)
        assertEquals("read_file", hud.tools.rows[0].name)
        assertEquals("path: /notes/inbox.md", hud.tools.rows[0].summary)
        assertTrue(hud.tools.running)
        assertEquals(ActivityRows.Kind.TOOL, hud.activity.rows[0].kind)
        assertEquals(ActivityRows.State.LIVE, hud.activity.rows[0].state)

        ev("tool.progress", """{"tool_id":"t1","text":"3 of 9 lines"}""")
        assertEquals("3 of 9 lines", hud.tools.rows[0].summary)

        ev("tool.complete", """{"tool_id":"t1","name":"read_file","args":{},"duration":0.42}""")
        assertEquals(ToolRun.State.OK, hud.tools.rows[0].state)
        assertEquals(420, hud.tools.rows[0].durationMs)
        assertFalse(hud.tools.running)
        assertEquals(100, hud.tools.percent)
        assertEquals(ActivityRows.State.DONE, hud.activity.rows[0].state)

        // A second tool fails.
        ev("tool.start", """{"tool_id":"t2","name":"write_file","args":{"path":"/notes/index.md","content":"a very long body of text that runs past forty characters"}}""")
        assertEquals(2, hud.tools.rows.size)
        assertEquals("2", hud.tools.total.toString())
        ev("tool.complete", """{"tool_id":"t2","name":"write_file","error":"permission denied","duration":0.01}""")
        assertEquals(ToolRun.State.FAILED, hud.tools.rows[1].state)
        assertEquals("permission denied", hud.tools.rows[1].error)
        assertEquals(1, hud.tools.failed)
        assertEquals(ToolRun.FAILED_HOLD_MS, hud.tools.holdMs())

        // An approval is asked for, with the smart-denied subset of choices.
        val asked = ev(
            "approval.request",
            """{"request_id":"r1","tool":"terminal","command":"rm -rf build","reason":"clean the tree","choices":["once","deny"]}""",
        )
        assertEquals(setOf(HudState.Change.APPROVAL, HudState.Change.ACTIVITY), asked)
        assertEquals(1, hud.approvals.size)
        assertEquals("rm -rf build", hud.approvals[0].params)
        assertEquals("clean the tree", hud.approvals[0].reason)
        assertEquals(listOf("once", "deny"), hud.approvals[0].choices)
        assertEquals(ActivityRows.Kind.APPROVAL, hud.activity.rows[0].kind)
        assertTrue(hud.approvalAnswered("r1"))
        assertTrue(hud.approvals.isEmpty())
        assertFalse(hud.approvalAnswered("r1"))

        // The goal loop continues, then is achieved.
        val goal = ev("status.update", """{"kind":"goal","text":"↻ Continuing toward goal (2/20): the index is still missing"}""")
        assertEquals(setOf(HudState.Change.GOAL), goal)
        assertEquals(GoalStatus.State.CONTINUING, hud.goal!!.state)
        assertEquals(2, hud.goal!!.turn)
        assertEquals("the index is still missing", hud.goal!!.reason)

        // Completion ends the turn.
        val done = ev("message.complete", """{"text":"Starting with the inbox. Done."}""")
        assertTrue(done.containsAll(setOf(HudState.Change.TEXT, HudState.Change.COMPLETE)))
        assertEquals("Starting with the inbox. Done.", hud.text)
        assertFalse(hud.turnPending)

        ev("status.update", """{"kind":"goal","text":"✓ Goal achieved: every note is indexed"}""")
        assertEquals(GoalStatus.State.ACHIEVED, hud.goal!!.state)
        assertEquals("every note is indexed", hud.goal!!.reason)

        ev("status.update", """{"kind":"goal","text":"Goal cleared"}""")
        assertEquals(GoalStatus.State.CLEARED, hud.goal!!.state)
        assertFalse(hud.goal!!.present)

        // The next turn starts clean.
        hud.turnStarted()
        assertEquals("", hud.text)
        assertEquals("", hud.reasoning)
        assertTrue(hud.tools.isEmpty)
        assertTrue(hud.turnPending)
        assertFalse(hud.tokenSeen)
        // But the goal and the model survive: they belong to the session.
        assertEquals(GoalStatus.State.CLEARED, hud.goal!!.state)
    }

    @Test
    fun `a turn the goal loop starts by itself begins a fresh reply`() {
        hud.turnStarted()
        ev("message.delta", """{"text":"First reply."}""")
        ev("tool.start", """{"tool_id":"a","name":"read_file","args":{}}""")
        ev("tool.complete", """{"tool_id":"a","name":"read_file","duration":0.1}""")
        ev("message.complete", """{"text":"First reply."}""")
        ev("status.update", """{"kind":"goal","text":"↻ Continuing toward goal (2/9): more to do"}""")
        assertFalse(hud.turnPending)
        // No prompt.submit from this end, and the next turn's tokens arrive.
        ev("reasoning.delta", """{"text":"Next: "}""")
        assertTrue(hud.turnPending)
        assertEquals("", hud.text)
        assertTrue("the last turn's tool rows are gone", hud.tools.isEmpty)
        ev("message.delta", """{"text":"Second reply."}""")
        assertEquals("Second reply.", hud.text)
        assertEquals(GoalStatus.State.CONTINUING, hud.goal!!.state)
    }

    @Test
    fun `a complete with no text keeps what streamed`() {
        hud.turnStarted()
        ev("message.delta", """{"text":"Hello"}""")
        ev("message.complete", """{}""")
        assertEquals("Hello", hud.text)
        assertFalse(hud.turnPending)
    }

    @Test
    fun `an interim note is appended unless it was already streamed`() {
        hud.turnStarted()
        ev("message.delta", """{"text":"one"}""")
        ev("message.interim", """{"text":"one","already_streamed":true}""")
        assertEquals("one", hud.text)
        ev("message.interim", """{"text":"two","already_streamed":false}""")
        assertEquals("one\ntwo", hud.text)
    }

    @Test
    fun `a tool completing with no start still lands`() {
        ev("tool.complete", """{"tool_id":"late","name":"search","duration":1.5}""")
        assertEquals(1, hud.tools.rows.size)
        assertEquals(ToolRun.State.OK, hud.tools.rows[0].state)
        assertEquals(1500, hud.tools.rows[0].durationMs)
    }

    @Test
    fun `a clarify request is kept until answered`() {
        val changed = ev("clarify.request", """{"request_id":"c1","question":"Which folder?","options":["notes","inbox"]}""")
        assertEquals(setOf(HudState.Change.CLARIFY), changed)
        assertEquals(listOf("notes", "inbox"), hud.clarifies[0].options)
        assertTrue(hud.clarifyAnswered("c1"))
        assertTrue(hud.clarifies.isEmpty())
    }

    /**
     * `tests/contracts/clarify.json` (T-0802): the adapter's clarify.request for each Hermes shape,
     * one question or a batch. The phone reads only the flat question and options and answers with no
     * question_id, so a batch that rides no flat question shows an empty dialog (T-0798).
     */
    @Test
    fun `every clarify shape the adapter emits shows its question and options`() {
        val contract = JSONObject(java.io.File("../../tests/contracts/clarify.json").readText())
        val cases = contract.getJSONArray("cases")
        val kinds = (0 until cases.length()).map { cases.getJSONObject(it).getString("kind") }.toSet()
        assertEquals(setOf("single", "batch"), kinds)
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val shown = case.getJSONObject("shown")
            val fresh = HudState()
            fresh.apply("clarify.request", case.getJSONObject("app_payload"), now = 1_000L)
            val asked = fresh.clarifies.single()
            val name = case.getString("name")
            assertEquals(name, contract.getString("srq"), asked.requestId)
            assertEquals(name, shown.getString("question"), asked.question)
            val options = shown.getJSONArray("options")
            assertEquals(name, (0 until options.length()).map { options.getString(it) }, asked.options)
        }
    }

    @Test
    fun `other status updates are a status line, not a goal`() {
        ev("status.update", """{"kind":"compact","text":"compacting context"}""")
        assertEquals("compacting context", hud.status)
        assertNull(hud.goal)
    }

    @Test
    fun `an adapter error is offline until the next event`() {
        hud.turnStarted()
        val changed = ev("adapter.error", """{"error":"backend unavailable"}""")
        assertTrue(changed.contains(HudState.Change.OFFLINE))
        assertTrue(hud.offline)
        assertFalse(hud.turnPending)
        assertEquals("backend unavailable", hud.lastError)
        val next = ev("session.info", """{"model":"m"}""")
        assertTrue(next.contains(HudState.Change.OFFLINE))
        assertFalse(hud.offline)
    }

    @Test
    fun `a server error ends the pending turn`() {
        hud.turnStarted()
        assertEquals(setOf(HudState.Change.ERROR), ev("error", """{"message":"model crashed"}"""))
        assertEquals("model crashed", hud.lastError)
        assertFalse(hud.turnPending)
    }

    @Test
    fun `an event the HUD does not draw changes nothing`() {
        assertTrue(ev("session.usage", """{"tokens":12}""").isEmpty())
    }

    @Test
    fun `choices come in protocol order, deny is always offered, unknown ones are dropped`() {
        assertEquals(listOf("once", "session", "always", "deny"), HudState.choicesOf(JSONObject("""{"choices":["deny","always","session","once"]}""")))
        assertEquals(listOf("once", "deny"), HudState.choicesOf(JSONObject("""{"choices":["once"]}""")))
        assertEquals(listOf("once", "deny"), HudState.choicesOf(JSONObject("""{"options":["once","deny","yolo"]}""")))
        assertEquals(HudState.ALL_CHOICES, HudState.choicesOf(JSONObject("{}")))
    }

    @Test
    fun `a layout frame changes nothing on the phone`() {
        // The phone has no layout to arrange (the home is the console); the frame
        // is a real event from the plugin and must fall through quietly.
        assertTrue(ev("hud.layout.apply", """{"op":"arrange","surface":"web","layout":{"mode":"task","panels":[]}}""").isEmpty())
    }

    @Test
    fun `a highlight names a panel and carries its duration`() {
        val changed = ev("hud.layout.highlight", """{"panel":"approvals","surface":"web","duration_ms":1200,"reason":"approval needed"}""")
        assertEquals(setOf(HudState.Change.HIGHLIGHT), changed)
        assertEquals("approvals", hud.highlight!!.panel)
        assertEquals(1200, hud.highlight!!.durationMs)
        assertTrue(ev("hud.layout.highlight", """{"surface":"web"}""").isEmpty())
    }

    @Test
    fun `durations arrive in seconds and are kept in milliseconds`() {
        assertEquals(1500, HudState.durationMs(1.5))
        assertEquals(250, HudState.durationMs("0.25"))
        assertEquals(0, HudState.durationMs(null))
        assertEquals(0, HudState.durationMs(-1.0))
    }
}
