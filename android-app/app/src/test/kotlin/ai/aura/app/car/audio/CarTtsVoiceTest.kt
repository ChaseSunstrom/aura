package ai.aura.app.car.audio

import dev.headway.audio.CarAudioSource
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The phone's own text-to-speech is what the car falls back to when the server's JARVIS
 * voice can't be made, and the engine's default English voice is a woman's (the owner,
 * 2026-10-29: "its a girl speaking instead of jarvis"). The fallback picks a male voice
 * by the Google engine's voice codes when the phone has one.
 */
class CarTtsVoiceTest {
    @Test
    fun aBritishMaleVoiceBeatsTheDefaultFemaleOne() {
        val names = listOf("en-us-x-sfg-local", "en-us-x-iom-local", "en-gb-x-rjs-local", "en-gb-x-gba-local")
        assertEquals("en-gb-x-rjs-local", CarAudioSource.preferredVoice(names))
    }

    @Test
    fun theOnPhoneCopyBeatsTheNetworkOne() {
        // A network voice needs the data link the fallback exists to cover for.
        val names = listOf("en-gb-x-rjs-network", "en-gb-x-rjs-local")
        assertEquals("en-gb-x-rjs-local", CarAudioSource.preferredVoice(names))
    }

    @Test
    fun anAmericanMaleVoiceWhenThereIsNoBritishOne() {
        assertEquals("en-us-x-iom-local", CarAudioSource.preferredVoice(listOf("en-us-x-sfg-local", "en-us-x-iom-local")))
    }

    @Test
    fun noKnownMaleVoiceLeavesTheEngineDefault() {
        assertNull(CarAudioSource.preferredVoice(listOf("en-us-x-sfg-local", "de-de-x-nfh-local")))
    }
}
