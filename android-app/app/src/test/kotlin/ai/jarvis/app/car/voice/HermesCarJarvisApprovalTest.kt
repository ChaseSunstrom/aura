package ai.jarvis.app.car.voice

import ai.jarvis.app.assist.hermes.HermesClient
import ai.jarvis.app.assist.hermes.HermesClientTest.FakeSocket
import ai.jarvis.app.assist.hermes.HermesClientTest.ManualClock
import ai.jarvis.app.assist.hermes.HudState
import ai.jarvis.app.car.dash.CarShell
import ai.jarvis.app.channel.Backoff
import ai.jarvis.app.config.JarvisConfig
import ai.jarvis.app.ui.ApprovalBridge
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.async
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * T-0945 in the car: a sensitive ask (`confirm: "phone"`) is never allowed by voice. The car
 * says to confirm on the phone, the ask goes to the phone's consent screen, and only a signed
 * answer from there is sent on the car's own session.
 *
 * Does not run the fingerprint: ApprovalBiometricTest covers the signature; this covers that
 * the car's "yes" path cannot produce an allow.
 */
@RunWith(RobolectricTestRunner::class)
class HermesCarJarvisApprovalTest {

    private val clock = ManualClock()
    private val sockets = ArrayList<FakeSocket>()
    private lateinit var client: HermesClient

    private fun frame(json: String) = client.onMessage(sockets.last(), json)
    private fun ready() = frame("""{"jsonrpc":"2.0","method":"event","params":{"type":"gateway.ready","payload":{}}}""")
    private fun reply(result: String) =
        frame("""{"jsonrpc":"2.0","id":${sockets.last().last().getInt("id")},"result":$result}""")

    private fun TestScope.car() = HermesCarJarvis(
        ApplicationProvider.getApplicationContext(), JarvisConfig(ApplicationProvider.getApplicationContext()), backgroundScope,
        newClient = { listener ->
            HermesClient(
                "http://adapter.lan:8643", "devkey", listener, clock,
                socketFactory = { request, _ -> FakeSocket(request).also { sockets.add(it) } },
                backoff = Backoff(baseMs = 500, maxMs = 30_000), random = { 0.0 },
            ).also { client = it }
        },
    )

    @After
    fun reset() {
        CarShell.onSay = null
    }

    private fun request(id: String, confirm: Boolean) {
        val payload = JSONObject().put("request_id", id).put("tool", "lock_door").put("command", "{}")
            .put("reason", "x").put("choices", JSONArray(listOf("once", "session", "always", "deny")))
        if (confirm) {
            payload.put("confirm", "phone").put("challenge", "$id\nlock:sensitive:0123456789abcdef0123456789abcdef")
        }
        frame(
            JSONObject().put("jsonrpc", "2.0").put("method", "event").put(
                "params", JSONObject().put("type", "approval.request").put("session_id", "s-1").put("payload", payload),
            ).toString(),
        )
    }

    private fun responses() = sockets.flatMap { s -> s.sent.filter { it.optString("method") == "approval.respond" } }

    @Test
    fun `the car never asks for a yes to a phone-confirmed ask and says where to confirm`() = runTest {
        val said = ArrayList<String>()
        CarShell.onSay = { said.add(it) }
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        var asked = false
        val answer = async { car.ask("lock the front door", null) { asked = true; true } }
        runCurrent()

        request("r1", confirm = true)
        runCurrent()
        shadowOf(Looper.getMainLooper()).idle()

        assertFalse("the car's yes/no card was never raised", asked)
        assertTrue("told the driver where to confirm: $said", said.single().contains("phone", ignoreCase = true))
        assertTrue("no answer was sent for the car", responses().isEmpty())
        assertTrue("the phone's consent screen holds it", ApprovalBridge.isPending("r1"))

        // The phone's screen allowing with a signature goes out on the car's session, signed.
        ApprovalBridge.deliverChoice("r1", "once", "c2ln")
        val sent = responses().single().getJSONObject("params")
        assertEquals("once", sent.getString("choice"))
        assertEquals("c2ln", sent.getString("signature"))
        answer.cancel()
    }

    @Test
    fun `an unsigned allow from the phone's screen is sent as a deny`() = runTest {
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        val answer = async { car.ask("lock the front door", null) { true } }
        runCurrent()
        request("r2", confirm = true)
        runCurrent()

        ApprovalBridge.deliverChoice("r2", "once")
        val sent = responses().single().getJSONObject("params")
        assertEquals("deny", sent.getString("choice"))
        assertFalse(sent.has("signature"))
        answer.cancel()
    }

    @Test
    fun `an ordinary ask still goes to the car's card`() = runTest {
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        var asked: HudState.Approval? = null
        val answer = async { car.ask("turn the lamp on", null) { asked = it; true } }
        runCurrent()
        request("r3", confirm = false)
        runCurrent()
        assertEquals("r3", asked?.requestId)
        assertEquals("once", responses().single().getJSONObject("params").getString("choice"))
        answer.cancel()
    }

    /** A proof ask comes from the adapter: `proof-` id and no session_id on the event at all. */
    private fun sessionless(type: String, payload: JSONObject) = frame(
        JSONObject().put("jsonrpc", "2.0").put("method", "event")
            .put("params", JSONObject().put("type", type).put("payload", payload)).toString(),
    )

    private fun proofAsk(id: String) = sessionless(
        "approval.request",
        JSONObject().put("request_id", id).put("tool", "lock_door").put("command", "{}").put("reason", "x")
            .put("choices", JSONArray(listOf("once", "deny"))).put("confirm", "phone")
            .put("challenge", "$id\nlock:sensitive:0123456789abcdef0123456789abcdef"),
    )

    @Test
    fun `an ask from the adapter with no session id is held and answered on the current session`() = runTest {
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        val answer = async { car.ask("lock the front door", null) { true } }
        runCurrent()

        proofAsk("proof-1")
        runCurrent()
        assertTrue(ApprovalBridge.isPending("proof-1"))

        ApprovalBridge.deliverChoice("proof-1", "once", "c2ln")
        val sent = responses().single().getJSONObject("params")
        assertEquals("proof-1", sent.getString("request_id"))
        assertEquals("c2ln", sent.getString("signature"))
        assertEquals("s-1", sent.getString("session_id"))
        answer.cancel()
    }

    @Test
    fun `an ask answered elsewhere or timed out comes down without sending anything`() = runTest {
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        val answer = async { car.ask("lock the front door", null) { true } }
        runCurrent()
        proofAsk("proof-2")
        runCurrent()
        assertTrue(ApprovalBridge.isPending("proof-2"))

        sessionless("approval.resolved", JSONObject().put("request_id", "proof-2").put("choice", "timeout"))
        runCurrent()

        assertFalse("the consent screen's ask is gone", ApprovalBridge.isPending("proof-2"))
        assertTrue("nothing was answered from here", responses().isEmpty())
        answer.cancel()
    }

    @Test
    fun `a refused signature keeps the ask open for another try`() = runTest {
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        val answer = async { car.ask("lock the front door", null) { true } }
        runCurrent()
        proofAsk("proof-3")
        runCurrent()

        ApprovalBridge.deliverChoice("proof-3", "once", "YmFk")
        assertFalse(ApprovalBridge.isPending("proof-3"))
        // The adapter refuses the signature: -32000, and the ask stays open on its side.
        frame("""{"jsonrpc":"2.0","id":${responses().single().getInt("id")},"error":{"code":-32000,"message":"signature refused"}}""")
        runCurrent()
        shadowOf(Looper.getMainLooper()).idle()

        assertTrue("raised again, so the person can retry or deny", ApprovalBridge.isPending("proof-3"))
        answer.cancel()
    }
}
