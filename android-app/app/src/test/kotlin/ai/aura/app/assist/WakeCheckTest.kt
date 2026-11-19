package ai.aura.app.assist

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/**
 * The wake word's second stage against `tests/contracts/wake_check.json` (T-1573); the python
 * mirror (`tools/wake_check_test.py`) reads the same rows. The HTTP call and the service's
 * wiring are not under test here.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class WakeCheckTest {
    private val contract = JSONObject(File("../../tests/contracts/wake_check.json").readText())

    @Test
    fun `the timeout and the window are the contract's`() {
        assertEquals(contract.getLong("timeout_ms"), WakeCheck.TIMEOUT_MS)
        assertEquals(contract.getInt("window_ms"), WakeCheck.WINDOW_MS)
    }

    @Test
    fun `a wake is dropped only on the route's drop`() {
        val rows = contract.getJSONArray("phone")
        for (i in 0 until rows.length()) {
            val row = rows.getJSONObject(i)
            val status = if (row.isNull("status")) null else row.getInt("status")
            val body = if (row.isNull("body")) null else row.getJSONObject("body").toString()
            val v = WakeCheck.decide(status, body)
            assertEquals(row.getString("name"), row.getBoolean("open"), v.open)
            assertEquals(row.getString("name"), row.getString("verdict"), v.verdict)
        }
    }

    @Test
    fun `each wake writes one decision line`() {
        val rows = contract.getJSONArray("line")
        for (i in 0 until rows.length()) {
            val row = rows.getJSONObject(i)
            val sim = if (row.isNull("similarity")) null else row.getDouble("similarity")
            val v = WakeCheck.Verdict(row.getBoolean("open"), row.getString("verdict"), sim)
            val text = WakeCheck.line(row.getString("head"), row.getDouble("score").toFloat(), row.getDouble("bar").toFloat(), row.getBoolean("music"), v)
            assertEquals(row.getString("text"), text)
        }
    }

    @Test
    fun `the window keeps the last two seconds, oldest first`() {
        val w = WakeWindow(sampleRate = 4) // 16 bytes
        w.push(ByteArray(10) { it.toByte() }, 10)
        assertArrayEquals(ByteArray(10) { it.toByte() }, w.snapshot())
        w.push(ByteArray(10) { (10 + it).toByte() }, 8)
        assertArrayEquals(ByteArray(16) { (2 + it).toByte() }, w.snapshot())
        w.push(ByteArray(40) { (100 + it).toByte() }, 40)
        assertArrayEquals(ByteArray(16) { (124 + it).toByte() }, w.snapshot())
        w.clear()
        assertEquals(0, w.snapshot().size)
    }
}
