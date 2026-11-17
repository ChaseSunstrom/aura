package ai.jarvis.app.assist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The phone's own speech to text, run on this host with onnxruntime's JVM build and the
 * weights the house mirrors. Skipped where they are not on disk (CI): set
 * AURA_MOONSHINE_DIR, or put the three files in ~/.cache/jarvis/moonshine-base.
 * The clips are CosyVoice's Jarvis voice, 16 kHz mono, from the 2026-10-08 prototype.
 */
class OnPhoneSpeechTest {

    private val speech: OnPhoneSpeech? by lazy {
        val dir = File(System.getenv("AURA_MOONSHINE_DIR") ?: "${System.getProperty("user.home")}/.cache/jarvis/moonshine-base")
        if (dir.isDirectory) OnPhoneSpeech.open(dir) else null
    }

    private fun clip(name: String): FloatArray =
        OnPhoneSpeech.samplesOfWav(javaClass.getResourceAsStream("/speech/$name")!!.readBytes())

    @Test
    fun the_consoles_wav_is_read_as_its_samples() {
        val samples = clip("remind-mom.wav")
        assertEquals(53_760, samples.size)
        assertTrue(samples.all { it >= -1f && it < 1f } && samples.any { it != 0f })
    }

    private fun words(s: String) = s.lowercase().replace(Regex("[^a-z0-9: ]"), "").trim()

    @Test
    fun a_spoken_command_is_written_down_on_the_phone() {
        val stt = speech
        assumeTrue("the Moonshine weights are not on this machine", stt != null)
        assertEquals("turn on the porch light and text isa that i am on my way", words(stt!!.transcribe(clip("porch-light.wav"))))
        assertEquals("remind me to call mom at 6:30", words(stt.transcribe(clip("remind-mom.wav"))))
    }

    @Test
    fun silence_is_no_words() {
        val stt = speech
        assumeTrue("the Moonshine weights are not on this machine", stt != null)
        assertEquals("", words(stt!!.transcribe(FloatArray(16_000))))
    }
}
