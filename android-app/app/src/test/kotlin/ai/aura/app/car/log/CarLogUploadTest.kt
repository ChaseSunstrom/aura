package ai.aura.app.car.log

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * A drive's log reaches Jarvis even when the end of the drive could not (T-0742): the
 * adapter's store held no car log from a week of drives while the owner reported the car
 * dropping, because the one upload ran from the car, where a VPN app kept Jarvis off the
 * network (T-0713), and a failed one was dropped.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CarLogUploadTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @get:Rule
    val dir = TemporaryFolder()

    @Test
    fun `a drive log that never reached Jarvis is sent later, and a sent one is not sent again`() {
        val now = System.currentTimeMillis()
        fun log(name: String, ageMs: Long) = dir.newFile(name).apply { writeText("drive"); setLastModified(now - ageMs) }
        val yesterday = log("headway-20261107-180000.txt", 26 * HOUR)
        val today = log("headway-20261108-171500.txt", HOUR)
        val crash = log("headway-crash-20261108-171400.txt", HOUR + 60_000L)
        // Older than the store keeps a log: sending it would only be thrown away.
        val stale = log("headway-20261020-090000.txt", 19 * 24 * HOUR)
        val exports = listOf(today, crash, yesterday, stale)

        assertEquals(listOf(yesterday, crash, today), CarLogUpload.pending(exports, CarLogUpload.sent(context), now))
        CarLogUpload.markSent(context, yesterday)
        CarLogUpload.markSent(context, crash)
        assertEquals(listOf(today), CarLogUpload.pending(exports, CarLogUpload.sent(context), now))
    }

    private companion object {
        const val HOUR = 3_600_000L
    }
    // T-0916: the upload goes through the console's relay, and SvelteKit's CSRF check answers
    // 403 to a form-type POST (text/plain is one) with no matching Origin, before the relay
    // runs: every drive log for four days was refused. The adapter reads only x-artifact-mime.
    @Test
    fun `a drive log is not sent as a form type the console's CSRF check refuses`() {
        val log = dir.newFile("headway-1.txt").apply { writeText("a drive") }
        val request = CarLogUpload.request("http://console.invalid/hermes", "the-key", log)
        val type = request.body!!.contentType().toString()
        assertEquals("application/octet-stream", type)
        assertEquals("text/plain", request.header("x-artifact-mime"))
        assertEquals("http://console.invalid/hermes/api/artifacts", request.url.toString())
    }
}
