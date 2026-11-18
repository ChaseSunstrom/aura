package ai.aura.app.automation.actions.builtin

import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.TimeZone

/** "When am I free" is arithmetic over the instances table; pin the edges. */
class FreeSlotsTest {

    private val utc = TimeZone.getTimeZone("UTC")
    private val h = 3_600_000L
    private val day0 = 1_775_001_600_000L - 1_775_001_600_000L % (24 * h) // a UTC midnight

    private fun slots(busy: List<LongRange>, days: Int = 1, min: Long = 30 * 60_000L) =
        freeSlots(busy, day0, day0 + days * 24 * h, min, 8, 20, utc).map { (it.first - day0) / h to (it.last + 1 - day0) / h }

    @Test
    fun `an empty day is one waking-hours slot`() {
        assertEquals(listOf(8L to 20L), slots(emptyList()))
    }

    @Test
    fun `a meeting splits the day`() {
        assertEquals(listOf(8L to 10L, 11L to 20L), slots(listOf(day0 + 10 * h..day0 + 11 * h)))
    }

    @Test
    fun `nested and overlapping spans merge`() {
        val busy = listOf(day0 + 9 * h..day0 + 14 * h, day0 + 10 * h..day0 + 11 * h, day0 + 13 * h..day0 + 15 * h)
        assertEquals(listOf(8L to 9L, 15L to 20L), slots(busy))
    }

    @Test
    fun `a span across midnight takes the next morning`() {
        val busy = listOf(day0 + 22 * h..day0 + 33 * h) // until 09:00 next day
        assertEquals(listOf(8L to 20L, 33L to 44L), slots(busy, days = 2))
    }

    @Test
    fun `gaps shorter than the minimum are dropped`() {
        val busy = listOf(day0 + 8 * h..day0 + 10 * h, day0 + 10 * h + 15 * 60_000L..day0 + 20 * h)
        assertEquals(emptyList<Pair<Long, Long>>(), slots(busy))
    }
}
