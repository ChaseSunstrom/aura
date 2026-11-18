package ai.aura.app.assist

import ai.aura.app.ui.theme.JarvisThemes
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.util.Log
import java.io.File
import java.nio.FloatBuffer

/**
 * "Hey Jarvis", decided on the phone.
 *
 * The alternative — and what the app did until now — is a permanently open
 * socket carrying 16 kHz PCM to the server so that openWakeWord there can
 * decide. That works, and it costs a continuous upload of everything the
 * microphone hears, forever. Doing the same arithmetic locally means audio
 * leaves the phone only after the name has been said.
 *
 * ## The pipeline
 *
 * openWakeWord is three ONNX models in a chain, and the split is not arbitrary:
 * the first two are shared by every wake word, and only the third knows what
 * "hey Jarvis" sounds like.
 *
 * ```
 *   1280 samples (80 ms)  -> melspectrogram  -> 8 mel frames of 32 bins
 *   76 mel frames         -> embedding       -> one 96-dim vector
 *   16 embeddings         -> hey_jarvis      -> P(the name was said)
 * ```
 *
 * The melspectrogram sees the step's 1280 samples and the 480 before them, as
 * openWakeWord's own streaming does: alone, 1280 samples make 5 frames where 80 ms
 * is 8, so 3 of every 8 frames never reached the embedding model and the name
 * arrived squeezed and gapped in time. The heads were trained on unbroken frames;
 * through the real models, synthetic takes fired 135 of 252 times with the 480
 * and 114 without (T-0714, `scripts/verify/wake_verifier_live_check.py`).
 *
 * Both intermediate stages are sliding windows, which is why this class keeps
 * two ring buffers rather than re-running the chain over a fresh window each
 * time: at 80 ms per step, recomputing 76 mel frames and 16 embeddings from
 * scratch every step is roughly twenty times the work for the same answer.
 *
 * ## Everything here fails safe
 *
 * The weights are fetched at runtime from the user's own server
 * ([ModelStore]), so on a phone that has not downloaded them — or one where
 * ONNX Runtime will not load, or a model that turns out to have the wrong
 * shape — [isReady] is false and the caller keeps using the server. Nothing in
 * this file is allowed to be the reason the wake word stops working; it is an
 * optimisation over a path that already works.
 */
class OnDeviceWakeWord private constructor(
    private val environment: OrtEnvironment,
    private val mels: OrtSession,
    private val embeddings: OrtSession,
    /**
     * One head per wake word, by name ("hey_jarvis", "aura"): the last stage,
     * over the embeddings the first two share, so another name costs one small
     * model rather than a second pipeline (2026-09-25).
     */
    private val words: Map<String, OrtSession>,
) {

    /** 76 mel frames of 32 bins, oldest first. */
    private val melWindow = ArrayDeque<FloatArray>(MEL_WINDOW)

    /** 16 embedding vectors of 96, oldest first. */
    private val embeddingWindow = ArrayDeque<FloatArray>(EMBEDDING_WINDOW)

    /** Samples not yet consumed by a full 1280-sample step. */
    private val pending = SampleChunker(CHUNK_SAMPLES)
    private var closed = false

    /** The last [MEL_OVERLAP] samples of the previous step; silence after a reset. */
    private val tail = FloatArray(MEL_OVERLAP)

    /**
     * The owner's own takes of the name ([WakeVerifier], T-0714): lifts "hey jarvis"
     * frames the head half-heard. Set by the service from the stored file; the
     * personas' heads are never lifted.
     */
    @Volatile var verifier: WakeVerifier? = null

    /** How faint a "hey jarvis" the [verifier] may lift: the level's [WakeSensitivity.verifierGate]. */
    @Volatile var verifierGate: Float = WakeSensitivity.NORMAL.verifierGate

    /**
     * Each step's own "hey jarvis" score, before any lift, and the 16x96 window the
     * head read: the enrolment screen picks each take's best windows from these.
     */
    @Volatile var onWindow: ((score: Float, window: FloatArray) -> Unit)? = null

    val isReady: Boolean get() = true

    /** The wake words this detector hears. */
    val names: Set<String> get() = words.keys

    /**
     * Feed 16 kHz mono PCM16.
     *
     * @return the newest probability per wake word, or null when there is not
     *   yet enough audio for one. Nulls are normal and frequent: a score exists
     *   only every 80 ms, and only once both windows have filled — 25 steps, two
     *   seconds after capture starts (10 steps of 8 mel frames, then 15 more embeddings).
     */
    @Synchronized
    fun score(pcm: ByteArray, length: Int): Map<String, Float>? {
        if (closed) return null
        // Little-endian int16 to float in -1..1, which is what the melspectrogram
        // model was trained on, cut into 80 ms steps.
        var latest: Map<String, Float>? = null
        pending.feed(pcm, length) { chunk -> latest = step(chunk) ?: latest }
        return latest
    }

    private fun step(chunk: FloatArray): Map<String, Float>? {
        val input = tail + chunk
        System.arraycopy(chunk, CHUNK_SAMPLES - MEL_OVERLAP, tail, 0, MEL_OVERLAP)
        val melFrames = runMels(input) ?: return null
        for (frame in melFrames) {
            if (melWindow.size == MEL_WINDOW) melWindow.removeFirst()
            melWindow.addLast(frame)
        }
        if (melWindow.size < MEL_WINDOW) return null

        val embedding = runEmbedding() ?: return null
        if (embeddingWindow.size == EMBEDDING_WINDOW) embeddingWindow.removeFirst()
        embeddingWindow.addLast(embedding)
        if (embeddingWindow.size < EMBEDDING_WINDOW) return null

        val window = FloatArray(EMBEDDING_WINDOW * EMBEDDING_SIZE)
        var at = 0
        for (vector in embeddingWindow) {
            System.arraycopy(vector, 0, window, at, EMBEDDING_SIZE)
            at += EMBEDDING_SIZE
        }
        return words.mapNotNull { (name, head) ->
            val raw = runWakeWord(head, window) ?: return@mapNotNull null
            if (name != JARVIS) return@mapNotNull name to raw
            onWindow?.invoke(raw, window)
            name to (verifier?.lift(raw, window, verifierGate) ?: raw)
        }.toMap().ifEmpty { null }
    }

    private fun runMels(chunk: FloatArray): List<FloatArray>? = try {
        OnnxTensor.createTensor(
            environment,
            FloatBuffer.wrap(chunk),
            longArrayOf(1, chunk.size.toLong()),
        ).use { input ->
            mels.run(mapOf(mels.inputNames.first() to input)).use { result ->
                // (1, 1, frames, 32), and the model's own scaling: openWakeWord
                // applies `x / 10 + 2` between this stage and the next. Skipping
                // it produces a model that runs, returns numbers, and never
                // detects anything.
                @Suppress("UNCHECKED_CAST")
                val raw = result[0].value as Array<Array<Array<FloatArray>>>
                raw[0][0].map { frame -> FloatArray(frame.size) { frame[it] / 10f + 2f } }
            }
        }
    } catch (t: Throwable) {
        Log.w(TAG, "melspectrogram failed", t)
        null
    }

    private fun runEmbedding(): FloatArray? = try {
        val flat = FloatArray(MEL_WINDOW * MEL_BINS)
        var at = 0
        for (frame in melWindow) {
            System.arraycopy(frame, 0, flat, at, MEL_BINS)
            at += MEL_BINS
        }
        OnnxTensor.createTensor(
            environment,
            FloatBuffer.wrap(flat),
            longArrayOf(1, MEL_WINDOW.toLong(), MEL_BINS.toLong(), 1),
        ).use { input ->
            embeddings.run(mapOf(embeddings.inputNames.first() to input)).use { result ->
                @Suppress("UNCHECKED_CAST")
                val raw = result[0].value as Array<Array<Array<FloatArray>>>
                raw[0][0][0].copyOf()
            }
        }
    } catch (t: Throwable) {
        Log.w(TAG, "embedding failed", t)
        null
    }

    private fun runWakeWord(wakeWord: OrtSession, flat: FloatArray): Float? = try {
        OnnxTensor.createTensor(
            environment,
            FloatBuffer.wrap(flat),
            longArrayOf(1, EMBEDDING_WINDOW.toLong(), EMBEDDING_SIZE.toLong()),
        ).use { input ->
            wakeWord.run(mapOf(wakeWord.inputNames.first() to input)).use { result ->
                @Suppress("UNCHECKED_CAST")
                (result[0].value as Array<FloatArray>)[0][0]
            }
        }
    } catch (t: Throwable) {
        Log.w(TAG, "wake word model failed", t)
        null
    }

    /** Drop the windows. Called when capture stops, so stale audio cannot fire. */
    @Synchronized
    fun reset() {
        melWindow.clear()
        embeddingWindow.clear()
        pending.reset()
        tail.fill(0f)
    }

    /**
     * Waits for a score in flight on the capture thread (the service closes from the main thread,
     * and a worker can outlive MicStreamer.stop()'s join); a score after this returns null.
     */
    @Synchronized
    fun close() {
        closed = true
        words.values.forEach { runCatching { it.close() } }
        runCatching { embeddings.close() }
        runCatching { mels.close() }
    }

    companion object {
        private const val TAG = "JarvisWakeOnDevice"

        /** 80 ms at 16 kHz — one step of the pipeline. */
        const val CHUNK_SAMPLES = 1280

        /** openWakeWord's 3 x 160-sample hop of context before each step (`_streaming_melspectrogram`). */
        const val MEL_OVERLAP = 480

        const val MEL_BINS = 32
        const val MEL_WINDOW = 76
        const val EMBEDDING_SIZE = 96
        const val EMBEDDING_WINDOW = 16

        /** The three files, in the order they are chained: the two shared stages, then "hey jarvis". */
        val REQUIRED_MODELS = listOf(
            "melspectrogram.onnx",
            "embedding_model.onnx",
            "hey_jarvis_v0.1.onnx",
        )

        /** The wake word each head is, by its file. */
        const val JARVIS = "hey_jarvis"

        /**
         * The personas' own names, trained here (2026-09-25, "Train a model per
         * name"): each a head over the same two stages, heard when its file is
         * present and the person has persona wake words on. "aura" is one head for both
         * "Aura" and "Hey Aura" (T-0861, trained with near-miss negatives).
         */
        val PERSONA_MODELS: Map<String, String> = linkedMapOf(
            "aura" to "aura.onnx",
        )

        /** The persona a wake word calls: "aura" -> "Aura"; the plain name for Jarvis too. */
        fun personaOf(word: String): String? = when (word) {
            JARVIS -> JarvisThemes.JARVIS.spokenName
            in PERSONA_MODELS -> word.removePrefix("hey_").replaceFirstChar { it.uppercase() }
            else -> null
        }

        /**
         * Load the chain, or return null.
         *
         * Null is a supported, expected answer — the weights may not have been
         * downloaded, ONNX Runtime may not have a build for this ABI, a file
         * may be truncated. Every one of those means "keep using the server",
         * never "the wake word is broken".
         */
        fun open(directory: File, personas: Boolean = false): OnDeviceWakeWord? {
            val files = REQUIRED_MODELS.map { File(directory, it) }
            val present = { f: File -> f.isFile && f.length() > 0L }
            val extra = if (!personas) emptyMap() else PERSONA_MODELS.mapValues { File(directory, it.value) }.filterValues(present)
            if (files.any { !present(it) }) {
                Log.i(TAG, "on-device models are not downloaded yet")
                return null
            }
            return try {
                val env = OrtEnvironment.getEnvironment()
                val options = OrtSession.SessionOptions().apply {
                    // One thread. This runs continuously in the background on a
                    // phone; taking every core to shave milliseconds off a
                    // detection nobody is waiting for is the wrong trade.
                    setIntraOpNumThreads(1)
                    setInterOpNumThreads(1)
                }
                val heads = linkedMapOf(JARVIS to env.createSession(files[2].absolutePath, options))
                // A persona's head that will not load is left out, not the reason
                // "hey jarvis" stops working.
                for ((word, file) in extra) {
                    // Not fetched yet is not a failure: a stack trace per missing name per
                    // open was most of the 1442 log.
                    if (!file.isFile || file.length() == 0L) continue
                    // A head that will not open is deleted, so the next open fetches it again:
                    // kept, it failed every reopen for good (ModelStore counts any non-empty
                    // file as downloaded), as the house's weightless stubs did (T-0893).
                    runCatching { env.createSession(file.absolutePath, options) }
                        .onSuccess { heads[word] = it }
                        .onFailure {
                            Log.w(TAG, "could not open $word", it)
                            file.delete()
                        }
                }
                OnDeviceWakeWord(
                    env,
                    env.createSession(files[0].absolutePath, options),
                    env.createSession(files[1].absolutePath, options),
                    heads,
                )
            } catch (t: Throwable) {
                Log.w(TAG, "could not open the on-device wake word models", t)
                null
            }
        }
    }
}
