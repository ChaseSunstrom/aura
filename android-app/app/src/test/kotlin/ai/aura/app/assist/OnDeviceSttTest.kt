package ai.aura.app.assist

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File

/** The cases are `tests/contracts/offline-voice.json` ("listen"), which the Python mirror runs too. */
class OnDeviceSttTest {
    private val listen = JSONObject(File("../../tests/contracts/offline-voice.json").readText())
        .getJSONObject("listen")

    @Test fun `every case in the table`() {
        val cases = listen.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val got = OnDeviceStt.choose(
                audioInHand = c.getBoolean("audioInHand"),
                serverReachable = c.getBoolean("serverReachable"),
                bundledModel = c.getBoolean("bundledModel"),
                platformRecognizer = c.getBoolean("platformRecognizer"),
                gateEnforcing = c.getBoolean("gateEnforcing"),
            )
            assertEquals(c.getString("name"), c.getString("expect"), got.name.lowercase())
        }
    }

    @Test fun `a hearer that cannot hear says so, in the table's words`() {
        assertEquals(listen.getString("none_says"), OnDeviceStt.says(OnDeviceStt.Hearer.NONE))
        assertEquals(listen.getString("gate_says"), OnDeviceStt.says(OnDeviceStt.Hearer.GATE))
        assertNull(OnDeviceStt.says(OnDeviceStt.Hearer.PLATFORM))
        assertNull(OnDeviceStt.says(OnDeviceStt.Hearer.BUNDLED))
    }
}
