package ai.jarvis.app.car.audio

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The rule only (T-0990): the wait itself and the pump's use of it are not under test. */
class GrantGapTest {
    @Test
    fun `a capture that ended because the grant went keeps the car's focus`() {
        assertTrue(grantGapHoldsFocus(endedByItself = true, grantStillLive = false))
    }

    @Test
    fun `a dead link, a cancel and a capture that failed under a live grant give it back`() {
        assertFalse(grantGapHoldsFocus(endedByItself = false, grantStillLive = false))
        assertFalse(grantGapHoldsFocus(endedByItself = false, grantStillLive = true))
        assertFalse(grantGapHoldsFocus(endedByItself = true, grantStillLive = true))
    }

    @Test
    fun `the wait is longer than a re-share and shorter than a song`() {
        assertTrue(GRANT_GAP_HOLD_MILLIS in 10_000L..30_000L)
    }
}
