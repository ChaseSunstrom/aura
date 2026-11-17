package ai.jarvis.app.car.link

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.Closeable

class PokeSlotTest {
    private class Sock : Closeable { var closed = false; override fun close() { closed = true } }

    @Test fun `an accepted socket waits for the next session and is taken once`() {
        val slot = PokeSlot<Sock>()
        val s = Sock()
        slot.offer("AA", s)
        assertEquals(s, slot.take("AA"))
        assertNull(slot.take("AA"))
    }

    @Test fun `a newer poke from the same car closes the older socket`() {
        val slot = PokeSlot<Sock>()
        val old = Sock(); val new = Sock()
        slot.offer("AA", old); slot.offer("AA", new)
        assertTrue(old.closed)
        assertEquals(new, slot.take("AA"))
    }

    @Test fun `another car's socket is never handed to this one`() {
        val slot = PokeSlot<Sock>()
        slot.offer("BB", Sock())
        assertNull(slot.take("AA"))
    }

    @Test fun `clearing closes everything waiting`() {
        val slot = PokeSlot<Sock>()
        val a = Sock(); val b = Sock()
        slot.offer("AA", a); slot.offer("BB", b)
        slot.clear()
        assertTrue(a.closed && b.closed)
        assertNull(slot.take("AA"))
    }
}
