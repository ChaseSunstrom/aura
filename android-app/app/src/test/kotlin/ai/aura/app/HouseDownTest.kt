package ai.aura.app

import ai.aura.app.channel.HouseDown
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone tells the owner once when the house has been quiet for 15 minutes on the Wi-Fi
 * connection it last answered on (T-0578). Times are monotonic milliseconds; a network is the
 * default Wi-Fi connection's handle, or null for mobile data / none.
 */
class HouseDownTest {
    private val min = 60_000L
    private val home = 11L
    private val elsewhere = 12L

    /** A house that answered on [home] at t=0. */
    private fun athome() = HouseDown().also { it.answered(0, home) }

    @Test fun `quiet for 15 minutes on home Wi-Fi is one notice carrying when it went quiet`() {
        val d = athome()
        assertNull(d.failed(2 * min, home)) // the first failure starts the clock
        var notices = 0
        var quietSince: Long? = null
        // one failed attempt a minute to 22 minutes: the notice is at 17:00, once
        for (t in 3..22) d.failed(t * min, home)?.let { notices++; quietSince = it }
        assertEquals(1, notices)
        assertEquals(2 * min, quietSince)
    }

    @Test fun `14 59 does not notify and exactly 15 00 does`() {
        val d = athome()
        assertNull(d.failed(1 * min, home))
        assertNull(d.failed(1 * min + 15 * min - 1, home))
        assertEquals(1 * min, d.failed(1 * min + 15 * min, home))
    }

    @Test fun `never twice for one outage`() {
        val d = athome()
        d.failed(0, home)
        assertEquals(0L, d.failed(15 * min, home))
        assertNull(d.failed(16 * min, home))
        assertNull(d.tick(40 * min, home))
        assertNull(d.failed(41 * min, home))
    }

    @Test fun `an answer re-arms it, and the first answer after a notice says so`() {
        val d = athome()
        d.failed(0, home)
        assertEquals(0L, d.failed(15 * min, home))
        assertTrue(d.answered(16 * min, home))
        assertFalse(d.answered(17 * min, home))
        // a new outage has its own clock and its own notice
        assertNull(d.failed(20 * min, home))
        assertNull(d.failed(34 * min + 59_999, home))
        assertEquals(20 * min, d.failed(35 * min, home))
    }

    @Test fun `failure answer failure restarts the clock`() {
        val d = athome()
        d.failed(1 * min, home)
        d.answered(10 * min, home)
        d.failed(11 * min, home)
        assertNull(d.failed(16 * min, home)) // 15 min after the first failure, 5 after the second
        assertEquals(11 * min, d.failed(26 * min, home))
    }

    @Test fun `a burst of failures in one tick is one notice`() {
        val d = athome()
        d.failed(0, home)
        val burst = List(5) { d.failed(15 * min, home) }
        assertEquals(listOf<Long?>(0L, null, null, null, null), burst)
    }

    @Test fun `another network is not home and its failures do not accumulate`() {
        val d = athome()
        for (t in 0..40) assertNull(d.failed(t * min, elsewhere))
        // coming back to the first connection starts nothing until it fails there
        assertNull(d.failed(41 * min, home))
    }

    @Test fun `a network change mid outage clears the clock`() {
        val d = athome()
        d.failed(0, home)
        d.networkChanged(elsewhere)
        assertNull(d.failed(15 * min, elsewhere))
        // back on the old handle: the house has not answered on it since, so it is not home yet
        d.networkChanged(home)
        assertNull(d.failed(30 * min, home))
        assertNull(d.failed(60 * min, home))
    }

    @Test fun `a house that answers on the new connection makes it home`() {
        val d = athome()
        d.failed(0, home)
        d.networkChanged(elsewhere)
        d.answered(5 * min, elsewhere)
        assertNull(d.failed(6 * min, elsewhere))
        assertEquals(6 * min, d.failed(21 * min, elsewhere))
    }

    @Test fun `mobile data never notifies, nor does a house that never answered here`() {
        val d = athome()
        for (t in 0..40) assertNull(d.failed(t * min, null))
        val fresh = HouseDown()
        for (t in 0..40) assertNull(fresh.failed(t * min, home))
        fresh.answered(41 * min, null) // answered over mobile data: still not a home Wi-Fi
        for (t in 42..90) assertNull(fresh.failed(t * min, null))
    }

    @Test fun `the minute tick fires the notice at 15 minutes when retries are further apart`() {
        val d = athome()
        d.failed(0, home)
        d.failed(10 * min, home) // the link now waits 5 minutes between tries
        assertNull(d.tick(14 * min, home))
        assertEquals(0L, d.tick(15 * min, home))
        assertNull(d.failed(20 * min, home))
    }

    @Test fun `a tick alone after the phone slept does not notify, only failures it saw count`() {
        val d = athome()
        d.failed(0, home)
        // asleep: no failures for 30 minutes, then the tick is the first to run
        assertNull(d.tick(30 * min, home))
        // the link is nudged awake and fails once more: that is a failure it saw
        assertEquals(0L, d.failed(31 * min, home))
    }

    @Test fun `a tick with no failure seen starts nothing`() {
        val d = athome()
        for (t in 0..40) assertNull(d.tick(t * min, home))
    }
}
