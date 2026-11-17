package ai.jarvis.app.car.voice

import ai.jarvis.app.assist.hermes.HermesClient
import ai.jarvis.app.assist.hermes.WavAudio
import dev.headway.voice.SpeechRecognizer
import java.io.ByteArrayOutputStream

/**
 * The car's microphone, heard by Jarvis's own transcriber (ADR-0047, in place of
 * Headway's on-phone Vosk model). The car's PCM is buffered for the utterance
 * CarVoiceStream's endpointer bounds, then sent as one WAV to
 * `POST /api/audio/transcribe` -- the same call, and so the same speaker gate and
 * speaker ticket, as the phone's own microphone.
 *
 * Blocking in [finish]: CarVoiceStream calls it from the listening coroutine on the
 * IO side, never from the car's main thread. Does not work without a link to
 * Jarvis; [offline] says when the network failing is why the transcript is empty, and
 * [failure] when the call went out and failed some other way (T-1079).
 */
class JarvisTranscriber(
    override val sampleRateHz: Int,
    private val transcribe: (wav: ByteArray) -> HermesClient.Transcribed,
) : SpeechRecognizer {

    private val pcm = ByteArrayOutputStream()

    /** What the last utterance was heard as, with its speaker ticket; null before one, or offline. */
    @Volatile
    var lastHeard: HermesClient.Heard? = null
        private set

    /** The last utterance could not reach Jarvis: the network failed, nothing else. */
    @Volatile
    var offline: Boolean = false
        private set

    /** Why the last utterance got no words though the network held (an HTTP error, an unreadable body); null otherwise. */
    @Volatile
    var failure: String? = null
        private set

    @Synchronized
    override fun accept(pcm: ByteArray, length: Int): String? {
        this.pcm.write(pcm, 0, length.coerceIn(0, pcm.size))
        return null
    }

    /**
     * Which utterance is being recorded; [reset] starts the next. An answer for an earlier
     * one is dropped: a listen given up on (CarVoiceStream's cap) leaves its call running,
     * and its late answer carried the old utterance's speaker ticket onto the new words.
     */
    private var utterance = 0

    override fun finish(): String {
        val mine: Int
        val audio = synchronized(this) { mine = utterance; pcm.toByteArray().also { pcm.reset() } }
        if (audio.isEmpty()) return ""
        val heard = transcribe(WavAudio.wrapPcm16(audio, sampleRate = sampleRateHz, channels = 1))
        synchronized(this) {
            if (mine != utterance) return ""
            lastHeard = (heard as? HermesClient.Transcribed.Got)?.heard
            offline = heard == HermesClient.Transcribed.Offline
            failure = (heard as? HermesClient.Transcribed.Failed)?.why
        }
        return lastHeardWords(heard)
    }

    private fun lastHeardWords(heard: HermesClient.Transcribed): String =
        (heard as? HermesClient.Transcribed.Got)?.heard?.transcript?.trim()?.lowercase().orEmpty()

    @Synchronized
    override fun reset() {
        utterance++
        pcm.reset()
    }

    /** Nothing held open: the transcriber is an HTTP call per utterance. */
    override fun close() {
        reset()
    }
}
