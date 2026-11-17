package ai.jarvis.app.car.audio

import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class PcmChunkerTest {
    @Test fun `pieces gather into chunks of at least the size`() {
        val c = PcmChunker(8)
        assertNull(c.add(ByteArray(4)))
        assertEquals(8, c.add(ByteArray(4))!!.size)
        assertEquals(10, c.add(ByteArray(10))!!.size)
    }

    @Test fun `an odd byte waits for the next piece and samples stay aligned`() {
        val c = PcmChunker(4)
        val first = c.add(byteArrayOf(1, 2, 3, 4, 5))!!
        assertArrayEquals(byteArrayOf(1, 2, 3, 4), first)
        val second = c.add(byteArrayOf(6, 7, 8, 9))!!
        assertArrayEquals(byteArrayOf(5, 6, 7, 8), second)
        assertNull("a lone last byte is not a sample", c.flush())
    }

    @Test fun `the end of the line sends what is left`() {
        val c = PcmChunker(100)
        assertNull(c.add(ByteArray(6)))
        assertEquals(6, c.flush()!!.size)
        assertNull(c.flush())
    }

    // T-0917: the car runs lock-step on acknowledgements, so it is never more than a few 20 ms
    // buffers ahead; a stretch held back for a full chunk while the car plays nothing is a gap.
    @Test fun `a line starts on the head start, then what has arrived goes at once`() = runTest {
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        val c = PcmChunker(8)
        pieces.send(ByteArray(4))
        val first = async { c.next(pieces) }
        runCurrent()
        assertFalse("half a head start does not start the line", first.isCompleted)
        pieces.send(ByteArray(4))
        assertEquals(8, first.await()!!.size)
        pieces.send(ByteArray(2))
        assertEquals("the car is still playing: a short piece is not held back", 2, c.next(pieces)!!.size)
        assertEquals(0, c.dry)
    }

    @Test fun `a queue that ran dry mid-line waits for the head start again, and says so`() = runTest {
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        val c = PcmChunker(8)
        pieces.send(ByteArray(8))
        assertEquals(8, c.next(pieces)!!.size)
        val after = async { c.next(pieces) }
        runCurrent()
        pieces.send(ByteArray(2))
        runCurrent()
        assertFalse("the link fell behind: one piece does not restart the line", after.isCompleted)
        pieces.send(ByteArray(6))
        assertEquals(8, after.await()!!.size)
        assertEquals(1, c.dry)
    }

    // The owner's drive, 2026-11-11: "the audio from her speech kept being stuttery".
    @Test fun `a line that ran dry waits for twice the head start each time, up to the cap`() = runTest {
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        val c = PcmChunker(8, max = 20)
        pieces.send(ByteArray(8))
        assertEquals("a line that has not run dry starts as soon as before", 8, c.next(pieces)!!.size)
        val second = async { c.next(pieces) }
        runCurrent()
        pieces.send(ByteArray(8))
        runCurrent()
        assertFalse("the old head start is no longer enough after running dry", second.isCompleted)
        pieces.send(ByteArray(8))
        assertEquals(16, second.await()!!.size)
        assertEquals(16, c.size)
        val third = async { c.next(pieces) }
        runCurrent()
        pieces.send(ByteArray(20))
        assertEquals(20, third.await()!!.size)
        assertEquals("capped", 20, c.size)
        assertEquals(2, c.dry)
    }

    @Test fun `the line's end sends what is left of it, then nothing`() = runTest {
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        val c = PcmChunker(8)
        pieces.send(ByteArray(3))
        pieces.close()
        assertEquals("whole samples only", 2, c.next(pieces)!!.size)
        assertNull(c.next(pieces))
    }

    @Test fun `a close that lands after the last piece is the line's end, not a pause`() = runTest {
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        val c = PcmChunker(8)
        pieces.send(ByteArray(8))
        assertEquals(8, c.next(pieces)!!.size)
        val end = async { c.next(pieces) }
        runCurrent()
        pieces.close()
        assertNull(end.await())
        assertEquals(0, c.dry)
    }

    // T-1171: the end of a line that had run dry doubled the head start once more, though no audio
    // followed, so every dry line grew the next one's start twice.
    @Test fun `the line's end after a dry stretch does not grow the head start again`() = runTest {
        val pieces = Channel<ByteArray>(Channel.UNLIMITED)
        val c = PcmChunker(8, max = 64)
        pieces.send(ByteArray(8))
        assertEquals(8, c.next(pieces)!!.size)
        val dry = async { c.next(pieces) }
        runCurrent()
        pieces.send(ByteArray(16))
        assertEquals(16, dry.await()!!.size)
        pieces.close()
        assertNull(c.next(pieces))
        assertEquals(1, c.dry)
        assertEquals("grown once, by the stretch that ran dry", 16, c.size)
    }

    // T-1171: one slow moment (a phone pause) must not delay every later reply for the drive.
    private val default = CarAudioStream.STREAM_HEAD_START_SECONDS
    private val max = CarAudioStream.STREAM_HEAD_START_MAX_SECONDS

    @Test fun `a dry line's head start is what the chunker grew to`() {
        assertEquals(2.0, nextStreamHeadStart(default, 2.0, dry = 1), 1e-9)
        assertEquals(max, nextStreamHeadStart(2.0, max, dry = 2), 1e-9)
    }

    @Test fun `a line that did not run dry moves the head start halfway back to the default`() {
        assertEquals(2.5, nextStreamHeadStart(4.0, 4.0, dry = 0), 1e-9)
        assertEquals(1.75, nextStreamHeadStart(2.5, 2.5, dry = 0), 1e-9)
        assertEquals(1.375, nextStreamHeadStart(1.75, 1.75, dry = 0), 1e-9)
    }

    @Test fun `clean lines never take it below the default, and the default stays put`() {
        assertEquals(default, nextStreamHeadStart(default, default, dry = 0), 1e-9)
        assertEquals(default, nextStreamHeadStart(default - 0.5, default, dry = 0), 1e-9)
        var now = max
        repeat(40) { now = nextStreamHeadStart(now, now, dry = 0) }
        assertEquals("settles on the default", default, now, 1e-6)
    }

    @Test fun `a dry line never lands above the cap, whatever the chunker says`() {
        assertEquals(max, nextStreamHeadStart(default, max * 3, dry = 1), 1e-9)
    }
}
