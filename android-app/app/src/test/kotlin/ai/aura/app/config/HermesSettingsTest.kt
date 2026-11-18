package ai.aura.app.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File

/** The console's settings, read by the phone's native screen (`SettingsActivity`'s Jarvis section). */
@RunWith(RobolectricTestRunner::class)
class HermesSettingsTest {

    /** The generated contract itself, as the build bundles it. */
    private val contract = File("../../tests/contracts/hermes-settings.json").readText()

    @Test
    fun `only the settable leaves are offered, each in a group the contract names`() {
        val (groups, specs) = HermesSettings.contract(contract)
        assertTrue(specs.isNotEmpty())
        val groupIds = groups.map { it.id }.toSet()
        assertTrue(specs.all { it.group in groupIds })
        assertTrue(specs.any { it.key == "approvals.mode" && it.widget == "select" && "manual" in it.choices })
        assertTrue(specs.none { it.widget == "secret" })
    }

    @Test
    fun `a value is written as its own JSON, and one it does not take is refused`() {
        val (_, specs) = HermesSettings.contract(contract)
        val turns = specs.first { it.key == "goals.max_turns" }
        assertEquals("25", HermesSettings.encode(turns, "25"))
        assertNull(HermesSettings.encode(turns, "0"))
        assertNull(HermesSettings.encode(turns, "many"))
        val mode = specs.first { it.key == "approvals.mode" }
        assertEquals("\"manual\"", HermesSettings.encode(mode, "manual"))
        assertNull(HermesSettings.encode(mode, "sometimes"))
    }

    @Test
    fun `the plugin's answers are read as written, proposed or refused`() {
        assertEquals(HermesSettings.Written.Done, HermesSettings.written("""{"key":"goals.max_turns","value":25}"""))
        assertEquals(
            HermesSettings.Written.Proposed("abc123"),
            HermesSettings.written("""{"status":"proposed","proposal":{"id":"abc123"}}"""),
        )
        assertEquals(HermesSettings.Written.Refused("refused: no"), HermesSettings.written("""{"error":"refused: no"}"""))
        assertEquals(25, HermesSettings.values("""[{"key":"goals.max_turns","value":25}]""")["goals.max_turns"])
    }
}
