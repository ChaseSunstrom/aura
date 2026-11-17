package ai.jarvis.app.car.voice

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** One "drive ended" per real drive: the head unit drops the link mid-drive, and a stop is not an arrival. */
class DriveEndTest {
    private val t0 = 1_791_388_000_000L

    @Test
    fun `a car session of a few minutes is not a drive`() {
        assertFalse(DriveEnd.isRealDrive(t0, t0 + DriveEnd.MIN_DRIVE_MS - 1))
        assertTrue(DriveEnd.isRealDrive(t0, t0 + DriveEnd.MIN_DRIVE_MS))
    }

    @Test
    fun `it is told only when no session came back after the end`() {
        assertTrue(DriveEnd.stillOver(endedAtMs = t0, lastSeenMs = t0))
        // A reconnect marks a newer time: the same drive, so it has not ended.
        assertFalse(DriveEnd.stillOver(endedAtMs = t0, lastSeenMs = t0 + 60_000L))
    }

    @Test
    fun `the event releases parked asks, lists open loops, asks what went wrong, and can be quiet`() {
        val p = CarTurn.DRIVE_END
        assertTrue(p.startsWith("[event: drive ended]"))
        assertTrue(p.contains("later_release {when: parked}"))
        assertTrue(p.contains("loops_list"))
        assertTrue(p.contains("Anything I got wrong on that drive?"))
        assertTrue(p.endsWith("reply with exactly ${CarTurn.QUIET}."))
    }
}
