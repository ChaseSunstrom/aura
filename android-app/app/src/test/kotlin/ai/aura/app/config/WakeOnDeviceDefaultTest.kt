package ai.aura.app.config

import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The owner, 2026-10-08: "Jarvis kills my battery even without screen sharing". With
 * on-device detection off by default, "Hey Jarvis" streamed the microphone to the server
 * around the clock; a phone that never touched the switch now listens on the phone.
 */
@RunWith(RobolectricTestRunner::class)
class WakeOnDeviceDefaultTest {
    @Test fun `the wake word is heard on the phone unless he turned that off`() {
        val config = JarvisConfig(ApplicationProvider.getApplicationContext())
        assertTrue(config.wakeWordOnDevice)
        config.wakeWordOnDevice = false
        assertFalse(JarvisConfig(ApplicationProvider.getApplicationContext()).wakeWordOnDevice)
    }
}
