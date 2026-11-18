package ai.aura.app.assist.hermes

import ai.aura.app.assist.hermes.HermesClient.Transcribed
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlin.concurrent.thread
import javax.net.ssl.SSLHandshakeException

/**
 * The phone's transcribe call says "offline" only when the network failed (T-1079): a server
 * fault, a body that is not a transcript and anything else each come back as themselves,
 * where one catch-all used to tell the owner their house was unreachable.
 */
class TranscribeOutcomeTest {
    private val errors = ArrayList<String>()
    private val listener = object : HermesClient.Listener {
        override fun onSession(sessionId: String, resumed: Boolean) {}
        override fun onEvent(type: String, sessionId: String, payload: JSONObject) {}
        override fun onDisconnected(reason: String, willRetry: Boolean) {}
        override fun onNotice(text: String) {}
        override fun onPrefill(text: String) {}
        override fun onError(message: String) { errors += message }
    }
    private val servers = ArrayList<ServerSocket>()

    @After fun stop() { servers.forEach { it.close() } }

    /** A loopback server that answers every request with [status] and [body]; the JDK's HttpServer is not on this classpath. */
    private fun clientFor(status: Int, body: String): HermesClient {
        val s = ServerSocket(0, 5, InetAddress.getLoopbackAddress())
        servers += s
        thread(isDaemon = true) {
            while (!s.isClosed) {
                val c = try { s.accept() } catch (e: IOException) { return@thread }
                c.use {
                    val input = it.getInputStream()
                    val head = StringBuilder()
                    while (!head.endsWith("\r\n\r\n")) {
                        val b = input.read()
                        if (b < 0) break
                        head.append(b.toChar())
                    }
                    val length = Regex("(?i)content-length: (\\d+)").find(head)?.groupValues?.get(1)?.toInt() ?: 0
                    var left = length
                    while (left > 0) {
                        val n = input.read(ByteArray(minOf(left, 8192)))
                        if (n < 0) break
                        left -= n
                    }
                    val bytes = body.toByteArray()
                    it.getOutputStream().write("HTTP/1.1 $status X\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray() + bytes)
                }
            }
        }
        return HermesClient("http://127.0.0.1:${s.localPort}", "k", listener, HermesClientTest.ManualClock())
    }

    private val wav = ByteArray(64)

    @Test fun `a refused connection is offline`() {
        val port = ServerSocket(0, 1, InetAddress.getLoopbackAddress()).use { it.localPort }
        val client = HermesClient("http://127.0.0.1:$port", "k", listener, HermesClientTest.ManualClock())
        assertEquals(Transcribed.Offline, client.transcribe(wav))
    }

    @Test fun `a timeout, an unknown host and a reset connection are offline`() {
        for (t in listOf(SocketTimeoutException("read"), UnknownHostException("h"), ConnectException("c"), SocketException("reset"))) {
            assertTrue(t.javaClass.simpleName, HermesClient.noNetwork(t))
        }
    }

    @Test fun `any other exception is not offline`() {
        for (t in listOf(IllegalStateException("x"), IOException("unexpected end of stream"), SSLHandshakeException("bad cert"), NullPointerException())) {
            assertFalse(t.javaClass.simpleName, HermesClient.noNetwork(t))
        }
    }

    @Test fun `an HTTP error is a failure with its code, reported and never offline`() {
        val outcome = clientFor(500, """{"detail":"model crashed"}""").transcribe(wav)
        assertTrue("$outcome", outcome is Transcribed.Failed)
        assertTrue((outcome as Transcribed.Failed).why, "500" in outcome.why)
        assertTrue(errors.single(), "500" in errors.single())
    }

    @Test fun `a body that is not a transcript is a failure, not offline`() {
        for (body in listOf("<html>proxy</html>", """{"ok":false,"error":"stt down"}""")) {
            val outcome = clientFor(200, body).transcribe(wav)
            assertTrue("$body -> $outcome", outcome is Transcribed.Failed)
        }
    }

    @Test fun `a transcript is heard, and the old Heard-or-null call agrees`() {
        val client = clientFor(200, """{"ok":true,"transcript":"lights on","speaker_ticket":"t-9"}""")
        val outcome = client.transcribe(wav) as Transcribed.Got
        assertEquals("lights on", outcome.heard.transcript)
        assertEquals("t-9", outcome.heard.ticket)
        assertEquals("lights on", client.transcribeHeard(wav)?.transcript)
    }

    @Test fun `the old call still answers null for every failure`() {
        assertNull(clientFor(500, "{}").transcribeHeard(wav))
    }

    // T-1137: an abandoned transcription frees its thread within a minute, not speech's four.
    @Test
    fun `a transcription waits a minute at most, well under what speech is allowed`() {
        assertTrue(HermesClient.TRANSCRIBE_TIMEOUT_S <= 60L)
        assertTrue(HermesClient.TRANSCRIBE_TIMEOUT_S * 2 <= HermesClient.AUDIO_TIMEOUT_S)
    }
}
