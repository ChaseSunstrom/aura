package ai.jarvis.app.channel

import android.content.Intent
import android.os.PowerManager
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Half the house's pushes waited hours for a phone that was in a pocket (2026-10-03..08). */
class WakeNudgeTest {
    @Test fun `picking the phone up or leaving Doze checks the link, falling asleep does not`() {
        assertTrue(WakeNudge.wanted(Intent.ACTION_USER_PRESENT, deviceIdle = false))
        assertTrue(WakeNudge.wanted(Intent.ACTION_SCREEN_ON, deviceIdle = true))
        assertTrue(WakeNudge.wanted(PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED, deviceIdle = false))
        assertFalse(WakeNudge.wanted(PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED, deviceIdle = true))
        assertFalse(WakeNudge.wanted(Intent.ACTION_SCREEN_OFF, deviceIdle = false))
        assertFalse(WakeNudge.wanted(null, deviceIdle = false))
    }
}
