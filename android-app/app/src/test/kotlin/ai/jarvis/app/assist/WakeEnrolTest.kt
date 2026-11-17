package ai.jarvis.app.assist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/** From recorded takes to a verifier (T-0714): the windows kept, the takes refused, leave-one-out. */
class WakeEnrolTest {
    private val rng = Random(7)

    /** A made-up 8-dim window: the owner's takes cluster around +1, the room around -1. */
    private fun window(centre: Float) = FloatArray(8) { centre + (rng.nextFloat() - 0.5f) * 0.6f }

    @Test
    fun `a take keeps its best windows, and one the head never half-heard is refused`() {
        val steps = listOf(0.02f, 0.3f, 0.7f, 0.05f, 0.4f).map { it to window(1f) }
        val take = WakeEnrol.take(steps)!!
        assertEquals(0.7f, take.peak)
        assertEquals(WakeEnrol.TOP, take.windows.size)
        assertTrue(take.windows[0] === steps[2].second)
        // Under the gate the lift never asks the verifier, so the take is not learnt.
        assertNull(WakeEnrol.take(listOf(0.02f, 0.09f).map { it to window(1f) }))
        assertNull(WakeEnrol.take(emptyList()))
        // Only the windows at or over the gate: a take heard in one step keeps one.
        assertEquals(1, WakeEnrol.take(listOf(0.2f, 0.01f, 0.0f).map { it to window(1f) })!!.windows.size)
    }

    @Test
    fun `the fitted verifier recognises the takes it was not fitted on, and not the room`() {
        val takes = List(6) { WakeEnrol.take(listOf(0.3f, 0.2f, 0.15f).map { it to window(1f) })!! }
        val room = List(40) { window(-1f) }
        val result = WakeEnrol.fit(takes, room)
        assertEquals(6, result.of)
        assertEquals(6, result.recognised)
        assertTrue(result.verifier.probability(window(1f)) > 0.5f)
        assertTrue(result.verifier.probability(window(-1f)) < 0.5f)
    }
}
