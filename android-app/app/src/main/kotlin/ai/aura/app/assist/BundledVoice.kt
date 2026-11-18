package ai.aura.app.assist

import android.content.Context
import android.util.Log
import android.os.SystemClock
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.OfflineTtsConfig
import com.k2fsa.sherpa.onnx.OfflineTtsKokoroModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsVitsModelConfig
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream
import java.io.File

/**
 * The phone's offline voice: Piper `en_GB-alan-medium` through sherpa-onnx, the voice the
 * server's CosyVoice persona is cloned from (docs/runbooks/voice.md, "Bundled voice"). It
 * speaks what no cached clip covers while the server is out, so the phone and the car do
 * not hand a reply to the system engine's different voice.
 *
 * The weights are not in the APK. Like every model here ([ModelStore]) they come once from
 * the house's mirror, digest-checked, and are unpacked under `models/offline-voice/`. A phone
 * that never fetched them has no voice: [open] returns null and the caller keeps the
 * announced system engine. Does not stream: a line is rendered whole (about real time).
 *
 * [Model.KOKORO] is AURA's own voice on the phone (T-0872): Kokoro-82M's `af_heart`, int8,
 * through the same runtime. It is heavier, so the fetch renders one line and keeps whether it
 * kept up ([realtime]); a phone too slow for it leaves AURA clips-only (OfflineVoice).
 */
class BundledVoice private constructor(private val tts: OfflineTts, private val speaker: Int) {
    /** One voice: the mirror's name for its archive ([ModelStore] checks its digest), its files, its speaker. */
    enum class Model(val archive: String, val folder: String, val onnx: String, val needs: List<String>, val speaker: Int) {
        PIPER("vits-piper-en_GB-alan-medium.tar.bz2", "offline-voice", "en_GB-alan-medium.onnx", listOf(TOKENS), 0),
        // af_heart is speaker 3 of Kokoro v1.0's voices.bin (sherpa-onnx's
        // scripts/kokoro/v1.0/generate_voices_bin.py); she is American, so the US lexicon.
        KOKORO(
            "kokoro-int8-multi-lang-v1_0.tar.bz2", "offline-voice-kokoro", "model.int8.onnx",
            listOf(TOKENS, VOICES, LEXICON_US), 3,
        ),
    }

    /** 16-bit mono PCM and its rate for [text], or null when it could not be rendered. */
    @Synchronized
    fun render(text: String): Pair<Int, ByteArray>? = try {
        val audio = tts.generate(text, speaker, 1.0f)
        val pcm = ByteArray(audio.samples.size * 2)
        for ((i, s) in audio.samples.withIndex()) {
            val v = (s.coerceIn(-1f, 1f) * 32767f).toInt()
            pcm[2 * i] = v.toByte()
            pcm[2 * i + 1] = (v shr 8).toByte()
        }
        if (pcm.isEmpty()) null else audio.sampleRate to pcm
    } catch (t: Throwable) {
        Log.w(TAG, "the bundled voice could not render a line", t)
        null
    }

    companion object {
        private const val TAG = "JarvisBundledVoice"

        private const val TOKENS = "tokens.txt"
        private const val VOICES = "voices.bin"
        private const val LEXICON_US = "lexicon-us-en.txt"
        private const val DATA = "espeak-ng-data"
        /** Written last, holding the model's size: no marker, or a size that differs, is not ready. */
        private const val READY = "ready"
        /** The fetch's measured render time over the audio's length (below 1 keeps up). */
        private const val PACE = "pace"
        /** What the fetch renders to measure the pace: a sentence the size of a car's line. */
        private const val PACE_LINE = "In two hundred metres, turn left onto the high street, then keep right."

        private val shared = mutableMapOf<Model, BundledVoice>()
        private val tried = mutableSetOf<Model>()

        private fun directory(context: Context, model: Model) = File(ModelStore.directory(context), model.folder)

        /** Whether the voice is on this phone, complete (cheap: no model load). */
        fun ready(context: Context, model: Model = Model.PIPER): Boolean {
            val dir = directory(context, model)
            val want = runCatching { File(dir, READY).readText().trim().toLong() }.getOrNull() ?: return false
            return File(dir, model.onnx).length() == want && model.needs.all { File(dir, it).isFile } &&
                File(dir, DATA).isDirectory
        }

        /**
         * Whether [model] is here and its fetch measured it rendering faster than it speaks on
         * this phone (cheap: one small file). Does not cover a phone that got slower since (a
         * hot or saving phone): a line it renders late is still said, late.
         */
        fun realtime(context: Context, model: Model): Boolean =
            ready(context, model) &&
                (runCatching { File(directory(context, model), PACE).readText().trim().toDouble() }.getOrNull() ?: 2.0) < 1.0

        /**
         * Fetch the voice once while online and unpack it. Blocking; call from a worker thread.
         *
         * @return null when the voice is ready, else a sentence for the Settings screen. A
         *   download that fails its digest is never saved ([ModelStore]); an archive that will
         *   not unpack is thrown away so the next call fetches it again, never loading it.
         */
        fun fetch(context: Context, serverUrl: String, token: String, model: Model = Model.PIPER): String? {
            if (ready(context, model)) return null
            ModelStore.download(context, serverUrl, token, listOf(model.archive))?.let { return it }
            val archive = File(ModelStore.directory(context), model.archive)
            return try {
                unpack(archive, directory(context, model), model)
                if (model == Model.KOKORO) pace(context, model)
                null
            } catch (t: Throwable) {
                Log.w(TAG, "the offline voice would not unpack", t)
                directory(context, model).deleteRecursively()
                "The offline voice would not unpack. It will be fetched again."
            } finally {
                archive.delete()
            }
        }

        /**
         * Render [PACE_LINE] once, after a warm-up line (the first inference is the slowest), and
         * keep how long it took over how long it speaks. The model is let go after: 114 MB is not
         * held in memory for an outage that may never come; the first offline line loads it again.
         */
        private fun pace(context: Context, model: Model) {
            val voice = open(context, model) ?: return
            val ratio = try {
                voice.render("Hello.")
                val start = SystemClock.elapsedRealtime()
                voice.render(PACE_LINE)?.let { (rate, pcm) ->
                    (SystemClock.elapsedRealtime() - start) / 1000.0 / (pcm.size / 2.0 / rate)
                }
            } finally {
                close(model)
            } ?: return
            Log.i(TAG, "${model.name} renders at %.2fx real time".format(ratio))
            // Written after the release: until it exists nothing speaks in this voice ([realtime]).
            File(directory(context, model), PACE).writeText(ratio.toString())
        }

        @Synchronized
        private fun close(model: Model) {
            // Under the voice's own lock: a line being rendered finishes before the model goes.
            shared.remove(model)?.let { synchronized(it) { it.tts.release() } }
            tried -= model
        }

        /** Drop a voice that cannot be trusted, so the next [fetch] starts clean. */
        private fun discard(context: Context, model: Model) {
            directory(context, model).deleteRecursively()
            File(ModelStore.directory(context), model.archive).delete()
        }

        private fun unpack(archive: File, dest: File, model: Model) {
            val part = File(dest.parentFile, "${model.folder}.part")
            part.deleteRecursively()
            part.mkdirs()
            val root = part.canonicalPath + File.separator
            TarArchiveInputStream(BZip2CompressorInputStream(archive.inputStream().buffered())).use { tar ->
                while (true) {
                    val entry = tar.nextEntry ?: break
                    // The archive's one top folder is dropped; only regular files are written.
                    val rel = entry.name.substringAfter('/', "")
                    if (rel.isEmpty() || !entry.isFile) continue
                    val out = File(part, rel)
                    require(out.canonicalPath.startsWith(root)) { "entry leaves the folder: ${entry.name}" }
                    out.parentFile?.mkdirs()
                    out.outputStream().use { tar.copyTo(it) }
                }
            }
            val onnx = File(part, model.onnx)
            check(onnx.length() > 0 && model.needs.all { File(part, it).isFile } && File(part, DATA).isDirectory) { "incomplete" }
            File(part, READY).writeText(onnx.length().toString())
            dest.deleteRecursively()
            check(part.renameTo(dest)) { "could not move the voice into place" }
        }

        /** The app's [model] voice, loaded on first use (seconds: call it off the main thread); null if absent. */
        @Synchronized
        fun open(context: Context, model: Model = Model.PIPER): BundledVoice? {
            shared[model]?.let { return it }
            if (model in tried) return null
            val dir = directory(context, model)
            if (!ready(context, model)) {
                // Present but not whole: corrupt, so never loaded, and fetched again by the next [fetch].
                if (dir.exists()) discard(context, model)
                return null
            }
            tried += model
            return try {
                val files = when (model) {
                    Model.PIPER -> OfflineTtsModelConfig(
                        vits = OfflineTtsVitsModelConfig(
                            model = File(dir, model.onnx).path,
                            tokens = File(dir, TOKENS).path,
                            dataDir = File(dir, DATA).path,
                        ),
                        numThreads = 2,
                    )
                    Model.KOKORO -> OfflineTtsModelConfig(
                        kokoro = OfflineTtsKokoroModelConfig(
                            model = File(dir, model.onnx).path,
                            voices = File(dir, VOICES).path,
                            tokens = File(dir, TOKENS).path,
                            dataDir = File(dir, DATA).path,
                            lexicon = File(dir, LEXICON_US).path,
                        ),
                        numThreads = 2,
                    )
                }
                BundledVoice(OfflineTts(config = OfflineTtsConfig(model = files)), model.speaker).also { shared[model] = it }
            } catch (t: Throwable) {
                // An ABI the AAR lacks, or a model that will not load: discard it so it is fetched
                // again, and keep the announced system engine meanwhile.
                Log.w(TAG, "could not load the bundled voice", t)
                discard(context, model)
                null
            }
        }
    }
}
