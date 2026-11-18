package ai.aura.app.car.holo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * What the driver says to the drive itself (T-0613): a card's action by its label or the
 * words people use for it, and "reroute" / "next turn". Anything else, or anything more,
 * is a question for Jarvis.
 */
class DriveWordsTest {

    private val call = listOf(
        CarAction("answer", "Answer", primary = true),
        CarAction("decline", "Decline", primary = false),
        CarAction("driving", "I'm driving", primary = false),
    )
    private val approval = listOf(
        CarAction("allow", "Allow once", primary = true),
        CarAction("deny", "Deny", primary = false),
    )

    private fun id(text: String, actions: List<CarAction> = call) = DriveWords.action(text, actions)?.id

    @Test
    fun `an action is said by its label, however the recogniser wrote it`() {
        assertEquals("answer", id("Answer"))
        assertEquals("answer", id(" Answer."))
        assertEquals("driving", id("I'm driving."))
        assertEquals("driving", id("I’m driving"))
        assertEquals("driving", id("Jarvis, I'm driving!"))
        assertEquals("decline", id("Decline, please."))
        assertEquals("allow", id("Allow once", approval))
    }

    @Test
    fun `and by the words people use for it`() {
        assertEquals("answer", id("Pick up"))
        assertEquals("answer", id("Hey Jarvis, answer the call"))
        assertEquals("decline", id("Hang up"))
        assertEquals("decline", id("not now"))
        assertEquals("driving", id("Tell them I'm driving"))
        assertEquals("allow", id("Yes", approval))
        assertEquals("deny", id("No.", approval))
    }

    @Test
    fun `an action that is not on the card is not taken`() {
        assertNull(id("Yes"))
        assertNull(id("Answer", approval))
        assertNull(id("Answer", emptyList()))
    }

    @Test
    fun `a longer sentence is a question for Jarvis, not a button`() {
        assertNull(id("Don't answer"))
        assertNull(id("Answer and tell them I'll be late"))
        assertNull(id("what's the weather"))
        assertNull(id(""))
    }

    @Test
    fun `reroute and the next turn are the drive's own`() {
        assertEquals(DriveWords.Command.REROUTE, DriveWords.command("Reroute"))
        assertEquals(DriveWords.Command.REROUTE, DriveWords.command("Re-route."))
        assertEquals(DriveWords.Command.REROUTE, DriveWords.command("Jarvis, find another route"))
        assertEquals(DriveWords.Command.NEXT_TURN, DriveWords.command("What's the next turn?"))
        assertEquals(DriveWords.Command.NEXT_TURN, DriveWords.command("where do I turn"))
        assertNull("a route through somewhere is Jarvis's to plan", DriveWords.command("Reroute via the bridge"))
        assertNull(DriveWords.command("navigate home"))
    }
}
