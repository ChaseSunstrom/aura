package ai.aura.app.ui

import ai.aura.app.config.JarvisConfig
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLooper
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The owner could not approve a text with the phone locked (2026-10-08). Each prompt is
 * reported to the house ([ApprovalBridge.consoleRoute]) and the console's answer settles
 * it as a tap would — but only for a prompt this phone offered and is still waiting on.
 */
@RunWith(RobolectricTestRunner::class)
class ApprovalBridgeConsoleTest {

    private val app: Context = ApplicationProvider.getApplicationContext()
    private val sent = CopyOnWriteArrayList<Pair<String, JSONObject>>()

    @After fun unhook() {
        ApprovalBridge.consoleRoute = null
        JarvisConfig(app).remoteApprovals = true
    }

    /** Raises a prompt, then runs [answer] with the id the console was told about. */
    private fun outcome(answer: (String?) -> Unit): ApprovalBridge.Outcome = runBlocking {
        ApprovalBridge.consoleRoute = { event, data -> sent += event to data }
        val job = async(Dispatchers.Default) {
            ApprovalBridge.requestOutcome(
                app,
                actionId = "send_sms",
                params = """{"to":"Grandma","body":"On my way"}""",
                reason = "You asked me to reply.",
                timeoutMs = 3_000,
            )
        }
        var asked = false
        while (!job.isCompleted) {
            if (!asked && (sent.isNotEmpty() || !JarvisConfig(app).remoteApprovals)) {
                delay(50)
                asked = true
                answer(sent.firstOrNull { it.first == ApprovalBridge.CONSENT_PENDING }?.second?.getString("id"))
            }
            ShadowLooper.idleMainLooper(20, java.util.concurrent.TimeUnit.MILLISECONDS)
            delay(10)
        }
        job.await()
    }

    @Test fun `a yes from the console approves and the prompt is taken off it`() {
        val result = outcome { id -> assertTrue(ApprovalBridge.deliverRemote(id!!, true)) }
        assertEquals(ApprovalBridge.Outcome.APPROVED, result)
        val pending = sent.first { it.first == ApprovalBridge.CONSENT_PENDING }.second
        assertEquals("send_sms", pending.getString("action"))
        assertEquals("""{"to":"Grandma","body":"On my way"}""", pending.getString("params"))
        assertEquals(10L, pending.getLong("timeout_s")) // clamped up to MIN_TIMEOUT_MS
        assertEquals(pending.getString("id"), sent.last { it.first == ApprovalBridge.CONSENT_RESOLVED }.second.getString("id"))
        // Settled: a second answer for the same id is refused.
        assertFalse(ApprovalBridge.deliverRemote(pending.getString("id"), true))
    }

    @Test fun `an id the phone never offered is refused`() {
        val result = outcome { id ->
            assertFalse(ApprovalBridge.deliverRemote("not-${id}", true))
            ApprovalBridge.deliverRemote(id!!, false)
        }
        assertEquals(ApprovalBridge.Outcome.DENIED, result)
    }

    @Test fun `with remote answers off nothing is reported and nothing remote settles it`() {
        JarvisConfig(app).remoteApprovals = false
        val result = outcome { assertTrue(sent.isEmpty()) }
        // Unanswered here (the activity cannot be tapped under Robolectric): it times out.
        assertEquals(ApprovalBridge.Outcome.TIMED_OUT, result)
        assertTrue(sent.isEmpty())
    }
}
