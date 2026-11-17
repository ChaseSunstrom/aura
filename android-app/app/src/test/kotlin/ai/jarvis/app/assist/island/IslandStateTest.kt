package ai.jarvis.app.assist.island

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The phone's island chooses its state as the desktop popup does (T-0695):
 * `tests/contracts/island.json` is the one table both suites read, each case's state and each
 * state's size as tokens. Does not cover how a state is drawn (the Island goldens).
 */
class IslandStateTest {
    private val contract = JSONObject(File("../../tests/contracts/island.json").readText())

    /** `--jv-…: 212px;` as the console's generated sheet has it, so a token name reads as dp. */
    private val css = File("../../jarvis-web/src/lib/styles/tokens.css").readText()
    private fun dp(name: String): Int =
        Regex("${Regex.escape(name)}:\\s*(\\d+)px;").find(css)?.groupValues?.get(1)?.toInt()
            ?: error("$name is not a px token in tokens.css")

    @Test
    fun `every case in the table gets the table's state`() {
        val cases = contract.getJSONArray("cases")
        for (n in 0 until cases.length()) {
            val case = cases.getJSONObject(n)
            val given = case.getJSONObject("in")
            val inputs = Island.Inputs(
                turn = given.optBoolean("turn"),
                ask = given.optBoolean("ask"),
                notice = given.optBoolean("notice"),
                activity = given.optBoolean("activity"),
                expanded = given.optBoolean("expanded"),
                collapsed = given.optBoolean("collapsed"),
                working = given.optBoolean("working"),
                touched = given.optBoolean("touched"),
                on = given.optBoolean("on"),
            )
            val state = Island.stateFor(inputs)
            assertEquals(case.getString("name"), case.getString("state"), state.key)
            // Where the phone puts it (T-0885), with its setting (T-0968); the desktop popup has one place and ignores both.
            if (case.has("place")) {
                val bottom = case.optString("placement") == "bottom"
                assertEquals(case.getString("name"), case.getString("place"), Island.placeFor(state, inputs, bottom).key)
                assertEquals(case.getString("name"), case.optBoolean("bare"), Island.bare(state, inputs))
            }
        }
    }

    @Test
    fun `each state is the table's size, and only a card takes the focus`() {
        val states = contract.getJSONObject("states")
        assertEquals(states.keys().asSequence().toSet(), Island.State.entries.map { it.key }.toSet())
        for (state in Island.State.entries) {
            val row = states.getJSONObject(state.key)
            val size = Island.SIZES.getValue(state)
            assertEquals(state.key, row.optString("width").ifEmpty { null }?.let(::dp), size.widthDp)
            val height = row.optString("height").ifEmpty { null }
            assertEquals(state.key, if (height == "content") Island.CONTENT else height?.let(::dp), size.heightDp)
            assertEquals(state.key, row.getBoolean("focus"), size.focus)
        }
        assertEquals(dp(contract.getString("radius")), Island.RADIUS_DP)
    }

    @Test
    fun `every state a person can see has a spoken description, the table's words`() {
        val states = contract.getJSONObject("states")
        for (state in Island.State.entries) {
            val row = states.getJSONObject(state.key)
            assertTrue("${state.key} has no \"spoken\" in the table", row.has("spoken"))
            assertEquals(state.key, row.getString("spoken"), state.spoken)
            // Nothing drawn, nothing said; anything drawn is named.
            val drawn = row.getJSONArray("shows").length() > 0
            assertEquals("${state.key}: spoken words iff it shows something", drawn, state.spoken.isNotBlank())
        }
    }}
