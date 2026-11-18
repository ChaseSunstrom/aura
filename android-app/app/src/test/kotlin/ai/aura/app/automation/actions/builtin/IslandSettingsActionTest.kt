package ai.aura.app.automation.actions.builtin

import ai.aura.app.config.JarvisConfig
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.io.File

/**
 * The phone's half of the island settings over the device channel (T-0984): the two actions
 * read and change the four switches under Settings, Island. The names, the defaults and the
 * refusals are `tests/contracts/island-settings.json`, which the Hermes plugin's tests read too.
 *
 * Does not cover how the island looks with a switch on (the island goldens) or the Settings
 * screen's own toggles (SettingsScreenTest); a real accessibility grant is the device's.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class IslandSettingsActionTest {

    private val contract = JSONObject(File("../../tests/contracts/island-settings.json").readText())
    private val app get() = RuntimeEnvironment.getApplication()
    private val config get() = JarvisConfig(app)

    private fun names(key: String): List<String> =
        contract.getJSONArray(key).let { a -> (0 until a.length()).map { a.getString(it) } }

    private fun run(action: ai.aura.app.automation.actions.JarvisAction, params: JSONObject = JSONObject()) =
        runBlocking { action.execute(app, params) }

    private fun settingsOf(data: JSONObject?): JSONObject = data!!.getJSONObject("settings")

    @Before
    fun fresh() {
        config.islandRest = true
        config.islandStatus = false
        config.islandCover = false
        config.islandBottom = false
    }

    @Test
    fun theActionIdsAndSettingNamesAreTheContracts() {
        assertEquals(contract.getJSONObject("actions").getString("get"), GetIslandSettings.id)
        assertEquals(contract.getJSONObject("actions").getString("set"), SetIslandSettings.id)
        assertEquals(names("settings").toSet(), IslandSettingsContract.NAMES.toSet())
    }

    @Test
    fun getReportsTheFourCurrentValuesAndTheDefaultsAreTheContracts() {
        val got = settingsOf(run(GetIslandSettings).data)
        val defaults = contract.getJSONObject("defaults")
        for (name in names("settings")) assertEquals(name, defaults.getBoolean(name), got.getBoolean(name))
        assertEquals(names("settings").size, got.length())
    }

    @Test
    fun setChangesOnlyTheNamedSwitchesAndReportsWhatMovedAndTheNewValues() {
        val r = run(SetIslandSettings, JSONObject().put("status", true).put("conversations_at_bottom", true))
        assertTrue(r.ok)
        assertTrue(config.islandStatus && config.islandBottom)
        assertTrue(config.islandRest)
        assertFalse(config.islandCover)
        val now = settingsOf(r.data)
        assertTrue(now.getBoolean("status") && now.getBoolean("conversations_at_bottom"))
        assertEquals(setOf("status", "conversations_at_bottom"), strings(r.data!!.getJSONArray("changed")))
        // What a read answers next is what the set answered.
        assertEquals(now.toString(), settingsOf(run(GetIslandSettings).data).toString())
    }

    @Test
    fun aSwitchAlreadyAtItsValueIsNotReportedAsChanged() {
        val r = run(SetIslandSettings, JSONObject().put("keep_at_camera", true).put("status", true))
        assertEquals(setOf("status"), strings(r.data!!.getJSONArray("changed")))
    }

    @Test
    fun aStatusWithNoIslandAtTheCameraIsStoredAndNotedNotRewritten() {
        val r = run(SetIslandSettings, JSONObject().put("keep_at_camera", false).put("status", true))
        assertTrue(r.ok)
        assertTrue(config.islandStatus)
        val notes = r.data!!.getJSONArray("notes")
        assertTrue(notes.toString(), (0 until notes.length()).any { notes.getJSONObject(it).getString("setting") == "status" })
    }

    @Test
    fun aCoverNeedsTheAccessibilityGrantAndSaysSoInsteadOfOpeningSettings() {
        val r = run(SetIslandSettings, JSONObject().put("cover", true))
        assertTrue(r.ok)
        assertTrue(config.islandCover)
        val notes = r.data!!.getJSONArray("notes").toString()
        assertTrue(notes, notes.contains("cover_needs_accessibility"))
    }

    @Test
    fun aBadCommandChangesNothingAndSaysWhy() {
        val refused = contract.getJSONObject("examples").getJSONArray("refused")
        val before = settingsOf(run(GetIslandSettings).data).toString()
        for (i in 0 until refused.length()) {
            val r = run(SetIslandSettings, refused.getJSONObject(i))
            assertFalse(refused.getJSONObject(i).toString(), r.ok)
            assertEquals(before, settingsOf(run(GetIslandSettings).data).toString())
        }
        // The first bad value must not leave the good one before it applied.
        run(SetIslandSettings, JSONObject().put("conversations_at_bottom", true).put("status", "yes"))
        assertFalse(config.islandBottom)
    }

    @Test
    fun theContractsExampleAnswerIsTheShapeTheActionAnswers() {
        val ex = contract.getJSONObject("examples")
        val r = run(SetIslandSettings, ex.getJSONObject("set_command"))
        val want = ex.getJSONObject("set_answer")
        assertEquals(want.getJSONObject("settings").toString(), settingsOf(r.data).toString())
        assertEquals(strings(want.getJSONArray("changed")), strings(r.data!!.getJSONArray("changed")))
    }

    private fun strings(a: JSONArray): Set<String> = (0 until a.length()).map { a.getString(it) }.toSet()
}
