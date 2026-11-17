package ai.jarvis.app.car.service

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The OS restarts the car link after the process dies with a null intent (the last command
 * may have been a notification button, which returns START_STICKY and redelivers nothing).
 * Without the remembered car the link restarted with no address and the drive had no phone side.
 */
@RunWith(RobolectricTestRunner::class)
class CarLinkResumeTest {
    private val app = ApplicationProvider.getApplicationContext<Application>()

    @Test fun `a restart with no intent finds the car it was linking`() {
        CarLinkResume.remember(app, "AA:BB", now = 1_000L)
        assertEquals("AA:BB", CarLinkResume.resolve(app, intentAddress = null, current = null, restarted = true, now = 2_000L))
    }

    @Test fun `a person pressing Connect is not handed an old car`() {
        CarLinkResume.remember(app, "AA:BB", now = 1_000L)
        assertNull(CarLinkResume.resolve(app, intentAddress = null, current = null, restarted = false, now = 2_000L))
        assertEquals("CC:DD", CarLinkResume.resolve(app, intentAddress = "CC:DD", current = "AA:BB", restarted = true, now = 2_000L))
    }

    @Test fun `a stop or a given-up link forgets the car`() {
        CarLinkResume.remember(app, "AA:BB", now = 1_000L)
        CarLinkResume.forget(app)
        assertNull(CarLinkResume.resolve(app, null, null, restarted = true, now = 2_000L))
    }

    @Test fun `a car remembered yesterday is not chased today`() {
        CarLinkResume.remember(app, "AA:BB", now = 1_000L)
        assertNull(CarLinkResume.resolve(app, null, null, restarted = true, now = 1_000L + CarLinkResume.MAX_AGE_MS + 1))
    }
}
