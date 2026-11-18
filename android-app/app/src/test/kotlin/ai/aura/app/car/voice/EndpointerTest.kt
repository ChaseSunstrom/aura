package ai.aura.app.car.voice

import dev.headway.protocol.channel.MicrophoneFormat
import dev.headway.protocol.channel.PcmChunk
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EndpointerTest {
    private val format = MicrophoneFormat(16_000, 16, 1)
    private fun chunk(level: Int) = PcmChunk(0L, ShortArray(1_024) { level.toShort() })

    @Test fun `the floor follows the cabin down after a music tail`() {
        // The 2026-11-10 drive: the car was still playing music when the microphone opened,
        // the floor was measured on it, and six listens in a row heard "no speech".
        val e = CarVoiceStream.Endpointer(format)
        repeat(5) { e.accept(chunk(6_000)) }
        repeat(10) { e.accept(chunk(100)) }
        assertFalse(e.speechSeen)
        e.accept(chunk(3_000))
        assertTrue("speech quieter than the music that was playing is still speech", e.speechSeen)
    }

    @Test fun `a steady cabin is not speech`() {
        val e = CarVoiceStream.Endpointer(format)
        repeat(40) { e.accept(chunk(1_000)) }
        assertFalse(e.speechSeen)
    }
}
