package ai.jarvis.app.car.voice

import ai.jarvis.app.assist.OfflineVoice
import dev.headway.voice.MediaAction
import java.io.File
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The car's fixed lines ("Jarvis is offline", "pause", "Sorry, I did not catch that") in
 * Jarvis's own voice, rendered by the server at drive start and kept on the phone, so an
 * outage does not hand them to the phone's speech engine, whose default English voice is
 * a woman's when no male voice is installed (brief §2.3, AUDIT B2: the owner heard "a
 * female voice" whenever the server dropped).
 *
 * A line with no clip still goes to [engine], but never silently: the first one of a
 * drive is preceded by [NOTICE], in Jarvis's voice when that is cached. Only the lines
 * [remember] is given are written, and the car gives it only fixed ones, so no reply,
 * approval or message text reaches the phone's storage. AURA's free-form line offline is her
 * own Kokoro voice on the phone once it keeps up in real time (T-0872). Does not cover JARVIS's:
 * the bundled Piper voice is his nearest on-device relative (GAPS G8, BundledVoice).
 */
class JarvisVoiceLines(
    private val dir: File,
    private val play: suspend (pcm: ByteArray, rateHz: Int) -> Boolean,
    private val engine: suspend (text: String) -> Unit,
    /** Speaks [text] in the bundled voice; false when there is none or it failed. */
    private val bundled: suspend (text: String) -> Boolean = { false },
    /** Whether the bundled voice is on the phone; [bundled] still decides if it rendered. */
    private val bundledReady: () -> Boolean = { true },
    /**
     * The persona voice the server speaks in now. Part of each clip's key, so a switch makes
     * the old voice's clips miss (T-0807) rather than play for up to a day.
     */
    private val voice: () -> String = { "" },
    /** The worn pack's `voice.offline.fallback`: [OfflineVoice.CLIPS_ONLY] lets no other voice speak. */
    private val fallback: () -> String = { OfflineVoice.BUNDLED_VOICE },
    /** Whether a clips-only pack's own voice is on the phone and measured in real time there. */
    private val ownRealtime: () -> Boolean = { false },
    /** Speaks [text] in that voice; false when it could not render or play. */
    private val own: suspend (text: String) -> Boolean = { false },
    /**
     * Whether the notice was said. The service hands every link of one drive the same flag:
     * the head unit re-forms the link several times a drive, and a flag made here came back
     * armed with each one (2026-11-12).
     */
    private val announced: AtomicBoolean = AtomicBoolean(false),
    /**
     * Whether the house answered a moment ago. Then a line that reaches here was only late,
     * and "I can't reach the server" would be false: it is said plainly, the notice kept.
     */
    private val houseUp: () -> Boolean = { false },
) {
    // Atomic, not just volatile: CarTurn's says and the link's sayOnCar run on different
    // threads, and a check-then-set let both play the notice over each other.
    // [announced] is set once and never re-armed here: it was re-armed by every line the
    // house voiced, and on a link that came and went the owner heard it "throughout the
    // entire drive" (2026-11-11).

    /**
     * Keeps [pcm] (16-bit mono at [rateHz]) as Jarvis's voice for [text], replacing any older
     * clip. False when it could not be written (a full phone): the drive goes on without it.
     */
    fun remember(text: String, rateHz: Int, pcm: ByteArray): Boolean = runCatching {
        dir.mkdirs()
        val tmp = File(dir, "${key(text)}.tmp")
        tmp.writeBytes(ByteBuffer.allocate(4).putInt(rateHz).array() + pcm)
        tmp.renameTo(File(dir, key(text)))
    }.getOrDefault(false)

    /** No clip for [text], or one rendered more than [maxAgeMs] ago: worth rendering again. */
    fun stale(text: String, maxAgeMs: Long, nowMs: Long = System.currentTimeMillis()): Boolean =
        File(dir, key(text)).let { !it.isFile || nowMs - it.lastModified() > maxAgeMs }

    /** The rate and PCM kept for [text]; null when there is none or its rate header is corrupt. */
    fun clip(text: String): Pair<Int, ByteArray>? {
        val bytes = runCatching { File(dir, key(text)).readBytes() }.getOrNull() ?: return null
        if (bytes.size <= 4) return null
        val rate = ByteBuffer.wrap(bytes, 0, 4).int
        return if (rate > 0) rate to bytes.copyOfRange(4, bytes.size) else null
    }

    /**
     * [text] in the persona's voice when a clip has it. Otherwise the pack decides (OfflineVoice):
     * the bundled voice, then the phone engine announced once a drive; or, clips only, her
     * own voice on the phone when it keeps up (the same voice, so no notice), else the line
     * stays unsaid and [WORKING_OFFLINE] says why once, in her voice or not at all.
     */
    suspend fun say(text: String) {
        if (playClip(text)) return
        // Past the clip (none, or it would not play). The bundled voice is Jarvis's own and needs
        // no notice; false means it is absent or could not render.
        when (val s = OfflineVoice.choose(hasClip = false, bundledReady(), fallback(), ownRealtime())) {
            OfflineVoice.Speaker.ON_DEVICE, OfflineVoice.Speaker.NOTICE_ONLY -> {
                if (s == OfflineVoice.Speaker.ON_DEVICE && own(text)) return
                if (!houseUp() && !announced.getAndSet(true)) playClip(WORKING_OFFLINE)
                return
            }
            OfflineVoice.Speaker.BUNDLED -> if (bundled(text)) return
            else -> Unit
        }
        if (!houseUp() && !announced.getAndSet(true) && !playClip(NOTICE)) engine(NOTICE)
        engine(text)
    }

    private suspend fun playClip(text: String): Boolean =
        clip(text)?.let { (rate, pcm) -> play(pcm, rate) } ?: false

    private fun key(text: String): String {
        val line = text.trim().lowercase().replace(Regex("\\s+"), " ")
        // No voice keeps the text-only key the clips on a phone were written under.
        val named = voice().let { if (it.isEmpty()) line else "$it\n$line" }
        return MessageDigest.getInstance("SHA-256").digest(named.toByteArray()).joinToString("") { "%02x".format(it) }
    }

    companion object {
        const val NOTICE = "I can't reach the server, so my voice will sound different until it's back."

        /** A clips-only voice's notice (AURA §3: "say plainly in that voice that she's working offline"). */
        const val WORKING_OFFLINE = "I'm working offline, so I can only say a few things until I reach the server again."
        const val UNHEARD = "Sorry, I did not catch that"
        const val YES_OR_NO = "Yes or no?"

        /** What the car says back for a media command: terse, so "pause" is just "pause". */
        fun said(action: MediaAction): String = action.name.lowercase().replace('_', ' ')

        /** Every line the car says with no reply behind it, so the only ones worth keeping. */
        val FIXED: List<String> get() =
            listOf(NOTICE, WORKING_OFFLINE, CarTurn.OFFLINE, UNHEARD, YES_OR_NO, HermesCarJarvis.CONFIRM_ON_PHONE) +
                ai.jarvis.app.car.service.CarConnectCheck.LINES + MediaAction.entries.map(::said)
    }
}
