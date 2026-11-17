package ai.jarvis.app.car.service

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The line and the clock only (T-1217); CarLinkService's wiring and the real broadcasts are not under test. */
class ResetReportTest {
    private val reset = "channel CONTROL closed: Connection reset"

    private fun clock(): ResetReport.ProfileClock = ResetReport.ProfileClock().apply {
        seed("headset", "disconnected", 1_000)
        seed("a2dp", "disconnected", 1_000)
        changed("headset", "connected", 70_000)
        changed("a2dp", "connected", 70_000)
    }

    @Test
    fun `a session ended on a connection reset gets one line naming each sink, focus, drop and bluetooth`() {
        val line = ResetReport.line(
            cause = reset,
            sinks = listOf(ResetReport.Sink("media", 5_120, 3), ResetReport.Sink("guidance", 40, 0)),
            focus = ResetReport.Focus("AUDIO_FOCUS_STATE_GAIN_TRANSIENT_GUIDANCE_ONLY", 100_000),
            lastBacklogDropMs = 118_000,
            bluetooth = clock().describe(120_000),
            nowMs = 120_000,
        )!!
        assertFalse(line.contains("\n"))
        assertTrue(line, "media 5120 sent, 3 unacknowledged" in line)
        assertTrue(line, "guidance 40 sent, 0 unacknowledged" in line)
        assertTrue(line, "20000 ms since the car's last audio focus change (AUDIO_FOCUS_STATE_GAIN_TRANSIENT_GUIDANCE_ONLY)" in line)
        assertTrue(line, "2000 ms since the media queue last dropped audio" in line)
        assertTrue(line, "headset connected (50000 ms since it changed)" in line)
        assertTrue(line, "a2dp connected (50000 ms since it changed)" in line)
    }

    @Test
    fun `no focus change and no drop say never, and an unchanged profile says only for at least`() {
        val seeded = ResetReport.ProfileClock().apply {
            seed("headset", "connected", 1_000)
            seed("a2dp", "connected", 1_000)
        }
        val line = ResetReport.line(reset, emptyList(), null, null, seeded.describe(11_000), 11_000)!!
        assertTrue(line, "no audio sink open" in line)
        assertTrue(line, "never changed audio focus" in line)
        assertTrue(line, "never dropped audio (never)" in line)
        assertTrue(line, "headset connected (unchanged for at least 10000 ms)" in line)
    }

    @Test
    fun `a session that ended any other way gets no line`() {
        assertNull(ResetReport.line("peer closed", emptyList(), null, null, "", 0))
        assertNull(ResetReport.line(null, emptyList(), null, null, "", 0))
    }

    @Test
    fun `a broadcast repeating the state keeps the time it first changed`() {
        val c = clock()
        c.changed("a2dp", "connected", 90_000)
        assertTrue(c.describe(100_000).contains("a2dp connected (30000 ms since it changed)"))
        assertEquals(true, c.describe(100_000).startsWith("Bluetooth: headset"))
    }
}
