package ai.aura.app.assist

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ai.onnxruntime.TensorInfo
import ai.aura.app.config.JarvisConfig
import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.nio.LongBuffer

/**
 * Speech to text on the phone: Moonshine base, on the onnxruntime the wake word already
 * runs (the owner, 2026-10-08: "on phone transcription isnt working").
 *
 * Why this and not the platform or Vosk: GrapheneOS ships no on-device recogniser, so
 * `SpeechRecognizer.isOnDeviceRecognitionAvailable` is false there; and Vosk loads through
 * JNA, whose runtime trampolines GrapheneOS refuses -- the process died on SIGSEGV in a
 * real drive (CarVoiceStream's breadcrumb). onnxruntime is plain JNI and is already proven
 * on this phone by "Hey Jarvis".
 *
 * The weights come from the house's model mirror ([ModelStore], `jarvis-core` models.py),
 * three files exported together. Greedy decoding, one utterance at a time; the dimensions
 * are read from the decoder's own inputs, so Moonshine tiny drops in without a code change.
 * Does not stream partial words: Moonshine reads the whole utterance at once, which on a
 * spoken command is a fraction of a second.
 */
class OnPhoneSpeech private constructor(
    private val env: OrtEnvironment,
    private val encoder: OrtSession,
    private val decoder: OrtSession,
    private val pieces: Array<String?>,
    private val special: Set<Int>,
) {
    private val layers = decoder.inputNames.count { it.endsWith(".decoder.key") }
    private val cacheShape = (decoder.inputInfo.getValue("past_key_values.0.decoder.key").info as TensorInfo).shape

    /** The words in [samples] (16 kHz mono, -1..1); empty when nothing was said. */
    @Synchronized
    fun transcribe(samples: FloatArray): String {
        // Half a second of silence each side: on clips that start on the first word the
        // model invented one ("Or what's the weather", "I'll play some jazz") without it.
        val padded = FloatArray(samples.size + 2 * PAD).also { samples.copyInto(it, PAD) }
        val ids = ArrayList<Int>()
        OnnxTensor.createTensor(env, FloatBuffer.wrap(padded), longArrayOf(1, padded.size.toLong())).use { audio ->
            encoder.run(mapOf("input_values" to audio)).use { encoded ->
                decode(encoded.get(0) as OnnxTensor, maxOf(8, (padded.size / RATE * TOKENS_PER_SECOND).toInt()), ids)
            }
        }
        return textOf(ids)
    }

    private fun decode(hidden: OnnxTensor, limit: Int, ids: MutableList<Int>) {
        val empty = OnnxTensor.createTensor(env, FloatBuffer.allocate(0), longArrayOf(1, cacheShape[1], 0, cacheShape[3]))
        var first: OrtSession.Result? = null // the cross-attention cache, made once from the audio
        var last: OrtSession.Result? = null // the self-attention cache, one longer each step
        var token = START
        try {
            for (step in 0 until limit) {
                val feed = HashMap<String, OnnxTensor>()
                val owned = listOf(
                    OnnxTensor.createTensor(env, LongBuffer.wrap(longArrayOf(token.toLong())), longArrayOf(1, 1)),
                    OnnxTensor.createTensor(env, booleanArrayOf(step > 0)),
                )
                feed["input_ids"] = owned[0]
                feed["use_cache_branch"] = owned[1]
                feed["encoder_hidden_states"] = hidden
                for (l in 0 until layers) for (kv in KV) {
                    feed["past_key_values.$l.decoder.$kv"] = last?.let { it.get("present.$l.decoder.$kv").get() as OnnxTensor } ?: empty
                    feed["past_key_values.$l.encoder.$kv"] = first?.let { it.get("present.$l.encoder.$kv").get() as OnnxTensor } ?: empty
                }
                val result = try { decoder.run(feed) } finally { owned.forEach { it.close() } }
                if (last != null && last !== first) last.close()
                last = result
                if (first == null) first = result
                token = argmaxLast(result.get("logits").get() as OnnxTensor)
                if (token == END) break
                ids.add(token)
            }
        } finally {
            if (last != null && last !== first) last.close()
            first?.close()
            empty.close()
        }
    }

    /**
     * The likeliest next token among the words. The logits are wider (32768) than the
     * vocabulary (32000): the rest is padding and special marks no transcript spells.
     */
    private fun argmaxLast(tensor: OnnxTensor): Int {
        val logits = tensor.floatBuffer
        val width = tensor.info.shape.last().toInt()
        val from = logits.limit() - width
        var best = 0
        for (i in 1 until minOf(width, pieces.size)) if (logits.get(from + i) > logits.get(from + best)) best = i
        return best
    }

    /** Pieces to text: `▁` is a space, `<0xNN>` a byte of a UTF-8 character. */
    internal fun textOf(ids: List<Int>): String {
        val out = ByteArrayOutputStream()
        for (id in ids) {
            if (id in special) continue
            val piece = pieces.getOrNull(id) ?: continue
            val byte = BYTE.matchEntire(piece)
            if (byte != null) out.write(byte.groupValues[1].toInt(16)) else out.write(piece.replace('\u2581', ' ').toByteArray())
        }
        return out.toString(Charsets.UTF_8.name()).removePrefix(" ").trim()
    }

    fun close() {
        runCatching { decoder.close() }
        runCatching { encoder.close() }
    }

    companion object {
        private const val TAG = "JarvisOnPhoneSpeech"
        private const val RATE = 16_000f
        private const val PAD = 8_000
        /** Moonshine's own bound on a greedy decode: ~6 tokens a second of audio. */
        private const val TOKENS_PER_SECOND = 6.5f
        private const val START = 1
        private const val END = 2
        private val KV = arrayOf("key", "value")
        private val BYTE = Regex("<0x([0-9A-Fa-f]{2})>")

        /** The house's names for the three files (jarvis-core models.py). */
        val MODELS = listOf("moonshine-base-encoder.onnx", "moonshine-base-decoder.onnx", "moonshine-tokenizer.json")

        @Volatile private var shared: OnPhoneSpeech? = null
        @Volatile private var fetching = false

        /**
         * Whether the weights are on this phone. The first ask without them starts fetching
         * them from the house in the background, so the switch works without a trip to
         * Settings → Download models; until they land the server transcribes.
         */
        fun ready(context: Context): Boolean {
            if (ModelStore.isDownloaded(context, MODELS)) return true
            if (!fetching) {
                fetching = true
                Thread {
                    try {
                        val config = JarvisConfig(context)
                        ModelStore.download(context, config.serverUrl, config.token, MODELS)?.let { Log.i(TAG, it) }
                    } finally {
                        fetching = false
                    }
                }.start()
            }
            return false
        }

        /** The app's one model, opened on first use (seconds: call it off the main thread). */
        @Synchronized
        fun shared(context: Context): OnPhoneSpeech? =
            shared ?: (if (ready(context)) open(ModelStore.directory(context)) else null)?.also { shared = it }

        /** 16 kHz mono 16-bit PCM out of a WAV; what the console's recorder makes (`audio/pcm.ts`). */
        fun samplesOfWav(bytes: ByteArray): FloatArray {
            require(bytes.size > 44 && String(bytes, 0, 4, Charsets.US_ASCII) == "RIFF") { "not a WAV" }
            val head = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            require(head.getShort(22).toInt() == 1 && head.getInt(24) == RATE.toInt()) { "not 16 kHz mono" }
            var at = 12
            while (at + 8 <= bytes.size && String(bytes, at, 4, Charsets.US_ASCII) != "data") at += 8 + head.getInt(at + 4)
            require(at + 8 <= bytes.size) { "a WAV with no data" }
            val pcm = ByteBuffer.wrap(bytes, at + 8, minOf(head.getInt(at + 4), bytes.size - at - 8))
                .order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
            return FloatArray(pcm.remaining()) { pcm.get(it) / 32768f }
        }

        /**
         * Open the model in [directory], or null with the reason logged: not downloaded,
         * a truncated file, no onnxruntime for this ABI. Null means "the server
         * transcribes", never a broken voice. Two threads: an utterance is waited for,
         * but this phone already runs warm.
         */
        fun open(directory: File): OnPhoneSpeech? {
            val files = MODELS.map { File(directory, it) }
            if (files.any { !it.isFile || it.length() == 0L }) return null
            return try {
                val tokenizer = JSONObject(files[2].readText())
                val vocab = tokenizer.getJSONObject("model").getJSONObject("vocab")
                val pieces = arrayOfNulls<String>(vocab.length())
                for (piece in vocab.keys()) vocab.getInt(piece).takeIf { it in pieces.indices }?.let { pieces[it] = piece }
                val added = tokenizer.optJSONArray("added_tokens")
                val special = (0 until (added?.length() ?: 0)).map { added!!.getJSONObject(it) }
                    .filter { it.optBoolean("special") }.map { it.getInt("id") }.toSet()
                val env = OrtEnvironment.getEnvironment()
                val options = OrtSession.SessionOptions().apply {
                    setIntraOpNumThreads(2)
                    setInterOpNumThreads(1)
                }
                OnPhoneSpeech(
                    env,
                    env.createSession(files[0].absolutePath, options),
                    env.createSession(files[1].absolutePath, options),
                    pieces,
                    special,
                )
            } catch (t: Throwable) {
                Log.w(TAG, "could not open the on-phone speech model", t)
                null
            }
        }
    }
}
