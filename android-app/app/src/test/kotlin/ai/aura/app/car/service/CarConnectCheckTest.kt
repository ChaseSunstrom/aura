package ai.aura.app.car.service

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/** The rule and its cases are `tests/contracts/car_connect_check.json`, which the Python mirror runs too. */
class CarConnectCheckTest {
    private val contract = JSONObject(File("../../tests/contracts/car_connect_check.json").readText())

    private fun JSONObject.bool(key: String): Boolean? = if (isNull(key)) null else getBoolean(key)

    @Test
    fun `the lines are the contract's`() {
        val lines = contract.getJSONObject("lines")
        assertEquals(lines.getString("home"), CarConnectCheck.HOME)
        assertEquals(lines.getString("console"), CarConnectCheck.CONSOLE)
        assertEquals(lines.getString("music"), CarConnectCheck.MUSIC)
    }

    @Test
    fun `every case in the contract`() {
        val cases = contract.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val want = if (c.isNull("say")) null else c.getString("say")
            assertEquals(c.getString("name"), want, CarConnectCheck.line(c.bool("home"), c.bool("console"), c.getBoolean("grant_missing")))
        }
    }

    // T-0713: home unreachable in the car with no tunnel of Jarvis's own is said with its remedy.
    @Test
    fun `every remedy case in the contract`() {
        val remedy = contract.getJSONObject("remedy")
        // The cases are said in the contract's worn pack, the default one.
        assertEquals(remedy.getString("worn"), ai.aura.app.ui.theme.ActiveTheme.name)
        assertEquals(remedy.getString("title").replace("{name}", remedy.getString("worn")), CarConnectCheck.REMEDY_TITLE)
        val cases = remedy.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val want = if (c.isNull("remedy")) null else c.getString("remedy")
            val apps = c.getJSONArray("vpn_apps").let { a -> List(a.length()) { a.getString(it) } }
            assertEquals(c.getString("name"), want, CarConnectCheck.remedy(c.bool("home"), c.getBoolean("own_tunnel"), apps))
        }
    }

    @Test
    fun `a console still loading at the first look is asked again before it is called down`() = kotlinx.coroutines.test.runTest {
        // 2026-11-12: console=false eight seconds after the link, true on the next link.
        var asked = 0
        assertEquals(true, CarConnectCheck.settled(tries = 5, gapMs = 2_000) { ++asked >= 3 })
        assertEquals(3, asked)
        asked = 0
        assertEquals(false, CarConnectCheck.settled(tries = 5, gapMs = 2_000) { asked++; false })
        assertEquals(5, asked)
        // No console to show is not a console that is down: asked once.
        asked = 0
        assertEquals(null, CarConnectCheck.settled(tries = 5, gapMs = 2_000) { asked++; null })
        assertEquals(1, asked)
    }
}
