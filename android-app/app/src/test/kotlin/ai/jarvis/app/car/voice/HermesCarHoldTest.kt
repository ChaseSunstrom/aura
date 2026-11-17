package ai.jarvis.app.car.voice

import ai.jarvis.app.assist.hermes.HermesClient
import ai.jarvis.app.assist.hermes.HermesClientTest.FakeSocket
import ai.jarvis.app.assist.hermes.HermesClientTest.ManualClock
import ai.jarvis.app.channel.Backoff
import ai.jarvis.app.config.JarvisConfig
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.async
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * AUDIT B5 in the car: a question asked while the Jarvis link redials used to answer null at
 * once (the car's offline line) although the link was seconds from back.
 */
@RunWith(RobolectricTestRunner::class)
class HermesCarHoldTest {

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

    private fun prompts() = sockets.flatMap { s -> s.sent.filter { it.optString("method") == "prompt.submit" } }

    @Test
    fun `a question asked while the link redials is sent once it re-attaches`() = runTest {
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        client.onFailure(sockets.last(), RuntimeException("reset"), null)

        val answer = async { car.ask("lights off", null) { false } }
        runCurrent()
        assertFalse("held, not answered offline", answer.isCompleted)

        clock.fire(0); ready()
        reply("""{"session_id":"s-2","stored_session_id":"st-1"}""")
        runCurrent()
        assertEquals("prompt.submit", sockets.last().lastMethod())
        assertEquals("s-2", sockets.last().last().getJSONObject("params").getString("session_id"))
        answer.cancel()
    }

    /**
     * T-0559: a turn lost to a drop was answered null at once (the car's offline line) while
     * the client resent it on resume, so Hermes ran a turn whose reply nobody heard.
     */
    @Test
    fun `a turn cut by a short drop is answered by the resent turn`() = runTest {
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        val answer = async { car.ask("how far to the bakery", null) { false } }
        runCurrent()
        client.onFailure(sockets.last(), RuntimeException("reset"), null)
        runCurrent()
        assertFalse("still waiting through the redial", answer.isCompleted)

        clock.fire(0); ready()
        reply("""{"session_id":"s-2","stored_session_id":"st-1"}""")
        assertEquals(2, prompts().size)  // the client's one resend
        frame("""{"jsonrpc":"2.0","method":"event","params":{"type":"message.complete","session_id":"s-2","payload":{"text":"Eight minutes."}}}""")
        runCurrent()
        assertEquals("Eight minutes.", answer.await())
    }

    @Test
    fun `a turn the car gave up on is never sent behind the driver`() = runTest {
        val car = car()
        client.connect(); ready()
        reply("""{"session_id":"s-1","stored_session_id":"st-1"}""")
        val answer = async { car.ask("text Isa I'm late", null) { false } }
        runCurrent()
        client.onFailure(sockets.last(), RuntimeException("reset"), null)
        advanceTimeBy(11_000)
        runCurrent()
        assertNull("the offline line, after the window", answer.await())

        clock.fire(0); ready()
        reply("""{"session_id":"s-2","stored_session_id":"st-1"}""")
        assertEquals("only the first send", 1, prompts().size)
    }
}
