package ai.jarvis.app.assist.hermes

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WavAudioTest {

    private fun le32(b: ByteArray, at: Int) =
        (b[at].toInt() and 0xff) or ((b[at + 1].toInt() and 0xff) shl 8) or
            ((b[at + 2].toInt() and 0xff) shl 16) or ((b[at + 3].toInt() and 0xff) shl 24)

    private fun le16(b: ByteArray, at: Int) = (b[at].toInt() and 0xff) or ((b[at + 1].toInt() and 0xff) shl 8)

    @Test
    fun `the header is the canonical 44 bytes for 16 kHz mono PCM16`() {
        val pcm = ByteArray(3200) { (it % 7).toByte() }   // 100 ms
        val wav = WavAudio.wrapPcm16(pcm)
        assertEquals(44 + 3200, wav.size)
        assertEquals("RIFF", String(wav, 0, 4, Charsets.US_ASCII))
        assertEquals(36 + 3200, le32(wav, 4))
        assertEquals("WAVE", String(wav, 8, 4, Charsets.US_ASCII))
        assertEquals("fmt ", String(wav, 12, 4, Charsets.US_ASCII))
        assertEquals(16, le32(wav, 16))
        assertEquals(1, le16(wav, 20))          // PCM
        assertEquals(1, le16(wav, 22))          // mono
        assertEquals(16_000, le32(wav, 24))     // sample rate
        assertEquals(32_000, le32(wav, 28))     // byte rate
        assertEquals(2, le16(wav, 32))          // block align
        assertEquals(16, le16(wav, 34))         // bits
        assertEquals("data", String(wav, 36, 4, Charsets.US_ASCII))
        assertEquals(3200, le32(wav, 40))
        assertArrayEquals(pcm, wav.copyOfRange(44, wav.size))
    }

    @Test
    fun `only len bytes of the buffer are wrapped`() {
        val pcm = ByteArray(10) { 9 }
        val wav = WavAudio.wrapPcm16(pcm, len = 4)
        assertEquals(48, wav.size)
        assertEquals(4, le32(wav, 40))
    }

    @Test
    fun `wrap then read agrees with itself`() {
        val wav = WavAudio.wrapPcm16(ByteArray(640), sampleRate = 22_050)
        val header = WavAudio.readHeader(wav)!!
        assertEquals(22_050, header.sampleRate)
        assertEquals(1, header.channels)
        assertEquals(16, header.bitsPerSample)
        assertEquals(44, header.dataOffset)
        assertEquals(640, header.dataLength)
    }

    @Test
    fun `the data URL is the mime the doc names and plain base64`() {
        val url = WavAudio.dataUrl(byteArrayOf(1, 2, 3))
        assertEquals("data:audio/wav;base64,AQID", url)
        val (mime, bytes) = WavAudio.decodeDataUrl(url)!!
        assertEquals("audio/wav", mime)
        assertArrayEquals(byteArrayOf(1, 2, 3), bytes)
    }

    @Test
    fun `a speak answer round-trips to a playable header`() {
        val wav = WavAudio.wrapPcm16(ByteArray(2000), sampleRate = 22_050)
        val body = """{"ok":true,"audio":"${WavAudio.dataUrl(wav)}","provider":"local"}"""
        val bytes = HermesClient.audioOf(body)!!
        assertArrayEquals(wav, bytes)
        assertEquals(2000, WavAudio.readHeader(bytes)!!.dataLength)
    }

    @Test
    fun `the live adapter's spelling of the speak answer is read too`() {
        // Captured 2026-09-02: `data_url`, and MP3 rather than the doc's WAV.
        val body = """{"ok":true,"data_url":"data:audio/mpeg;base64,SUQzBAAAAAAAIg==","mime_type":"audio/mpeg","provider":"local"}"""
        val bytes = HermesClient.audioOf(body)!!
        assertEquals(0x49, bytes[0].toInt())   // "ID3"
        assertEquals(0x44, bytes[1].toInt())
        assertEquals(0x33, bytes[2].toInt())
        assertNull(WavAudio.readHeader(bytes))  // not a WAV, and the player does not need it to be
    }

    @Test
    fun `a header with a LIST chunk before the data still reads`() {
        val wav = WavAudio.wrapPcm16(ByteArray(8))
        // Splice a 6-byte LIST chunk (odd size 5 + pad) between fmt and data.
        val list = "LIST".toByteArray() + byteArrayOf(5, 0, 0, 0) + byteArrayOf(1, 2, 3, 4, 5, 0)
        val spliced = wav.copyOfRange(0, 36) + list + wav.copyOfRange(36, wav.size)
        val header = WavAudio.readHeader(spliced)!!
        assertEquals(36 + list.size + 8, header.dataOffset)
        assertEquals(8, header.dataLength)
    }

    @Test
    fun `a data length longer than the file is clamped`() {
        val wav = WavAudio.wrapPcm16(ByteArray(8))
        wav[40] = 0xFF.toByte(); wav[41] = 0xFF.toByte(); wav[42] = 0; wav[43] = 0
        assertEquals(8, WavAudio.readHeader(wav)!!.dataLength)
    }

    @Test
    fun `not a wav`() {
        assertNull(WavAudio.readHeader(ByteArray(3)))
        assertNull(WavAudio.readHeader("RIFF....WAVX".toByteArray() + ByteArray(40)))
        assertNull(WavAudio.decodeDataUrl("https://example.test/a.wav"))
        assertNull(WavAudio.decodeDataUrl("data:audio/wav,notbase64"))
        assertNull(WavAudio.decodeDataUrl("data:audio/wav;base64,@@@"))
    }

    @Test
    fun `the transcript reader treats silence as empty and failure as null`() {
        assertEquals("hello there", HermesClient.transcriptOf("""{"ok":true,"transcript":" hello there ","provider":"local"}"""))
        assertEquals("", HermesClient.transcriptOf("""{"ok":true,"transcript":"","provider":"local"}"""))
        assertEquals("", HermesClient.transcriptOf("""{"ok":true,"transcript":null}"""))
        assertNull(HermesClient.transcriptOf("""{"ok":false,"error":"no audio"}"""))
        assertNull(HermesClient.transcriptOf("<html>"))
        assertTrue(HermesClient.audioOf("""{"ok":false}""") == null)
    }
}
