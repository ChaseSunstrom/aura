package ai.jarvis.app.ui

import ai.jarvis.app.assist.hermes.AskWhy
import ai.jarvis.app.assist.hermes.AskWhyQueue
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * "Ask why" is a deny, then a line the model reads. The sentence, the template and the cap are
 * `tests/contracts/approval-event.json` `ask_why`, which the console's suite reads too.
 */
class ApprovalAskWhyTest {
    private val spec = JSONObject(File("../../tests/contracts/approval-event.json").readText())
        .getJSONObject("ask_why")

    private fun line(what: String) =
        spec.getString("template").replace("{what}", what).replace("{why}", spec.getString("sentence"))

    @Test fun `the phone's line is the contract's`() {
        assertEquals(spec.getString("sentence"), AskWhy.SENTENCE)
        assertEquals(spec.getInt("what_cap"), AskWhy.WHAT_CAP)
        assertEquals(line("Send mail to a@example.test?"), AskWhy.line("Send mail to a@example.test?"))
    }

    @Test fun `a long headline is cut to the cap and a padded one is trimmed`() {
        val long = "x".repeat(spec.getInt("what_cap") + 50)
        assertEquals(line("x".repeat(spec.getInt("what_cap"))), AskWhy.line(long))
        assertEquals(line("Delete it?"), AskWhy.line("  Delete it?  "))
    }

    // --- who is offered it ----------------------------------------------------------

    @Test fun `offered only on a Hermes approval, and enabled exactly when Deny is`() {
        for (answered in listOf(true, false)) {
            assertEquals(ConsentGate.denyEnabled(answered), ConsentGate.askWhyEnabled(true, answered))
            assertFalse("the phone's own ask never offers it", ConsentGate.askWhyEnabled(false, answered))
        }
    }

    // --- when the line goes out -----------------------------------------------------

    private var clock = 0L
    private val queue = AskWhyQueue { clock }
    private val sent = mutableListOf<String>()

    @Test fun `with no turn running the line goes out at once, once`() {
        queue.submit("L", "s1", turnRunning = false) { sent += it }
        queue.turnEnded("s1") { sent += it }
        assertEquals(listOf("L"), sent)
    }

    @Test fun `with a turn running it waits for the turn and goes out once`() {
        queue.submit("L", "s1", turnRunning = true) { sent += it }
        assertTrue(sent.isEmpty())
        queue.turnEnded("s1") { sent += it }
        queue.turnEnded("s1") { sent += it }
        assertEquals(listOf("L"), sent)
    }

    @Test fun `a closed link, a changed session or sixty seconds drop it silently`() {
        queue.submit("L", "s1", true) { sent += it }
        queue.drop()
        queue.turnEnded("s1") { sent += it }

        queue.submit("L", "s1", true) { sent += it }
        queue.turnEnded("s2") { sent += it }

        queue.submit("L", "s1", true) { sent += it }
        clock += AskWhyQueue.HOLD_MS + 1
        queue.turnEnded("s1") { sent += it }

        queue.submit("L", null, true) { sent += it }
        queue.turnEnded(null) { sent += it }
        assertTrue(sent.toString(), sent.isEmpty())
    }

    @Test fun `a newer ask replaces the waiting one`() {
        queue.submit("old", "s1", true) { sent += it }
        queue.submit("new", "s1", true) { sent += it }
        queue.turnEnded("s1") { sent += it }
        assertEquals(listOf("new"), sent)
    }
}
