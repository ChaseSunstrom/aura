package ai.aura.app.car.voice

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The owner's drive, 2026-11-11: "it told me to turn or whatever way after I already had". */
@OptIn(ExperimentalCoroutinesApi::class)
class PreparedLinesTest {
    private val heard = mutableListOf<String>()
    private var renders = 0
    private val play: suspend (ByteArray, Int) -> Boolean = { pcm, rate -> heard += "house:${pcm.size}@$rate"; true }
    private val phone: suspend (String) -> Unit = { heard += "phone:$it" }

    @Test fun `a prepared turn is said from the phone's memory with no call to the house`() = runTest {
        val kept = PreparedLines()
        kept.put("aura\nTurn left onto Main Street", 16_000, ByteArray(320))
        val from = kept.say("aura\nTurn left onto Main Street", "Turn left onto Main Street", play, phone) {
            renders++
            async { null }
        }
        assertEquals("kept", from)
        assertEquals(listOf("house:320@16000"), heard)
        assertEquals(0, renders)
    }

    @Test fun `a render slower than the deadline gives way to the phone, and is kept for the next time`() = runTest {
        val kept = PreparedLines()
        val slow = { renders++; async { delay(20_000); 16_000 to ByteArray(640) } }
        assertEquals("phone", kept.say("k", "Slow down", play, phone, render = slow))
        assertEquals(listOf("phone:Slow down"), heard)
        advanceUntilIdle()
        assertEquals("kept", kept.say("k", "Slow down", play, phone, render = slow))
        assertEquals(listOf("phone:Slow down", "house:640@16000"), heard)
        assertEquals(1, renders)
    }

    @Test fun `a render in time is played and kept, and one that fails goes to the phone`() = runTest {
        val kept = PreparedLines()
        assertEquals("house", kept.say("a", "Route cancelled.", play, phone) { async { 24_000 to ByteArray(8) } })
        assertEquals("phone", kept.say("b", "Other", play, phone) { async { null } })
        assertEquals(listOf("house:8@24000", "phone:Other"), heard)
        assertEquals(24_000, kept.get("a")?.first)
        assertNull(kept.get("b"))
    }

    @Test fun `only the newest few lines are held, in memory`() {
        val kept = PreparedLines(max = 2)
        kept.put("one", 1, ByteArray(1))
        kept.put("two", 1, ByteArray(1))
        kept.get("one")
        kept.put("three", 1, ByteArray(1))
        assertNull(kept.get("two"))
        assertEquals(1, kept.get("one")?.first)
    }
}
