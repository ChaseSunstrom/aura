package ai.jarvis.app.assist

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The on-device wake word's sample buffer (T-0154). The owner's phone measured the capture
 * thread at 58% of a core while listening: the old buffer was an ArrayList<Float> drained
 * with removeAt(0) 1280 times a step, quadratic and boxed, 12.5 steps a second.
 */
class SampleChunkerTest {

    private fun pcm(samples: Int, seed: Int = 7): ByteArray {
        val out = ByteArray(samples * 2)
        var x = seed
        for (i in 0 until samples) {
            x = x * 1103515245 + 12345
            val s = (x ushr 16).toShort().toInt()
            out[2 * i] = (s and 0xFF).toByte()
            out[2 * i + 1] = (s shr 8).toByte()
        }
        return out
    }

    /** What the old buffer produced: every sample as -1..1, cut into whole chunks, in order. */
    private fun reference(bytes: ByteArray, size: Int): List<FloatArray> {
        val all = FloatArray(bytes.size / 2) {
            ((bytes[2 * it + 1].toInt() shl 8) or (bytes[2 * it].toInt() and 0xFF)).toShort() / 32768f
        }
        return (0 until all.size / size).map { all.copyOfRange(it * size, (it + 1) * size) }
    }

    @Test
    fun `uneven pieces come out as the same samples at the same boundaries`() {
        val bytes = pcm(16_000 * 3)
        val chunker = SampleChunker(1280)
        val got = ArrayList<FloatArray>()
        var at = 0
        val pieces = intArrayOf(2048, 100, 4096, 2, 2560, 1000, 3000)
        var n = 0
        while (at < bytes.size) {
            val len = minOf(pieces[n++ % pieces.size], bytes.size - at)
            chunker.feed(bytes.copyOfRange(at, at + len), len) { got.add(it) }
            at += len
        }
        val want = reference(bytes, 1280)
        assertEquals(want.size, got.size)
        want.indices.forEach { assertArrayEquals("chunk $it", want[it], got[it], 0f) }
    }

    @Test
    fun `a buffer's odd last byte is dropped, as before`() {
        val chunker = SampleChunker(2)
        val got = ArrayList<FloatArray>()
        chunker.feed(byteArrayOf(0, 64, 0, 64, 9), 5) { got.add(it) }
        assertEquals(1, got.size)
        assertArrayEquals(floatArrayOf(0.5f, 0.5f), got[0], 0f)
    }

    @Test
    fun `reset drops what was held`() {
        val chunker = SampleChunker(4)
        val got = ArrayList<FloatArray>()
        chunker.feed(pcm(3), 6) { got.add(it) }
        chunker.reset()
        chunker.feed(pcm(3), 6) { got.add(it) }
        assertTrue(got.isEmpty())
    }

    @Test
    fun `a minute of audio is cut in linear time`() {
        val piece = pcm(1024)
        val chunker = SampleChunker(1280)
        var chunks = 0
        val began = System.nanoTime()
        repeat(60 * 16_000 / 1024) { chunker.feed(piece, piece.size) { chunks++ } }
        val ms = (System.nanoTime() - began) / 1_000_000
        assertEquals(60 * 16_000 / 1024 * 1024 / 1280, chunks)
        assertTrue("took $ms ms for a minute of audio", ms < 1000)
    }
}
