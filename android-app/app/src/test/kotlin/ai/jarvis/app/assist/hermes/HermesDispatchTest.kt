package ai.jarvis.app.assist.hermes

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** `command.dispatch` result types and the goal trigger phrases, from the protocol doc. */
class HermesDispatchTest {

    @Test
    fun `send carries the notice and the message the client must submit`() {
        val outcome = HermesDispatch.interpret(
            JSONObject("""{"type":"send","notice":"⊙ Goal set (20-turn budget): tidy","message":"tidy the notes"}""")
        )
        assertEquals(HermesDispatch.Outcome.Send("⊙ Goal set (20-turn budget): tidy", "tidy the notes"), outcome)
    }

    @Test
    fun `exec is output to display`() {
        assertEquals(
            HermesDispatch.Outcome.Exec("✓ Goal cleared."),
            HermesDispatch.interpret(JSONObject("""{"type":"exec","output":"✓ Goal cleared."}""")),
        )
    }

    @Test
    fun `a plugin command's reply is output to display, like exec`() {
        assertEquals(
            HermesDispatch.Outcome.Exec("Subgoal 1 added."),
            HermesDispatch.interpret(JSONObject("""{"type":"plugin","output":"Subgoal 1 added."}""")),
        )
    }

    @Test
    fun `plugin commands carry the session in the arg, built-ins do not`() {
        assertEquals("@s-1 draft build a widget", HermesDispatch.argFor("jgoal", "draft build a widget", "s-1"))
        assertEquals("@s-1 subgoal add a regression test", HermesDispatch.argFor("jgoal", "subgoal add a regression test", "s-1"))
        assertEquals("@s-1 gate add ./gradlew test", HermesDispatch.argFor("jgoal", "gate add ./gradlew test", "s-1"))
        // Idempotent, and nothing to prefix with before a session exists.
        assertEquals("@s-1 show", HermesDispatch.argFor("jgoal", "@s-1 show", "s-1"))
        assertEquals("show", HermesDispatch.argFor("jgoal", "show", null))
        assertEquals("pause", HermesDispatch.argFor("goal", "pause", "s-1"))
        assertEquals("tidy the notes", HermesDispatch.argFor("goal", "tidy the notes", "s-1"))
        assertEquals(setOf("jgoal"), HermesDispatch.PLUGIN_COMMANDS)
    }

    @Test
    fun `prefill fills the composer and sends nothing`() {
        assertEquals(
            HermesDispatch.Outcome.Prefill("draft text"),
            HermesDispatch.interpret(JSONObject("""{"type":"prefill","message":"draft text"}""")),
        )
    }

    @Test
    fun `an error result and an unknown type are both errors to show`() {
        assertEquals(
            HermesDispatch.Outcome.Error("nope"),
            HermesDispatch.interpret(JSONObject("""{"type":"error","message":"nope"}""")),
        )
        assertTrue(HermesDispatch.interpret(JSONObject("""{"type":"dance"}""")) is HermesDispatch.Outcome.Error)
        assertTrue(HermesDispatch.interpret(JSONObject("{}")) is HermesDispatch.Outcome.Error)
    }

    @Test
    fun `4018 names the missing plugin and the command that needs it`() {
        val error = HermesDispatch.interpretError(4018, "unknown command", "jgoal")
        assertTrue(error.message, error.message.contains("4018"))
        assertTrue(error.message, error.message.contains("jgoal"))
        assertTrue(error.message, error.message.contains("aura-goals"))
    }

    @Test
    fun `any other code is shown with its number`() {
        val error = HermesDispatch.interpretError(4001, "no active session", "goal")
        assertEquals("goal failed (error 4001): no active session", error.message)
    }

    @Test
    fun `every trigger phrase in the doc is a goal`() {
        for (phrase in listOf("keep working on", "keep going until", "don't stop until", "get this done", "set a goal")) {
            assertTrue(phrase, HermesDispatch.isGoal("$phrase the kitchen is clean"))
            assertTrue(phrase, HermesDispatch.isGoal("  ${phrase.uppercase()} it"))
        }
        assertEquals(5, HermesDispatch.GOAL_TRIGGERS.size)
    }

    @Test
    fun `a curly apostrophe from the recogniser still matches`() {
        assertTrue(HermesDispatch.isGoal("Don’t stop until the tests pass"))
    }

    @Test
    fun `an ordinary sentence is a prompt`() {
        assertFalse(HermesDispatch.isGoal("what's the weather"))
        assertFalse(HermesDispatch.isGoal("I want you to keep working on it"))
        assertFalse(HermesDispatch.isGoal(""))
    }
}
