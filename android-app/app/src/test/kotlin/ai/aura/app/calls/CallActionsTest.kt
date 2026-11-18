package ai.aura.app.calls

import ai.aura.app.car.phone.LiveCall
import ai.aura.app.automation.policy.ActionTier
import android.content.Context
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import java.io.File

/** Answer, decline, call back and the policy. Telecom, SMS and the dialer are fakes: nothing here rings or dials. */
@RunWith(RobolectricTestRunner::class)
class CallActionsTest {
    private val contract = JSONObject(File("../../tests/contracts/call-event.json").readText())
    private val ctx: Context get() = RuntimeEnvironment.getApplication()
    private val log = ArrayList<String>()

    @Before fun fakes() {
        CallEvents.reset()
        CallAnswer.answerer = { log.add("answer"); true }
        CallDecline.hanger = { log.add("hangup"); true }
        CallDecline.texter = { _, n, t -> log.add("text $n $t"); true }
        CallBack.dialer = { _, n -> log.add("dial $n"); true }
        CallActions.numberFor = { _, from -> if (from == "Sam") "+15551234567" else null }
        CallPolicy.store = { _, r -> log.add("store $r") }
    }

    @After fun real() {
        CallAnswer.answerer = { false }
        CallDecline.hanger = { false }
        CallDecline.texter = { _, _, _ -> false }
        CallBack.dialer = { _, _ -> false }
        CallActions.numberFor = { _, _ -> null }
        CallPolicy.store = { _, _ -> }
        CallPolicy.rule = "ring"
        CallEvents.reset()
    }

    @Test fun `the actions are the contract's and none is CONFIRM`() {
        val a = contract.getJSONObject("actions")
        assertEquals(listOf(a.getString("answer"), a.getString("decline"), a.getString("back"), a.getString("policy")), CallActions.all.map { it.id })
        assertTrue(CallActions.all.none { it.tier == ActionTier.CONFIRM })
    }

    @Test fun `answer answers`() = runBlocking {
        assertTrue(CallAnswer.execute(ctx, JSONObject()).ok)
        assertEquals(listOf("answer"), log)
    }

    @Test fun `decline hangs up and sends only a fixed line to a known number`() = runBlocking {
        CallEvents.onCall(ctx, LiveCall("Sam", true, null, null, "d"), 1L)
        assertTrue(CallDecline.execute(ctx, JSONObject().put("reply", "driving")).ok)
        assertEquals(listOf("hangup", "text +15551234567 " + CallEvents.REPLIES.getValue("driving")), log)
    }

    @Test fun `decline with a stranger hangs up and texts nobody`() = runBlocking {
        CallEvents.onCall(ctx, LiveCall("Stranger", true, null, null, "d"), 1L)
        assertTrue(CallDecline.execute(ctx, JSONObject().put("reply", "busy")).ok)
        assertEquals(listOf("hangup"), log)
    }

    @Test fun `a reply that is not a fixed line is refused before anything happens`() = runBlocking {
        assertFalse(CallDecline.execute(ctx, JSONObject().put("reply", "I am at the pub")).ok)
        assertTrue(log.isEmpty())
    }

    @Test fun `call back dials the number behind a seen call, even after it ended`() = runBlocking {
        CallEvents.onCall(ctx, LiveCall("Sam", true, null, null, "d"), 1L)
        val id = CallEvents.snapshot()!!.getString("id")
        CallEvents.onCall(ctx, null, 2L)
        assertTrue(CallBack.execute(ctx, JSONObject().put("call_id", id)).ok)
        assertEquals(listOf("dial +15551234567"), log)
    }

    @Test fun `call back refuses an unknown call or a name with no number`() = runBlocking {
        assertFalse(CallBack.execute(ctx, JSONObject().put("call_id", "call-999")).ok)
        assertFalse(CallBack.execute(ctx, JSONObject()).ok)
        CallEvents.onCall(ctx, LiveCall("Nobody", true, null, null, "d"), 1L)
        assertFalse(CallBack.execute(ctx, JSONObject().put("call_id", CallEvents.snapshot()!!.getString("id"))).ok)
        assertTrue(log.isEmpty())
    }

    @Test fun `the policy takes only a known rule`() = runBlocking {
        assertTrue(CallPolicy.execute(ctx, JSONObject().put("rule", "reject")).ok)
        assertEquals("reject", CallPolicy.rule)
        assertFalse(CallPolicy.execute(ctx, JSONObject().put("rule", "block")).ok)
        assertEquals("reject", CallPolicy.rule)
        assertEquals(listOf("store reject"), log)
    }
}
