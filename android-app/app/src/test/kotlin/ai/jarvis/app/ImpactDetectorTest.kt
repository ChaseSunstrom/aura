package ai.jarvis.app

import ai.jarvis.app.safety.ImpactDetector
import ai.jarvis.app.safety.ImpactThresholds
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Made-up traces, 50 ms apart, through [ImpactDetector]. The numbers are guesses about real
 * phones (see [ImpactThresholds]); these tests pin the shape, not the tuning.
 */
class ImpactDetectorTest {
    private val g = 9.81f

    /** Counts signals over a list of (seconds, magnitude) samples. */
    private fun signals(d: ImpactDetector, trace: List<Pair<Double, Float>>): Int =
        trace.count { (s, m) -> d.feed((s * 1000).toLong(), m) }

    /** A steady [mag] (with a little deterministic wobble of [wobble]) from [from] to [to] seconds. */
    private fun hold(from: Double, to: Double, mag: Float = g, wobble: Float = 0.3f): List<Pair<Double, Float>> {
        val out = mutableListOf<Pair<Double, Float>>()
        var s = from
        var i = 0
        while (s < to) { out += s to mag + if (i++ % 2 == 0) wobble else -wobble; s += 0.05 }
        return out
    }

    @Test fun `a drop, a bounce and ten still seconds are exactly one signal`() {
        val trace = hold(0.0, 5.0, g, 2f) + listOf(5.0 to 60f, 5.05 to 25f, 5.1 to 3f, 5.15 to 20f) +
            hold(5.2, 25.0)
        assertEquals(1, signals(ImpactDetector(), trace))
    }

    @Test fun `a drop and a second one later, each followed by stillness, are two signals`() {
        val trace = listOf(0.0 to 60f) + hold(0.1, 14.0) + listOf(14.0 to 55f) + hold(14.1, 28.0)
        assertEquals(2, signals(ImpactDetector(), trace))
    }

    @Test fun `a pocketed walk gives none`() {
        // 2 Hz steps peaking near 2 g on top of gravity, for two minutes.
        val trace = (0 until 2400).map { i ->
            val s = i * 0.05
            s to (g + 10f * kotlin.math.abs(kotlin.math.sin(Math.PI * 2 * s)).toFloat())
        }
        assertEquals(0, signals(ImpactDetector(), trace))
    }

    @Test fun `a hard set-down that is picked up again gives none`() {
        // Set down hard, still for 4 s, then lifted and carried: motion ends the stillness.
        val trace = hold(0.0, 3.0, g, 2f) + listOf(3.0 to 45f) + hold(3.05, 7.0) + hold(7.0, 40.0, g + 4f, 4f)
        assertEquals(0, signals(ImpactDetector(), trace))
    }

    @Test fun `a hard car brake gives none`() {
        // About 1 g of braking, held, then easing off and driving on over a bumpy road.
        val trace = hold(0.0, 2.0, g + 1f, 0.5f) + hold(2.0, 5.0, g + 10f, 1f) + hold(5.0, 60.0, g, 3f)
        assertEquals(0, signals(ImpactDetector(), trace))
    }

    @Test fun `stillness that never comes before the window closes gives none, and a late quiet is not a fall`() {
        // Impact, then restless for 40 s (past the 30 s watch), then still for a minute.
        val trace = listOf(0.0 to 60f) + hold(0.1, 40.0, g, 5f) + hold(40.0, 100.0)
        assertEquals(0, signals(ImpactDetector(), trace))
    }

    @Test fun `a sensor that stalls mid-stillness does not count the silence as stillness`() {
        // Impact, 6 s still, a 20 s silence, then 3 s still: never 10 s of seen stillness.
        val trace = listOf(0.0 to 60f) + hold(1.5, 7.5) + hold(27.5, 30.5)
        assertEquals(0, signals(ImpactDetector(), trace))
    }

    @Test fun `thresholds are parameters`() {
        val trace = listOf(0.0 to 30f) + hold(0.1, 8.0)
        assertEquals("the default 35 m/s² impact ignores a 30", 0, signals(ImpactDetector(), trace))
        val touchy = ImpactThresholds(impactMs2 = 25f, stillMs = 5_000L)
        assertEquals("a touchier impact and a shorter stillness fire on the same trace", 1, signals(ImpactDetector(touchy), trace))
    }
}
