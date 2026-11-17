package ai.jarvis.app.car.audio

import aap_protobuf.service.control.message.AudioFocusRequestTypeOuterClass.AudioFocusRequestType.AUDIO_FOCUS_GAIN
import aap_protobuf.service.control.message.AudioFocusRequestTypeOuterClass.AudioFocusRequestType.AUDIO_FOCUS_GAIN_TRANSIENT_MAY_DUCK
import aap_protobuf.service.control.message.AudioFocusRequestTypeOuterClass.AudioFocusRequestType.AUDIO_FOCUS_RELEASE
import aap_protobuf.service.control.message.AudioFocusStateTypeOuterClass.AudioFocusStateType
import androidx.test.core.app.ApplicationProvider
import dev.headway.audio.PhoneAudioFocus
import dev.headway.protocol.channel.AudioFocus
import dev.headway.protocol.channel.AudioFocusState
import dev.headway.protocol.framing.AapMessage
import dev.headway.protocol.io.Cryptor
import dev.headway.protocol.io.MessageChannel
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The end of a prompt while music is relayed (the owner, 2026-11-10: tapping AURA in the car
 * "messes up the audio"). The car's log had a RELEASE after every prompt: the head unit answered
 * LOSS, went to its own source, and came back only when the media pump asked again.
 */
@RunWith(RobolectricTestRunner::class)
class PhoneAudioFocusTest {
    private val car = AudioFocus(Silent())
    private val phone = PhoneAudioFocus(ApplicationProvider.getApplicationContext(), car)

    @Test
    fun `a prompt over music hands the car straight back to the music, never a release`() = runBlocking {
        phone.requestForMedia()
        assertTrue(phone.requestForPrompt(mayDuck = true))
        assertTrue(phone.endPrompt())
        assertEquals(listOf(AUDIO_FOCUS_GAIN, AUDIO_FOCUS_GAIN_TRANSIENT_MAY_DUCK, AUDIO_FOCUS_GAIN), car.requests)
        // The player on the phone is let go of too, so it is not left ducked.
        assertEquals(PhoneAudioFocus.PhoneFocus.NONE, phone.phoneFocus)
        // And again for the next one: a tap right after a reply.
        phone.requestForPrompt(mayDuck = true)
        assertTrue(phone.endPrompt())
        assertEquals(AUDIO_FOCUS_GAIN, car.requests.last())
    }

    @Test
    fun `a prompt with no music under it still gives the car its focus back`() = runBlocking {
        phone.requestForPrompt(mayDuck = true)
        assertFalse(phone.endPrompt())
        assertEquals(listOf(AUDIO_FOCUS_GAIN_TRANSIENT_MAY_DUCK, AUDIO_FOCUS_RELEASE), car.requests)
    }

    @Test
    fun `music that went quiet, or that the car took back, is not handed focus again`() = runBlocking {
        phone.requestForMedia()
        phone.release() // the pump's ten seconds of silence
        phone.requestForPrompt(mayDuck = true)
        assertFalse(phone.endPrompt())

        phone.requestForMedia()
        phone.onCarFocus(AudioFocusState(AudioFocusStateType.AUDIO_FOCUS_STATE_LOSS))
        phone.requestForPrompt(mayDuck = true)
        assertFalse(phone.endPrompt())
        assertEquals(AUDIO_FOCUS_RELEASE, car.requests.last())
    }

    /** The control channel, sending into nothing: what was asked is [AudioFocus.requests]. */
    private class Silent : MessageChannel {
        override var cryptor: Cryptor? = null
        override suspend fun send(message: AapMessage) = Unit
        override suspend fun receive(): AapMessage = awaitCancellation()
    }
}
