package ai.jarvis.app.assist

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** A speak-pcm stream cut at odd byte counts still plays every frame (TtsPlayer.PcmLine). */
class PcmFramesTest {

    @Test
    fun `an odd piece keeps its last byte for the next one`() {
        val (first, rest) = PcmFrames.whole(null, byteArrayOf(1, 2, 3))
        assertArrayEquals(byteArrayOf(1, 2), first)
        assertEquals(3.toByte(), rest)
        val (second, none) = PcmFrames.whole(rest, byteArrayOf(4, 5, 6))
        assertArrayEquals(byteArrayOf(3, 4, 5, 6), second)
        assertNull(none)
    }

    @Test
    fun `every byte of a stream cut anywhere comes out, in order, in whole frames`() {
        val stream = ByteArray(1001) { it.toByte() }
        val cuts = listOf(0, 7, 8, 333, 334, 999, 1001)
        var carry: Byte? = null
        val out = mutableListOf<Byte>()
        for ((a, b) in cuts.zipWithNext()) {
            val (frames, rest) = PcmFrames.whole(carry, stream.copyOfRange(a, b))
            assertEquals(0, frames.size % 2)
            out += frames.toList()
            carry = rest
        }
        assertEquals(stream.toList().take(1000), out)
        assertEquals(stream[1000], carry)
    }
}
