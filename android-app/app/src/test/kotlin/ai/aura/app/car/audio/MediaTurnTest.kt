package ai.aura.app.car.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MediaTurnTest {
    private fun level(value: Int, frames: Int): ByteArray {
        val out = ByteArray(frames * 2)
        for (i in 0 until frames) {
            out[2 * i] = value.toByte()
            out[2 * i + 1] = (value shr 8).toByte()
        }
        return out
    }

    private fun sample(pcm: ByteArray, i: Int): Int = (pcm[2 * i + 1].toInt() shl 8) or (pcm[2 * i].toInt() and 0xff)

    @Test fun `a voice turn keeps the car's media focus, but not for ever`() {
        // The player is paused for the listen, so the pump hears silence; ten seconds of it
        // gave the car's focus back mid-turn and the car left Android Auto audio (the owner,
        // 2026-11-11; the drive's log: paused 08:53:36, LOSS 08:53:46, taken again 08:53:55).
        assertTrue(turnHoldsFocus(inTurn = true, forMillis = 0))
        assertTrue(turnHoldsFocus(inTurn = true, forMillis = 30_000))
        assertFalse(turnHoldsFocus(inTurn = false, forMillis = 0))
        // A phase that never returned to idle must not hold a silent car for the whole drive.
        assertFalse(turnHoldsFocus(inTurn = true, forMillis = TURN_HOLD_MAX_MILLIS))
    }

    @Test fun `music under her voice is lowered on the phone, without a step`() {
        val ducker = MusicDucker()
        val first = level(8_000, 1_000)
        ducker.apply(first, first.size, duck = true)
        // A ramp across the first buffer, never a jump (a jump is a click in the car).
        for (i in 1 until 1_000) assertTrue(sample(first, i - 1) - sample(first, i) in 0..16)
        val held = level(8_000, 1_000)
        ducker.apply(held, held.size, duck = true)
        assertEquals((8_000 * DUCK_GAIN).toInt(), sample(held, 500))
        // And back to exactly what was captured once she has finished.
        val back = level(8_000, 1_000)
        ducker.apply(back, back.size, duck = false)
        val after = level(8_000, 1_000)
        ducker.apply(after, after.size, duck = false)
        assertEquals(8_000, sample(after, 0))
        assertEquals(8_000, sample(after, 999))
    }

    @Test fun `music nobody is talking over is not touched`() {
        val ducker = MusicDucker()
        val pcm = level(-12_345, 64)
        ducker.apply(pcm, pcm.size, duck = false)
        assertEquals(-12_345, sample(pcm, 0).toShort().toInt())
        assertEquals(-12_345, sample(pcm, 63).toShort().toInt())
    }
}
