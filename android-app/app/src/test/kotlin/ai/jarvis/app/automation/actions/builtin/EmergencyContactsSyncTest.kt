package ai.jarvis.app.automation.actions.builtin

import ai.jarvis.app.automation.audit.ParamRedaction
import ai.jarvis.app.automation.policy.ActionTier
import ai.jarvis.app.config.JarvisConfig
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File

/**
 * The phone's half of the house handing it the owner's emergency contacts (T-1262): the action
 * only STORES the list the impact watch (T-1209) reads. The action's name, its limits and which
 * lists it takes or refuses whole are `tests/contracts/emergency-contacts-sync.json`, which the
 * house's tests and `tools/emergency_contacts_sync_test.py` read too.
 *
 * Does not cover the house's side of the send (jarvis-core's test_emergency_outreach) or the
 * watch's own alert (ImpactWatchTest). Every name and number here is made up.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class EmergencyContactsSyncTest {

    private val contract = JSONObject(File("../../tests/contracts/emergency-contacts-sync.json").readText())
    private val app get() = RuntimeEnvironment.getApplication()
    private val config get() = JarvisConfig(app)

    private val before = """[{"name":"Old Person","number":"+15550199"}]"""

    private fun cases(key: String): List<JSONObject> =
        contract.getJSONArray(key).let { a -> (0 until a.length()).map { a.getJSONObject(it) } }

    private fun run(params: JSONObject) = runBlocking { EmergencyContactsSync.execute(app, params) }

    private fun stored(): JSONArray = JSONArray(config.emergencyContacts.ifEmpty { "[]" })

    @Before
    fun fresh() {
        config.emergencyContacts = before
    }

    @Test
    fun theActionIsTheContractsAndNeedsNoPrompt() {
        assertEquals(contract.getString("action"), EmergencyContactsSync.id)
        assertEquals(contract.getInt("tier"), 1)
        assertEquals(ActionTier.AUTO, EmergencyContactsSync.tier)
        assertNotNull(
            "not registered, so the phone would answer unknown action",
            Builtins.all().firstOrNull { it.id == EmergencyContactsSync.id }
        )
    }

    @Test
    fun theLimitsAreTheContractsAndTheAlertsOwn() {
        val limits = contract.getJSONObject("limits")
        assertEquals(limits.getInt("contacts_max"), EmergencyContract.CONTACTS_MAX)
        assertEquals(limits.getInt("name_max"), EmergencyContract.NAME_MAX)
        assertEquals(limits.getInt("min_digits"), EmergencyContract.MIN_DIGITS)
    }

    @Test
    fun aValidListIsStoredForTheWatchAndAnEmptyOneClears() {
        for (case in cases("accepted")) {
            config.emergencyContacts = before
            val res = run(case.getJSONObject("params"))
            val want = case.getJSONArray("stored")
            assertTrue("${case.getString("why")}: ${res.error}", res.ok)
            assertEquals(case.getString("why"), want.toString(), stored().toString())
            assertEquals(want.length(), res.data!!.getInt("stored"))
            assertEquals("ok", res.data!!.getString("status"))
        }
    }

    @Test
    fun theAnswersAreTheContractsShape() {
        val answers = contract.getJSONObject("answers")
        val ok = run(cases("accepted")[0].getJSONObject("params")).data!!
        assertEquals(answers.getJSONObject("ok").toString(), ok.toString())
        val cleared = run(JSONObject().put("contacts", JSONArray())).data!!
        assertEquals(answers.getJSONObject("cleared").toString(), cleared.toString())
    }

    @Test
    fun aNameIsCleanedTheWayTheAlertCleansIt() {
        for (case in cases("phone_cleans")) {
            assertTrue(run(case.getJSONObject("params")).ok)
            assertEquals(case.getJSONArray("stored").toString(), stored().toString())
        }
    }

    @Test
    fun aListItCannotTakeIsRefusedWholeAndTheStoredOneIsLeftAlone() {
        for (case in cases("refused") + cases("refused_by_phone_only")) {
            config.emergencyContacts = before
            val res = run(case.getJSONObject("params"))
            assertFalse("${case.getString("why")} was taken", res.ok)
            assertEquals("${case.getString("why")} touched the stored list", before, config.emergencyContacts)
        }
    }

    @Test
    fun itOnlyStoresItStartsNoCallOrTextAndAsksForNoPermission() {
        run(cases("accepted")[0].getJSONObject("params"))
        run(cases("accepted")[2].getJSONObject("params"))
        assertNull("an activity (a dial or a message) was started", shadowOf(app).nextStartedActivity)
        assertNull(shadowOf(app).nextStartedService)
        assertTrue("it must need no permission", EmergencyContactsSync.requiredPermissions.isEmpty())
    }

    // --- who may send it: the house's device channel and nothing else ---------------------------

    private fun registry() = ai.jarvis.app.automation.actions.ActionRegistry(
        app, ai.jarvis.app.automation.policy.InMemoryPolicyProvider(),
        ai.jarvis.app.automation.audit.AuditLog(app),
        object : ai.jarvis.app.automation.actions.ApprovalGateway {
            override suspend fun request(request: ai.jarvis.app.automation.actions.ApprovalRequest) =
                ai.jarvis.app.automation.actions.ApprovalVerdict.DENIED
        },
        ai.jarvis.app.automation.actions.NoPermissionGateway,
    ).register(EmergencyContactsSync)

    private val list get() = cases("accepted")[0].getJSONObject("params")

    @Test
    fun theHouseOnlyActionsAreTheContractsList() {
        // The house refuses these ids for every service call (DeviceLink.dispatch) and reads this same list.
        val ids = contract.getJSONArray("house_only_actions")
        assertEquals(
            (0 until ids.length()).map { ids.getString(it) }.toSet(),
            ai.jarvis.app.automation.actions.ActionRegistry.HOUSE_ONLY,
        )
    }

    @Test
    fun theHouseChannelsDispatchStoresIt() {
        val body = runBlocking {
            ai.jarvis.app.automation.actions.RegistryDispatcher(registry())
                .dispatch(EmergencyContactsSync.id, list, "auto", "the house synced the contacts")
        }
        assertEquals(body.toString(), "ok", body.optString("status"))
        assertEquals(cases("accepted")[0].getJSONArray("stored").toString(), stored().toString())
    }

    @Test
    fun aTaskOrAnyOtherSourceIsDeniedAndTheStoredListIsLeftAlone() {
        for (source in listOf("task:t-1", "assistant", "bridge", "")) {
            val res = runBlocking {
                registry().dispatch(EmergencyContactsSync.id, list, null, "x", source = source)
            }
            assertEquals("'$source' was let through", ai.jarvis.app.automation.actions.ActionResult.Status.DENIED, res.status)
            assertEquals(before, config.emergencyContacts)
        }
    }

    @Test
    fun theBridgeDoorTheAssistantAndTheUiUseIsDeniedToo() {
        val bridge = ai.jarvis.app.automation.AutomationBridge
        bridge.dispatcher = ai.jarvis.app.automation.actions.RegistryDispatcher(registry())
        try {
            val body = runBlocking {
                bridge.dispatchCommand(EmergencyContactsSync.id, list, "auto", "the model asked", "hud-1")
            }
            assertNotNull(body)
            assertEquals(body.toString(), "denied", body!!.optString("status"))
            assertEquals(before, config.emergencyContacts)
        } finally {
            bridge.dispatcher = null
        }
    }

    @Test
    fun theAuditLogKeepsTheCountAndNeverTheNamesOrNumbers() {
        val params = cases("accepted")[2].getJSONObject("params")
        val redacted = ParamRedaction.redact(params, EmergencyContactsSync.id)
        assertEquals(5, redacted.getInt("contacts_count"))
        assertFalse(redacted.toString(), redacted.toString().contains("+1555"))
        assertFalse(redacted.toString(), redacted.toString().contains("\"name\""))
    }
}
