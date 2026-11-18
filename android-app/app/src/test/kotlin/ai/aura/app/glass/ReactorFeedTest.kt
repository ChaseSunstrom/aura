package ai.aura.app.glass

import ai.aura.app.assist.Phase
import ai.aura.app.assist.hermes.HudState
import ai.aura.app.reactor.ReactorState
import ai.aura.app.reactor.derive
import org.json.JSONObject
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The reactor's inputs, gathered from what the phone already knows
 * (GLASS_HUD §10; `ReactorFeed`).
 *
 * Every case drives [HudState] with the events the socket really sends rather
 * than setting fields, because the fields are `private set` for a reason and a
 * test that reached past that would prove the mapping against a state the app
 * cannot actually be in.
 *
 * The assertions go one step further than the inputs where it is cheap, to
 * `ReactorState.derive`, because the input struct is not the thing anyone
 * looks at — the state is. A mapping that populated the right field and still
 * lit the wrong ring would pass a test that stopped at the struct.
 */
class ReactorFeedTest {

    private val hud = HudState()

    private fun ev(type: String, json: String = "{}") =
        hud.apply(type, JSONObject(json), now = 1_000L)

    private fun inputs(
        linkUp: Boolean = true,
        warming: Boolean = false,
        speaking: Boolean = false,
        listening: Boolean = false,
        kill: Boolean = false,
    ) = ReactorFeed.inputsFrom(hud, linkUp, warming, speaking, listening, kill)

    @Test
    fun `a quiet hud is idle and asks for nothing`() {
        val i = inputs()
        assertFalse(i.kill)
        assertTrue(i.linkUp)
        assertFalse(i.adapterError)
        assertFalse(i.turnRunning)
        assertFalse(i.errorEvent)
        assertFalse(i.attention)
        assertEquals(0, i.toolsRunning)
        assertFalse(i.goalActive)
        assertEquals(ReactorState.IDLE, derive(i).state)
    }

    @Test
    fun `a started turn is a running turn`() {
        hud.turnStarted()
        assertTrue(inputs().turnRunning)
        assertEquals(ReactorState.THINKING, derive(inputs()).state)
    }

    @Test
    fun `an adapter error is not a link failure`() {
        // The distinction the contract insists on, and the one that was got
        // wrong on the web: the socket is up, and the adapter is telling us
        // through it that it cannot reach Hermes. Collapsing the two makes
        // §10's alert row unreachable, because an adapter error mid-turn could
        // only ever read as "offline".
        ev("adapter.error", """{"error":"backend unavailable"}""")
        val i = inputs(linkUp = true)
        assertTrue("adapter.error is an adapter error", i.adapterError)
        assertTrue("the socket is still up", i.linkUp)
        assertTrue("and it is an error the person should see", i.errorEvent)
    }

    @Test
    fun `a link that is down is reported without inventing an adapter error`() {
        assertFalse(inputs(linkUp = false).adapterError)
        assertEquals(ReactorState.OFFLINE, derive(inputs(linkUp = false)).state)
    }

    @Test
    fun `the error message is the fact, and a fresh turn clears it`() {
        ev("error", """{"error":"the model refused"}""")
        assertTrue(inputs().errorEvent)
        // Any other event clears `offline`, and a new turn clears the error —
        // that is HudState's own rule, and the feed must not outlive it.
        hud.turnStarted()
        assertFalse("a new turn starts afresh", inputs().errorEvent)
    }

    // The payload key is `request_id`, not `id` — `HudState` drops an approval
    // whose `request_id` is empty, so a test using the wrong key asserts
    // against an approval that was never stored. This suite caught exactly
    // that on its first run, which is the argument for driving real events.

    @Test
    fun `a waiting approval is attention`() {
        ev("approval.request", """{"request_id":"a1","tool":"shell","command":"rm -rf /tmp/x"}""")
        val i = inputs()
        assertTrue(i.attention)
        assertEquals(ReactorState.ATTENTION, derive(i).state)
    }

    @Test
    fun `a clarifying question is attention too`() {
        ev("clarify.request", """{"request_id":"c1","question":"which study?"}""")
        assertTrue(inputs().attention)
    }

    @Test
    fun `an answered approval stops asking`() {
        ev("approval.request", """{"request_id":"a1","tool":"shell","command":"x"}""")
        assertTrue(inputs().attention)
        assertTrue(hud.approvalAnswered("a1"))
        assertFalse("answering it is the end of it", inputs().attention)
    }

    @Test
    fun `running tools are counted, not merely noticed`() {
        // `ToolRun` already exposes a `running` boolean; §10 lights one ring
        // segment per tool, so the count is the thing and the boolean is not.
        ev("tool.start", """{"tool_id":"t1","name":"read_file","round":1,"index":0}""")
        ev("tool.start", """{"tool_id":"t2","name":"web_search","round":1,"index":1}""")
        assertEquals(2, inputs().toolsRunning)
        ev("tool.complete", """{"tool_id":"t1","ok":true,"round":1,"index":0}""")
        assertEquals("a finished tool is not a running one", 1, inputs().toolsRunning)
    }

    @Test
    fun `a goal that is under way is active, and one that is not is not`() {
        // Named states rather than excluded ones, so a state added to
        // GoalStatus defaults to "not running" instead of lighting the reactor.
        ev("status.update", """{"kind":"goal","text":"⊙ tidy the notes"}""")
        assertTrue("SET is under way", inputs().goalActive)

        ev("status.update", """{"kind":"goal","text":"↻ tidy the notes 3/20"}""")
        assertTrue("CONTINUING is under way", inputs().goalActive)

        ev("status.update", """{"kind":"goal","text":"⏸ tidy the notes"}""")
        assertFalse("a paused goal is not running", inputs().goalActive)

        ev("status.update", """{"kind":"goal","text":"✓ tidy the notes"}""")
        assertFalse("an achieved goal is not running", inputs().goalActive)
    }

    @Test
    fun `a parked goal is both the goal and the thing asking`() {
        // The one deliberate overlap: PARKED sets `attention` and
        // `goalActive` at once. The eleven-state table ranks attention above
        // goal, so it reads as needing the person rather than as running —
        // which is the whole point of parking it.
        ev("status.update", """{"kind":"goal","text":"⏳ waiting on you"}""")
        val i = inputs()
        assertTrue(i.attention)
        assertTrue(i.goalActive)
        assertEquals(ReactorState.ATTENTION, derive(i).state)
    }

    @Test
    fun `the four flags the conversation owns are passed through, not guessed`() {
        assertTrue(inputs(warming = true).warming)
        assertTrue(inputs(speaking = true).speaking)
        assertTrue(inputs(listening = true).listening)
        assertTrue(inputs(kill = true).kill)
        // And they are not read off the hud, which knows nothing about any of
        // them: a hud with no events still reports whatever it was told.
        assertFalse(inputs().warming)
        assertFalse(inputs().speaking)
    }

    @Test
    fun `kill is false until Phase 7 gives it a source`() {
        // A parameter rather than a literal, so the day it exists is a
        // call-site change; but nothing on this surface sets it yet, and a
        // default that lied would dark the reactor on every screen.
        assertFalse(ReactorFeed.inputsFrom(hud).kill)
    }

    @Test
    fun `speaking outranks a running tool, and attention outranks both`() {
        // Not the feed's own rule — `derive` owns the ranking — but the feed
        // is what supplies the collisions, so this is where a mapping that
        // populated the wrong field would show up as the wrong ring.
        ev("tool.start", """{"tool_id":"t1","name":"read_file","round":1,"index":0}""")
        hud.turnStarted()
        assertEquals(ReactorState.SPEAKING, derive(inputs(speaking = true)).state)

        ev("approval.request", """{"request_id":"a1","tool":"shell","command":"x"}""")
        assertEquals(ReactorState.ATTENTION, derive(inputs(speaking = true)).state)
    }

    @Test
    fun `a phase alone maps onto the four inputs it can speak for`() {
        // The pipeline engine and the View surfaces have no HUD state: the
        // phase is all they know, and each phase lights exactly its own ring.
        assertEquals(ReactorState.IDLE, derive(ReactorFeed.inputsFrom(Phase.IDLE)).state)
        assertEquals(ReactorState.LISTENING, derive(ReactorFeed.inputsFrom(Phase.LISTENING)).state)
        assertEquals(ReactorState.THINKING, derive(ReactorFeed.inputsFrom(Phase.THINKING)).state)
        assertEquals(ReactorState.SPEAKING, derive(ReactorFeed.inputsFrom(Phase.SPEAKING)).state)
        assertEquals(ReactorState.ALERT, derive(ReactorFeed.inputsFrom(Phase.ERROR)).state)
    }

    @Test
    fun `a phase does not outrank the link or the model`() {
        assertEquals(ReactorState.OFFLINE, derive(ReactorFeed.inputsFrom(Phase.LISTENING, linkUp = false)).state)
        assertEquals(ReactorState.WARMING, derive(ReactorFeed.inputsFrom(Phase.THINKING, warming = true)).state)
        // ...except that a phase never invents a goal, a tool or an approval.
        val i = ReactorFeed.inputsFrom(Phase.SPEAKING)
        assertFalse(i.goalActive)
        assertFalse(i.attention)
        assertEquals(0, i.toolsRunning)
    }

    @Test
    fun `the contract's event sequences, through the phone's own reducer`() {
        // `tests/contracts/reactor.json` `derivation.sequences`: the console and the terminal
        // fold the same events. The truth table could not see this fault, where the precedence
        // was right and the reducer handed it the adapter's stale error (T-1483).
        val file = File("../../tests/contracts/reactor.json")
        assertTrue("contract not found at ${file.absolutePath}", file.isFile)
        val cases = JSONObject(file.readText())
            .getJSONObject("derivation").getJSONObject("sequences").getJSONArray("cases")
        assertTrue("an emptied table is not a pass", cases.length() > 2)
        for (n in 0 until cases.length()) {
            val case = cases.getJSONObject(n)
            val fresh = HudState()
            val events = case.getJSONArray("events")
            for (e in 0 until events.length()) {
                val event = events.getJSONObject(e)
                fresh.apply(event.getString("type"), event.optJSONObject("payload") ?: JSONObject(), now = 1_000L + e)
            }
            val state = derive(ReactorFeed.inputsFrom(fresh, true, false, false, false, false)).state
            assertEquals(case.getString("name"), case.getString("state"), state.key)
        }
    }
}
