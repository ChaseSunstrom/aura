package ai.aura.app.car.voice

import ai.aura.app.assist.hermes.HermesClient
import ai.aura.app.assist.hermes.HermesClient.Transcribed
import ai.aura.app.assist.hermes.HermesClient.Transcribed.Failed
import ai.aura.app.assist.hermes.HermesClient.Transcribed.Got
import ai.aura.app.assist.hermes.HermesClient.Transcribed.Offline
import ai.aura.app.assist.hermes.WavAudio
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class JarvisTranscriberTest {
    @Test fun `one utterance is one WAV at the car's rate, and the transcript and ticket are kept`() {
        val sent = mutableListOf<ByteArray>()
        val stt = JarvisTranscriber(16_000) { wav -> sent += wav; Got(HermesClient.Heard("What's on My Calendar", false, "owner", "t-1")) }
        stt.accept(ByteArray(3200))
        stt.accept(ByteArray(3200), 1600)
        assertEquals("what's on my calendar", stt.finish())
        assertEquals(1, sent.size)
        val header = WavAudio.readHeader(sent.single())!!
        assertEquals(16_000, header.sampleRate)
        assertEquals(4800, sent.single().size - 44)
        assertEquals("What's on My Calendar", stt.lastHeard?.transcript)
        assertEquals("t-1", stt.lastHeard?.ticket)
        assertFalse(stt.offline)
    }

    @Test fun `silence sends nothing`() {
        var calls = 0
        val stt = JarvisTranscriber(16_000) { calls++; Offline }
        assertEquals("", stt.finish())
        assertEquals(0, calls)
    }

    @Test fun `a network failure is offline, not an empty sentence`() {
        val stt = JarvisTranscriber(16_000) { Offline }
        stt.accept(ByteArray(320))
        assertEquals("", stt.finish())
        assertTrue(stt.offline)
        assertNull(stt.failure)
        assertNull(stt.lastHeard)
    }

    @Test fun `a server or decode failure is itself, with its reason, never offline`() {
        val stt = JarvisTranscriber(16_000) { Failed("/api/audio/transcribe answered 500") }
        stt.accept(ByteArray(320))
        assertEquals("", stt.finish())
        assertFalse(stt.offline)
        assertEquals("/api/audio/transcribe answered 500", stt.failure)
        assertNull(stt.lastHeard)
    }

    @Test fun `a transcript after a failure clears it`() {
        var next: Transcribed = Failed("500")
        val stt = JarvisTranscriber(16_000) { next }
        stt.accept(ByteArray(320)); stt.finish()
        next = Got(HermesClient.Heard("ok", false, null, null))
        stt.accept(ByteArray(320))
        assertEquals("ok", stt.finish())
        assertNull(stt.failure)
        assertFalse(stt.offline)
    }

    @Test fun `reset starts the next utterance fresh`() {
        val sizes = mutableListOf<Int>()
        val stt = JarvisTranscriber(16_000) { wav -> sizes += wav.size - 44; Got(HermesClient.Heard("x", false, null, null)) }
        stt.accept(ByteArray(100)); stt.reset(); stt.accept(ByteArray(10)); stt.finish()
        assertEquals(listOf(10), sizes)
    }
}
