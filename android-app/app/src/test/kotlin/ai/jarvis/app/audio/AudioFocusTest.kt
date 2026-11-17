package ai.jarvis.app.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/** A turn's audio focus ducks the music and ends on a call (contract "focus"; AudioAttention.kt). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class AudioFocusTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val shadow = shadowOf(context.getSystemService(AudioManager::class.java))
    private var losses = 0

    private fun focus() = TurnFocus(context) { losses++ }

    @Test fun `taking focus asks to duck the music with navigation-guidance speech`() {
        val f = focus()
        assertTrue(f.take())
        val request = shadow.lastAudioFocusRequest.audioFocusRequest
        assertEquals(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK, request.focusGain)
        assertEquals(AudioAttributes.USAGE_ASSISTANCE_NAVIGATION_GUIDANCE, request.audioAttributes.usage)
    }

    @Test fun `a call arriving ends the turn, a request to duck does not`() {
        val f = focus()
        f.take()
        val listener = shadow.lastAudioFocusRequest.listener
        listener.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK)
        assertEquals(0, losses)
        listener.onAudioFocusChange(AudioManager.AUDIOFOCUS_LOSS_TRANSIENT)
        assertEquals(1, losses)
        assertFalse(f.held)
    }

    @Test fun `release gives the focus back once, and only if it was taken`() {
        val f = focus()
        f.release()
        assertEquals(null, shadow.lastAbandonedAudioFocusRequest)
        f.take()
        f.release()
        assertNotNull(shadow.lastAbandonedAudioFocusRequest)
    }
}
