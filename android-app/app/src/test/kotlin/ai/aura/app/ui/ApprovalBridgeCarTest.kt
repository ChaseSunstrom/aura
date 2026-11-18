package ai.aura.app.ui

import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLooper

/**
 * The phone's consent prompt is inert under the keyguard, so a text asked for from the
 * car with the phone locked timed out as a no (the owner, 2026-09-26). While a car is
 * linked the same request is asked on the car too ([ApprovalBridge.carRoute]); the car's
 * yes or no settles it, exactly as the phone's buttons would.
 */
@RunWith(RobolectricTestRunner::class)
class ApprovalBridgeCarTest {

    @After fun unhook() {
        ApprovalBridge.carRoute = null
    }

    private fun outcomeWithCarAnswer(answer: Boolean): Pair<ApprovalBridge.Outcome, String> {
        var said = ""
        ApprovalBridge.carRoute = { _: String, _: String, spoken: String -> said = spoken; delay(50); answer }
        val outcome = runBlocking {
            val job = async(Dispatchers.Default) {
                ApprovalBridge.requestOutcome(
                    ApplicationProvider.getApplicationContext(),
                    actionId = "sms.send",
                    params = """{"to":"Dad","body":"Hello from Jarvis"}""",
                    reason = "You asked me to text Dad.",
                    description = "Send a text message",
                    timeoutMs = 10_000,
                )
            }
            // The car's answer is delivered on the main looper, which Robolectric runs by hand.
            while (!job.isCompleted) {
                ShadowLooper.idleMainLooper(20, java.util.concurrent.TimeUnit.MILLISECONDS)
                delay(10)
            }
            job.await()
        }
        return outcome to said
    }

    @Test fun `a yes on the car approves without the phone`() {
        val (outcome, said) = outcomeWithCarAnswer(true)
        assertEquals(ApprovalBridge.Outcome.APPROVED, outcome)
        assertTrue(said, said.startsWith("Send a text message. You asked me to text Dad."))
        assertTrue(said.endsWith("Shall I go ahead?"))
    }

    @Test fun `a no on the car denies`() {
        assertEquals(ApprovalBridge.Outcome.DENIED, outcomeWithCarAnswer(false).first)
    }
}
