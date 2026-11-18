package ai.aura.app.assist.hermes

import java.io.ByteArrayOutputStream
import java.util.Base64

/**
 * PCM ⇄ WAV, and the data URLs the adapter's audio relay speaks.
 *
 * The microphone produces 16 kHz mono little-endian PCM16 ([ai.aura.app.assist.MicStreamer]);
 * `POST /api/audio/transcribe` wants a base64 data URL of a container it can
 * decode, and `POST /api/audio/speak` answers with one. A WAV is that PCM
 * with a 44-byte header, so this is the whole codec. Pure, and pinned by a
 * JVM test byte for byte — a header one field wrong is silence at the other
 * end, not an error.
 */
object WavAudio {
    const val MIME = "audio/wav"
    const val SAMPLE_RATE = 16_000
    const val CHANNELS = 1
    const val BITS = 16
    const val HEADER_BYTES = 44

    data class Header(
        val sampleRate: Int,
        val channels: Int,
        val bitsPerSample: Int,
        /** Where the samples start. */
        val dataOffset: Int,
        /** How many bytes of samples follow, clamped to what is actually there. */
        val dataLength: Int,
    )

    /** [len] bytes of PCM16 from [pcm], as a canonical RIFF/WAVE file. */
    fun wrapPcm16(
        pcm: ByteArray,
        len: Int = pcm.size,
        sampleRate: Int = SAMPLE_RATE,
        channels: Int = CHANNELS,
    ): ByteArray {
        val dataLen = len.coerceIn(0, pcm.size)
        val out = ByteArrayOutputStream(HEADER_BYTES + dataLen)
        val byteRate = sampleRate * channels * BITS / 8
        val blockAlign = channels * BITS / 8
        out.write("RIFF".toByteArray(Charsets.US_ASCII))
        out.writeIntLe(36 + dataLen)
        out.write("WAVE".toByteArray(Charsets.US_ASCII))
        out.write("fmt ".toByteArray(Charsets.US_ASCII))
        out.writeIntLe(16)             // PCM fmt chunk size
        out.writeShortLe(1)            // PCM
        out.writeShortLe(channels)
        out.writeIntLe(sampleRate)
        out.writeIntLe(byteRate)
        out.writeShortLe(blockAlign)
        out.writeShortLe(BITS)
        out.write("data".toByteArray(Charsets.US_ASCII))
        out.writeIntLe(dataLen)
        out.write(pcm, 0, dataLen)
        return out.toByteArray()
    }

    /** `data:<mime>;base64,…` */
    fun dataUrl(bytes: ByteArray, mime: String = MIME): String =
        "data:$mime;base64," + Base64.getEncoder().encodeToString(bytes)

    /** The mime type and the bytes of a `data:` URL, or null for anything else. */
    fun decodeDataUrl(url: String?): Pair<String, ByteArray>? {
        if (url == null || !url.startsWith("data:")) return null
        val comma = url.indexOf(',')
        if (comma < 0) return null
        val meta = url.substring(5, comma)
        if (!meta.endsWith(";base64")) return null
        val mime = meta.removeSuffix(";base64").ifEmpty { "application/octet-stream" }
        val bytes = try {
            Base64.getDecoder().decode(url.substring(comma + 1).trim())
        } catch (e: IllegalArgumentException) {
            return null
        }
        return mime to bytes
    }

    /**
     * Read a WAV's header by walking its chunks: `fmt ` then `data`, in
     * whatever order and with whatever else in between (Piper writes a plain
     * 44-byte header; other encoders add LIST chunks). Null when it is not a
     * PCM WAV.
     */
    fun readHeader(wav: ByteArray): Header? {
        if (wav.size < 12) return null
        if (ascii(wav, 0) != "RIFF" || ascii(wav, 8) != "WAVE") return null
        var pos = 12
        var sampleRate = 0
        var channels = 0
        var bits = 0
        var format = 0
        while (pos + 8 <= wav.size) {
            val id = ascii(wav, pos)
            val size = intLe(wav, pos + 4)
            val body = pos + 8
            when (id) {
                "fmt " -> {
                    if (body + 16 > wav.size) return null
                    format = shortLe(wav, body)
                    channels = shortLe(wav, body + 2)
                    sampleRate = intLe(wav, body + 4)
                    bits = shortLe(wav, body + 14)
                }
                "data" -> {
                    if (format != 1 || channels <= 0 || sampleRate <= 0 || bits <= 0) return null
                    val available = (wav.size - body).coerceAtLeast(0)
                    val length = if (size < 0 || size > available) available else size
                    return Header(sampleRate, channels, bits, body, length)
                }
            }
            if (size < 0) return null
            // Chunks are word-aligned: an odd size carries one pad byte.
            pos = body + size + (size and 1)
        }
        return null
    }

    private fun ascii(bytes: ByteArray, at: Int): String =
        if (at + 4 > bytes.size) "" else String(bytes, at, 4, Charsets.US_ASCII)

    private fun intLe(b: ByteArray, at: Int): Int =
        (b[at].toInt() and 0xff) or
            ((b[at + 1].toInt() and 0xff) shl 8) or
            ((b[at + 2].toInt() and 0xff) shl 16) or
            ((b[at + 3].toInt() and 0xff) shl 24)

    private fun shortLe(b: ByteArray, at: Int): Int =
        (b[at].toInt() and 0xff) or ((b[at + 1].toInt() and 0xff) shl 8)

    private fun ByteArrayOutputStream.writeIntLe(v: Int) {
        write(v and 0xff); write((v ushr 8) and 0xff); write((v ushr 16) and 0xff); write((v ushr 24) and 0xff)
    }

    private fun ByteArrayOutputStream.writeShortLe(v: Int) {
        write(v and 0xff); write((v ushr 8) and 0xff)
    }
}
