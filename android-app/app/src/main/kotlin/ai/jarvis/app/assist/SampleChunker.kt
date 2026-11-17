package ai.jarvis.app.assist

/**
 * 16 kHz mono PCM16 in, fixed-size float chunks (-1..1) out, in order: the on-device wake
 * word's input buffer.
 *
 * It replaced an ArrayList<Float> drained with removeAt(0) once per sample of every chunk:
 * each removal shifted the whole list, every sample was boxed, and the owner's phone measured
 * the capture thread at 58% of a core while it listened (T-0154, 2026-10-09). Here the samples
 * live in one FloatArray and the leftover is moved once per feed.
 *
 * A buffer's odd last byte is dropped, as before. Each chunk is a fresh array the caller may
 * keep. Not thread-safe: one capture thread feeds it.
 */
class SampleChunker(private val size: Int) {
    private var held = FloatArray(size * 2)
    private var count = 0

    /** Add [length] bytes of [pcm]; [onChunk] gets every whole chunk now available. */
    fun feed(pcm: ByteArray, length: Int, onChunk: (FloatArray) -> Unit) {
        val samples = length / 2
        if (count + samples > held.size) held = held.copyOf(maxOf(held.size * 2, count + samples))
        var i = 0
        while (i + 1 < length) {
            held[count++] = ((pcm[i + 1].toInt() shl 8) or (pcm[i].toInt() and 0xFF)).toShort() / 32768f
            i += 2
        }
        var start = 0
        while (count - start >= size) {
            onChunk(held.copyOfRange(start, start + size))
            start += size
        }
        if (start > 0) {
            System.arraycopy(held, start, held, 0, count - start)
            count -= start
        }
    }

    /** Drop what is held, so stale audio cannot complete a chunk. */
    fun reset() {
        count = 0
    }
}
