package ai.aura.app.car.voice

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** One "drive started" per drive (2026-10-07: five for one drive, each a full model turn). */
class DriveStartTest {
    private val t0 = 1_791_388_000_000L

    @Test
    fun `a reconnect inside fifteen minutes is the same drive`() {
        assertFalse(DriveStart.isNewDrive(t0, t0 + 2 * 60_000L))
        assertFalse(DriveStart.isNewDrive(t0, t0 + DriveStart.GAP_MS))
    }

    @Test
    fun `the first session, a long gap and a clock gone backwards are new drives`() {
        assertTrue(DriveStart.isNewDrive(0L, t0))
        assertTrue(DriveStart.isNewDrive(t0, t0 + DriveStart.GAP_MS + 1))
        assertTrue(DriveStart.isNewDrive(t0, t0 - 1))
    }
}

/**
 * What the drive-start event asks (the owner, 2026-10-08: "jarvis isn't being very proactive
 * ... with the features from the brainstorm"). The drive start is the moment he meets Jarvis
 * every day, so the planned features hang on it -- and it stays quiet when nothing needs him.
 */
class DriveStartPromptTest {
    private val prompt = CarTurn.DRIVE_START

    @Test
    fun `it reads the calendar and who has messaged, never asking for approval`() {
        assertTrue(prompt.contains("phone_action read_calendar"))
        // read_notifications is answered before the phone's tier guard; read_sms is
        // confirm-tier and would put an approval in front of him at every drive start.
        assertTrue(prompt.contains("phone_action read_notifications"))
        assertFalse(prompt.contains("read_sms"))
        assertTrue(prompt.contains("names only"))
    }

    @Test
    fun `it offers a radio show only on a long routed drive, and is otherwise quiet`() {
        assertTrue(prompt.contains("radio show"))
        assertTrue(prompt.contains("twenty minutes"))
        assertTrue(prompt.endsWith("reply with exactly ${CarTurn.QUIET}."))
    }
}
