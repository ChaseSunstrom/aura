package ai.aura.app.messages

import ai.aura.app.automation.actions.ActionResult
import ai.aura.app.automation.policy.ActionTier
import ai.aura.app.car.dash.tiles.CarMessage
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

/** Nothing here can send: SmsManager and the notification's reply action are replaced by fakes. */
@RunWith(RobolectricTestRunner::class)
class ReplyActionTest {
    private val contract = JSONObject(File("../../tests/contracts/message-mirror.json").readText())
    private val ctx: Context get() = RuntimeEnvironment.getApplication()
    private val sent = ArrayList<String>()

    private fun example(name: String) = contract.getJSONObject("examples").getJSONObject(name)

    @Before fun fakes() {
        ReplyAction.smsSender = { _, number, body -> sent.add("sms $number $body"); ActionResult.ok("sent" to true) }
        ReplyAction.appFinder = { pkg, sender ->
            if (pkg == "com.whatsapp" && sender == "anna b") CarMessage("k", pkg, "Anna B", "hi", 1L, null) else null
        }
        ReplyAction.appSender = { _, m, text -> sent.add("app ${m.packageName} $text"); true }
    }

    @After fun real() {
        // the fakes are process-wide: leave the real senders for any test that follows
        ReplyAction.smsSender = { c, n, b ->
            ai.aura.app.automation.actions.builtin.SendSms.execute(c, JSONObject().put("number", n).put("body", b))
        }
    }

    @Test fun `the action is the contract's and always asks on the phone`() {
        assertEquals(contract.getString("reply_action"), ReplyAction.id)
        assertEquals(ActionTier.CONFIRM, ReplyAction.tier)
    }

    @Test fun `an sms conversation goes to its number`() = runBlocking {
        val r = ReplyAction.execute(ctx, JSONObject(example("reply_sms").toString()))
        assertTrue(r.ok)
        assertEquals(listOf("sms +15551234567 Yes, 1 works"), sent)
    }

    @Test fun `an sms from a name or short code is refused`() = runBlocking {
        val r = ReplyAction.execute(ctx, JSONObject().put("conversation", "sms:google").put("text", "hi"))
        assertFalse(r.ok)
        assertTrue(sent.isEmpty())
    }

    @Test fun `an app conversation answers through its notification, and only that one`() = runBlocking {
        // the fake finder returns a message with no reply slot: the app offers none
        val none = ReplyAction.execute(ctx, JSONObject(example("reply_app").toString()))
        assertFalse(none.ok)
        assertTrue(sent.isEmpty())
        val gone = ReplyAction.execute(ctx, JSONObject().put("conversation", "app:com.signal:x").put("text", "hi"))
        assertFalse(gone.ok)
    }

    @Test fun `a reply over the card's cap or without text is refused before any send`() = runBlocking {
        val cap = contract.getJSONObject("limits").getInt("reply_card_max")
        assertFalse(ReplyAction.execute(ctx, JSONObject().put("conversation", "sms:+15551234567").put("text", "x".repeat(cap + 1))).ok)
        assertFalse(ReplyAction.execute(ctx, JSONObject().put("conversation", "sms:+15551234567").put("text", " ")).ok)
        assertFalse(ReplyAction.execute(ctx, JSONObject().put("conversation", "fax:1").put("text", "hi")).ok)
        assertTrue(sent.isEmpty())
    }
}
