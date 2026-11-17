package ai.jarvis.app.assist

import ai.jarvis.app.config.ServerUrl
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.MediaDataSource
import android.media.MediaPlayer
import android.net.Uri
import android.util.Log

/**
 * Plays a TTS URL served by jarvis-core. The server's `tts_output.url` needs
 * the bearer token, so it goes in a request header via
 * MediaPlayer.setDataSource(context, uri, headers). Fully self-contained; no
 * extra playback deps.
 *
 * The URL comes from the server, and the token is the key to the whole house.
 * So the origin is checked HERE too, not only in [AssistPipelineClient]: a
 * second caller that forgets must not be able to post the bearer token to a
 * host the user never configured. Off-origin URLs are refused outright rather
 * than played without the header — Jarvis has no business fetching media from
 * somewhere else, and a silent fetch would still leak the device's IP.
 */
class TtsPlayer(
    private val context: Context,
    private val token: String,
    /** The configured jarvis-core base URL. Playback is pinned to its origin. */
    private val serverUrl: String,
) {

    private var player: MediaPlayer? = null

    /**
     * True while capture is pinned to a worn headset, i.e. while the echo
     * canceller is doing the work that keeps Jarvis from hearing itself.
     *
     * Set by the conversation controller from
     * [ai.jarvis.app.audio.AudioRoute.hasEchoLoop]. It changes only the
     * `AudioAttributes` usage, because AEC is defined against the
     * *communication* stream: playing the reply as `USAGE_ASSISTANT` while
     * capturing as `VOICE_COMMUNICATION` gives the canceller no reference
     * signal for the audio it is supposed to be subtracting, and the echo comes
     * straight back. The two settings are one decision and have to agree.
     */
    @Volatile
    var communicationRoute: Boolean = false

    fun play(url: String, onDone: () -> Unit) {
        stop()
        if (ServerUrl.resolveOnServer(serverUrl, url) != url) {
            Log.w(TAG, "refusing to play a TTS URL that is not on the configured server")
            onDone()
            return
        }
        val mp = MediaPlayer()
        player = mp
        mp.setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(
                    if (communicationRoute) {
                        AudioAttributes.USAGE_VOICE_COMMUNICATION
                    } else {
                        AudioAttributes.USAGE_ASSISTANT
                    }
                )
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()
        )
        try {
            mp.setDataSource(
                context,
                Uri.parse(url),
                mapOf("Authorization" to "Bearer $token")
            )
        } catch (e: Exception) {
            Log.e(TAG, "setDataSource failed", e)
            release(mp); onDone(); return
        }
        mp.setOnPreparedListener { it.start() }
        mp.setOnCompletionListener {
            release(mp)
            onDone()
        }
        mp.setOnErrorListener { _, what, extra ->
            Log.e(TAG, "playback error $what/$extra")
            release(mp)
            onDone()
            true
        }
        try {
            mp.prepareAsync()
        } catch (e: Exception) {
            Log.e(TAG, "prepareAsync failed", e)
            release(mp); onDone()
        }
    }

    /**
     * Play a reply held in memory — the adapter's `POST /api/audio/speak`
     * answer, decoded from its data URL — through a [MediaPlayer] reading a
     * [MediaDataSource], with the same audio attributes as the URL path so the
     * earpiece route and the echo canceller see one kind of stream whichever
     * server spoke.
     *
     * Codec-agnostic on purpose. `docs/hermes-hud-protocol.md` says the relay
     * answers with a WAV; the live adapter (2026-09-02) answers with
     * `data:audio/mpeg`. The platform decoder takes either, and nothing
     * touches disk: a temporary file for every reply would be the one place
     * this app wrote speech to storage. [onDone] is called on the main
     * thread, once, whether the clip finished, failed or was stopped.
     */
    fun playBytes(bytes: ByteArray, onDone: () -> Unit) {
        stop()
        if (bytes.isEmpty()) {
            onDone()
            return
        }
        val mp = MediaPlayer()
        player = mp
        mp.setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(
                    if (communicationRoute) AudioAttributes.USAGE_VOICE_COMMUNICATION
                    else AudioAttributes.USAGE_ASSISTANT
                )
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()
        )
        try {
            mp.setDataSource(BytesDataSource(bytes))
        } catch (e: Exception) {
            Log.e(TAG, "setDataSource(bytes) failed", e)
            release(mp); onDone(); return
        }
        mp.setOnPreparedListener { it.start() }
        mp.setOnCompletionListener {
            release(mp)
            onDone()
        }
        mp.setOnErrorListener { _, what, extra ->
            Log.e(TAG, "playback error $what/$extra")
            release(mp)
            onDone()
            true
        }
        try {
            mp.prepareAsync()
        } catch (e: Exception) {
            Log.e(TAG, "prepareAsync failed", e)
            release(mp); onDone()
        }
    }

    /**
     * A streamed line: 16-bit mono PCM written to an `AudioTrack` as the pieces
     * land, so the first word plays while the rest is still being made. One
     * track per line; `stop()` ends it mid-line for a barge-in. `onDone` fires
     * when the last piece has been written and played out, or when the line is
     * cut. The caller feeds it from the network thread; the track's own buffer
     * (a second) rides over a late piece as a gap, not a glitch.
     */
    inner class PcmLine(rate: Int) {
        private val track: AudioTrack
        @Volatile private var open = true
        private var written = 0L

        init {
            stop()
            val minBuf = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
            track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(if (communicationRoute) AudioAttributes.USAGE_VOICE_COMMUNICATION else AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(rate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build()
                )
                .setBufferSizeInBytes(maxOf(minBuf, rate * 2))
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
            line = this
            track.play()
        }

        /** A byte left over from the last piece: half a 16-bit frame. */
        private var carry: Byte? = null

        /** One piece, blocking until the track has room: back-pressure on the network read is the intent. */
        fun write(piece: ByteArray) {
            if (!open) return
            val (frames, rest) = PcmFrames.whole(carry, piece)
            carry = rest
            var at = 0
            while (at < frames.size && open) {
                val n = track.write(frames, at, frames.size - at)
                if (n < 0) {
                    Log.w(TAG, "AudioTrack.write refused a piece ($n); the rest of the line is dropped")
                    open = false
                    break
                }
                at += n
            }
            written += frames.size
        }

        /** The last piece is in: wait for it to play out, then release. */
        fun finish(onDone: () -> Unit) {
            if (open) {
                val frames = (written / 2).toInt()
                try {
                    // Poll the head position: `stop()` from another thread ends the wait.
                    var waited = 0
                    while (open && track.playState == AudioTrack.PLAYSTATE_PLAYING && track.playbackHeadPosition < frames && waited < 60_000) {
                        Thread.sleep(20); waited += 20
                    }
                } catch (_: Exception) {}
            }
            close()
            onDone()
        }

        fun close() {
            if (!open) return
            open = false
            try { track.pause(); track.flush() } catch (_: Exception) {}
            try { track.release() } catch (_: Exception) {}
            if (line === this) line = null
        }
    }

    private var line: PcmLine? = null

    /** A byte array as the platform decoder's input. Nothing is copied or written. */
    private class BytesDataSource(private val bytes: ByteArray) : MediaDataSource() {
        override fun readAt(position: Long, buffer: ByteArray, offset: Int, size: Int): Int {
            if (position >= bytes.size) return -1
            val n = minOf(size.toLong(), bytes.size - position).toInt()
            System.arraycopy(bytes, position.toInt(), buffer, offset, n)
            return n
        }

        override fun getSize(): Long = bytes.size.toLong()
        override fun close() = Unit
    }

    val isPlaying: Boolean
        get() = try { player?.isPlaying == true || line != null } catch (_: Exception) { false }

    fun stop() {
        player?.let { release(it) }
        player = null
        line?.close()
        line = null
    }

    private fun release(mp: MediaPlayer) {
        try { if (mp.isPlaying) mp.stop() } catch (_: Exception) {}
        try { mp.reset() } catch (_: Exception) {}
        try { mp.release() } catch (_: Exception) {}
        if (player === mp) player = null
    }

    companion object {
        private const val TAG = "JarvisTts"
    }
}

/**
 * Whole 16-bit frames out of a byte stream cut wherever the network cut it.
 *
 * `AudioTrack.write` refuses a byte count that is not a whole number of frames
 * (ERROR_BAD_VALUE), and the player took that as the end of the line -- so the
 * first odd-sized read from `speak-pcm` silenced the rest of every CosyVoice
 * reply (the owner, 2026-09-25: "how come the cosy voice audio isn't playing on
 * my phone?"). A trailing half frame is carried into the next piece instead.
 */
internal object PcmFrames {
    fun whole(carry: Byte?, piece: ByteArray): Pair<ByteArray, Byte?> {
        val data = if (carry != null) byteArrayOf(carry) + piece else piece
        val even = data.size and 1.inv()
        return data.copyOf(even) to (if (even < data.size) data[data.size - 1] else null)
    }
}
