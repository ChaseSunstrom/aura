package ai.jarvis.app.channel

import android.app.Application
import android.content.Intent
import android.os.Looper
import android.os.PowerManager
import androidx.test.core.app.ApplicationProvider
import java.util.concurrent.TimeUnit
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.shadows.ShadowLooper

/**
 * Presence is pushed on the events that change it (screen, power, audio route), not read on a
 * five-second loop (AUDIT V9). The reporter's only timers are one-shots: the rate floor, the
 * heartbeat, and the moment "active" lapses.
 */
@RunWith(RobolectricTestRunner::class)
class PresenceEventsTest {
    private val app = ApplicationProvider.getApplicationContext<Application>()
    private val looper = Looper.getMainLooper()
    private var now = 1_000_000L
    private val sent = mutableListOf<JSONObject>()
    private val reporter = PresenceReporter(
        app,
        emit = { _, data -> sent += data; true },
        clock = { now },
        looper = looper,
    )

    @After fun stop() = reporter.stop()

    private fun idle() = shadowOf(looper).idle()

    @Test fun `the first report goes out on start`() {
        reporter.start(); idle()
        assertEquals(1, reporter.sends)
    }

    @Test fun `a screen-off broadcast is reported at once, without waiting for a tick`() {
        reporter.start(); idle()
        now += 10_000
        shadowOf(app.getSystemService(PowerManager::class.java)).turnScreenOn(false)
        app.sendBroadcast(Intent(Intent.ACTION_SCREEN_OFF)); idle()
        assertEquals(2, reporter.sends)
        assertEquals(false, sent.last().getBoolean("screen_on"))
    }

    @Test fun `a quiet phone sends nothing until the heartbeat, then exactly one`() {
        reporter.start(); idle()
        now += 10_000
        ShadowLooper.idleMainLooper(10, TimeUnit.MINUTES)
        assertEquals(1, reporter.sends)
        now += 61_000
        ShadowLooper.idleMainLooper(2, TimeUnit.MINUTES)
        assertEquals(2, reporter.sends)
    }

    @Test fun `after stop a broadcast reports nothing`() {
        reporter.start(); idle()
        reporter.stop()
        now += 10_000
        app.sendBroadcast(Intent(Intent.ACTION_SCREEN_ON)); idle()
        assertEquals(1, reporter.sends)
    }

    @Test fun `the next look is the soonest deadline and never the old five-second poll`() {
        // Just sent: only the rate floor is near.
        assertEquals(PresenceThrottle.MIN_INTERVAL_MS, PresenceThrottle.nextCheckMs(0L, null))
        // Past the floor: the heartbeat is the deadline.
        assertEquals(PresenceThrottle.HEARTBEAT_MS - 10_000L, PresenceThrottle.nextCheckMs(10_000L, null))
        // "Active" lapsing sooner than the heartbeat wins.
        assertEquals(20_000L, PresenceThrottle.nextCheckMs(10_000L, 20_000L))
        // Overdue never goes negative.
        assertTrue(PresenceThrottle.nextCheckMs(PresenceThrottle.HEARTBEAT_MS + 5, null) >= 0L)
    }
}
