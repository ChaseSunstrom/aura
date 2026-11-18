package ai.aura.app.car.holo

import ai.aura.app.car.holo.CarCard.Kind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The one card slot's holder: who is live, who wins, and that a card leaves with its source. */
class CarCardsTest {

    private val shown = mutableListOf<CarCard?>()
    private val taps = mutableListOf<String>()
    private val cards = CarCards { card, _ -> shown += card }

    private fun card(kind: Kind, title: String = kind.name) = CarCard(kind, "cap", title)

    @Test
    fun `a card set is shown and a card cleared leaves`() {
        val music = card(Kind.MUSIC)
        cards.set(music)
        assertEquals(listOf<CarCard?>(music), shown)
        assertEquals(music, cards.current)

        cards.clear(Kind.MUSIC)
        assertEquals(listOf(music, null), shown)
        assertNull(cards.current)
    }

    @Test
    fun `the most urgent live card has the slot and the next one takes it when that source ends`() {
        val music = card(Kind.MUSIC)
        val call = card(Kind.CALL)
        val approval = card(Kind.APPROVAL)
        val message = card(Kind.MESSAGE)
        cards.set(music)
        cards.set(message)
        cards.set(approval)
        cards.set(call)
        assertEquals(call, cards.current)

        cards.clear(Kind.CALL)
        assertEquals(approval, cards.current)
        cards.clear(Kind.APPROVAL)
        assertEquals(message, cards.current)
        cards.clear(Kind.MESSAGE)
        assertEquals(music, cards.current)
        cards.clear(Kind.MUSIC)
        assertNull(cards.current)
        assertEquals(listOf(music, message, approval, call, approval, message, music, null), shown)
    }

    @Test
    fun `a change that leaves the slot as it was is not pushed again`() {
        val call = card(Kind.CALL)
        cards.set(call)
        // A lesser card arriving or leaving behind the call changes nothing the driver sees.
        cards.set(card(Kind.MUSIC))
        cards.clear(Kind.MUSIC)
        cards.clear(Kind.MESSAGE)
        // The same card again (a source that re-reads on a timer) is not a change either.
        cards.set(call.copy())
        assertEquals(listOf<CarCard?>(call), shown)
    }

    @Test
    fun `a card of the same kind with new words replaces the old one`() {
        cards.set(card(Kind.MESSAGE, "Sam"))
        cards.set(card(Kind.MESSAGE, "Alex"))
        assertEquals(listOf("Sam", "Alex"), shown.map { it?.title })
    }

    @Test
    fun `an action goes to the handler of the card that is showing, not one behind it`() {
        val answer = CarAction("answer", "Answer")
        cards.set(card(Kind.MUSIC)) { taps += "music:${it.id}" }
        cards.set(CarCard(Kind.CALL, "c", "t", actions = listOf(answer))) { taps += "call:${it.id}" }

        cards.act(answer)
        cards.clear(Kind.CALL)
        cards.act(CarAction("pause", "Pause"))

        assertEquals(listOf("call:answer", "music:pause"), taps)
    }

    @Test
    fun `a card behind another hears it has the slot only when it gets there, and when it loses it`() {
        // T-0663: an approval behind a call counted its 30 s down unseen and was refused.
        val heard = mutableListOf<Boolean>()
        cards.set(card(Kind.CALL))
        cards.set(card(Kind.APPROVAL), onShown = { heard += it })
        assertEquals(emptyList<Boolean>(), heard)

        cards.clear(Kind.CALL)
        assertEquals(listOf(true), heard)
        // The same words again while it shows is not a second arrival.
        cards.set(card(Kind.MUSIC))
        assertEquals(listOf(true), heard)

        cards.set(card(Kind.CALL))
        cards.clear(Kind.CALL)
        assertEquals(listOf(true, false, true), heard)
        cards.clear(Kind.APPROVAL)
        assertEquals(listOf(true, false, true, false), heard)
    }

    @Test
    fun `a card shown at once hears it, and one replaced by its own kind hears it lost the slot`() {
        val first = mutableListOf<Boolean>()
        val second = mutableListOf<Boolean>()
        cards.set(card(Kind.APPROVAL, "first"), onShown = { first += it })
        cards.set(card(Kind.APPROVAL, "second"), onShown = { second += it })
        assertEquals(listOf(true, false), first)
        assertEquals(listOf(true), second)
        cards.clearAll()
        assertEquals(listOf(true, false), second)
    }

    @Test
    fun `clearing a kind nobody set, or acting on an empty slot, does nothing`() {
        cards.clear(Kind.HOME)
        cards.act(CarAction("x", "X"))
        assertEquals(emptyList<CarCard?>(), shown)
        assertEquals(emptyList<String>(), taps)
    }

    @Test
    fun `clearAll empties the slot once, as a stopped screen does`() {
        cards.set(card(Kind.CALL))
        cards.set(card(Kind.MUSIC))
        cards.clearAll()
        cards.clearAll()
        assertNull(cards.current)
        assertEquals(2, shown.size)
        assertNull(shown.last())
    }
}
