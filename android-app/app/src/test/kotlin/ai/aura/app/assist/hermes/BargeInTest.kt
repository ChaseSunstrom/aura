package ai.aura.app.assist.hermes

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * The owner, 2026-09-25: "it hears itself ... so it cancels mid speech". The rule and
 * its scenarios are `tests/contracts/barge-in.json`, which the console's
 * `bargeIn.test.ts` runs too: one rule on the phone and in the browser.
 */
class BargeInTest {
    private val contract = JSONObject(File("../../tests/contracts/barge-in.json").readText())

    @Test
    fun `the constants are the contract's`() {
        val c = contract.getJSONObject("constants")
        assertEquals(c.getLong("learn_ms"), BargeIn.LEARN_MS)
        assertEquals(c.getLong("hold_ms"), BargeIn.HOLD_MS)
        assertEquals(c.getDouble("over_echo").toFloat(), BargeIn.OVER_ECHO)
        assertEquals(c.getDouble("min_level").toFloat(), BargeIn.MIN_LEVEL)
        assertEquals(c.getDouble("floor_ratio").toFloat(), BargeIn.FLOOR_RATIO)
        assertEquals(c.getDouble("echo_decay").toFloat(), BargeIn.ECHO_DECAY)
    }

    @Test
    fun `every scenario in the contract`() {
        val step = contract.getLong("step_ms")
        val floor = contract.getDouble("floor").toFloat()
        val scenarios = contract.getJSONArray("scenarios")
        for (i in 0 until scenarios.length()) {
            val scenario = scenarios.getJSONObject(i)
            val b = BargeIn().apply { reset(0) }
            val spans = scenario.getJSONArray("spans")
            for (j in 0 until spans.length()) {
                val span = spans.getJSONArray(j)
                var heard = false
                var t = span.getLong(0)
                while (t < span.getLong(1)) { heard = b.hear(t, span.getDouble(2).toFloat(), floor) || heard; t += step }
                assertEquals("${scenario.getString("name")}: span $j", span.getBoolean(3), heard)
            }
        }
    }
}
