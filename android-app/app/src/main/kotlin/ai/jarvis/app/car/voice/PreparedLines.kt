package ai.jarvis.app.car.voice

import kotlinx.coroutines.Deferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Lines the car will say soon or has just said (the coming turns, a speed warning), voiced by
 * the house and held in memory, so saying one needs no link at the moment it is due. Each line
 * used to be rendered whole on the house when asked for; on a slow link the turn was said after
 * the corner (the owner's drive, 2026-11-11).
 *
 * In memory only, the newest [max]: an approval's or a message's words pass through here too,
 * and nothing said in the car is written to the phone's storage (JarvisVoiceLines keeps only
 * the fixed lines). Does not cover: a line never prepared on a dead link, which goes to
 * `phone` (the on-phone voice, or unsaid for a voice that allows no other).
 */
class PreparedLines(private val max: Int = 12) {
    private val kept = object : LinkedHashMap<String, Pair<Int, ByteArray>>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Pair<Int, ByteArray>>): Boolean = size > max
    }

    @Synchronized fun get(key: String): Pair<Int, ByteArray>? = kept[key]

    @Synchronized fun put(key: String, rateHz: Int, pcm: ByteArray) {
        kept[key] = rateHz to pcm
    }

    /**
     * Says [text] (held under [key], which names the voice too): from memory, else from
     * [render] if it answers within [deadlineMs], else by [phone]. A render that answers late
     * is still kept for the next time.
     *
     * @return where it came from: "kept", "house" or "phone".
     */
    @OptIn(ExperimentalCoroutinesApi::class)
    suspend fun say(
        key: String,
        text: String,
        play: suspend (pcm: ByteArray, rateHz: Int) -> Boolean,
        phone: suspend (text: String) -> Unit,
        deadlineMs: Long = DEADLINE_MS,
        render: () -> Deferred<Pair<Int, ByteArray>?>,
    ): String {
        get(key)?.let { (rate, pcm) -> if (play(pcm, rate)) return "kept" }
        val job = render()
        job.invokeOnCompletion { failed ->
            if (failed == null) job.getCompleted()?.let { (rate, pcm) -> put(key, rate, pcm) }
        }
        val got = withTimeoutOrNull(deadlineMs) {
            try {
                job.await()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
        }
        if (got != null && play(got.second, got.first)) return "house"
        phone(text)
        return "phone"
    }

    companion object {
        /** A turn's word is due about seven seconds before the turn (route.ts); later than this it is not worth waiting for. */
        const val DEADLINE_MS = 2_500L
    }
}
