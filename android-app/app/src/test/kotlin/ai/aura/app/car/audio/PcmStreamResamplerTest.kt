package ai.aura.app.car.audio

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin
import kotlin.math.sqrt

class PcmStreamResamplerTest {
    private fun tone(hz: Double, rate: Int, frames: Int, peak: Double = 10_000.0): ByteArray {
        val out = ByteArray(frames * 2)
        for (i in 0 until frames) {
            val v = (peak * sin(2 * PI * hz * i / rate)).toInt()
            out[2 * i] = v.toByte()
            out[2 * i + 1] = (v shr 8).toByte()
        }
        return out
    }

    private fun rms(pcm: ByteArray, skipFrames: Int): Double {
        var sum = 0.0
        var n = 0
        for (i in skipFrames until pcm.size / 2) {
            val v = ((pcm[2 * i + 1].toInt() shl 8) or (pcm[2 * i].toInt() and 0xff)).toDouble()
            sum += v * v
            n++
        }
        return sqrt(sum / n)
    }

    @Test fun `ragged pieces come out exactly as the whole does`() {
        // Her voice arrives off the network in pieces of any size, odd bytes included; each
        // boundary used to be a seam (the owner, 2026-11-11: "messed up and choppy").
        val line = tone(440.0, 24_000, 4_800)
        val whole = PcmStreamResampler(24_000, 16_000).feed(line)
        val ragged = PcmStreamResampler(24_000, 16_000)
        val out = java.io.ByteArrayOutputStream()
        var at = 0
        for (size in generateSequence(1) { (it * 7 + 3) % 611 + 1 }) {
            if (at >= line.size) break
            val end = minOf(line.size, at + size)
            out.write(ragged.feed(line.copyOfRange(at, end)))
            at = end
        }
        assertArrayEquals(whole, out.toByteArray())
        assertTrue("a second of 24 kHz is a second of 16 kHz", abs(whole.size / 2 - 3_200) <= 2)
    }

    @Test fun `what the car cannot carry is filtered out, not folded back`() {
        // 10 kHz is above the 16 kHz channel's 8 kHz ceiling: unfiltered it lands at 6 kHz.
        val high = PcmStreamResampler(24_000, 16_000).feed(tone(10_000.0, 24_000, 4_800))
        val low = PcmStreamResampler(24_000, 16_000).feed(tone(1_000.0, 24_000, 4_800))
        val full = 10_000.0 / sqrt(2.0)
        assertTrue("10 kHz is down 20 dB: ${rms(high, 64)}", rms(high, 64) < full / 10)
        assertEquals("1 kHz passes", full, rms(low, 64), full * 0.1)
    }

    @Test fun `the same rate passes through and a lone byte waits`() {
        val same = PcmStreamResampler(16_000, 16_000)
        assertArrayEquals(byteArrayOf(1, 2), same.feed(byteArrayOf(1, 2, 3)))
        assertArrayEquals(byteArrayOf(3, 4), same.feed(byteArrayOf(4)))
        assertEquals(0, PcmStreamResampler(24_000, 16_000).feed(ByteArray(0)).size)
    }

    @Test fun `music is withheld while she listens, and not for ever`() {
        assertTrue(musicHushed(listening = true, forMillis = 0))
        assertTrue(musicHushed(listening = true, forMillis = 14_999))
        // A phase stuck on listening must not cost the drive its music.
        assertFalse(musicHushed(listening = true, forMillis = 15_000))
        assertFalse(musicHushed(listening = false, forMillis = 0))
    }
}
