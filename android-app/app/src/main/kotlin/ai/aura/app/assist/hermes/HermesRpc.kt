package ai.aura.app.assist.hermes

import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

/**
 * Request ids and the callbacks waiting on them, over a socket somebody else
 * owns.
 *
 * Monotonic ids, one callback per id, delivered exactly once: a reply for an
 * id nobody is waiting on is dropped, and a socket that closes fails every
 * outstanding request rather than leaving its caller waiting forever. Pure —
 * the send function is injected — so the bookkeeping is testable without
 * OkHttp.
 */
class HermesRpc(
    /** Put one frame on the wire. False when there is no wire. */
    private val transmit: (String) -> Boolean,
) {
    sealed interface Reply {
        data class Ok(val result: JSONObject) : Reply
        data class Error(val code: Int, val message: String) : Reply
    }

    private val next = AtomicInteger(1)
    private val pending = LinkedHashMap<Int, (Reply) -> Unit>()

    /** Requests sent and not yet answered. */
    val inFlight: Int get() = synchronized(pending) { pending.size }

    /** The id the next request will carry. */
    val nextId: Int get() = next.get()

    /**
     * Send [method] and hand its reply to [onReply]. Returns the id.
     *
     * When nothing can be sent the callback gets [NOT_CONNECTED] at once, so a
     * caller never waits on a request that never left.
     */
    fun request(method: String, params: JSONObject = JSONObject(), onReply: (Reply) -> Unit = {}): Int {
        val id = next.getAndIncrement()
        synchronized(pending) { pending[id] = onReply }
        val sent = try {
            transmit(HermesFrames.request(id, method, params))
        } catch (t: Throwable) {
            false
        }
        if (!sent) {
            val waiting = synchronized(pending) { pending.remove(id) }
            waiting?.invoke(Reply.Error(NOT_CONNECTED, "not connected"))
        }
        return id
    }

    /**
     * One frame from the wire. Replies are matched to their callbacks here;
     * events and unknowns are handed back for the owner to deal with.
     */
    fun receive(text: String): HermesFrames.Frame {
        val frame = HermesFrames.parse(text)
        when (frame) {
            is HermesFrames.Frame.Response -> take(frame.id)?.invoke(Reply.Ok(frame.result))
            is HermesFrames.Frame.Failure -> take(frame.id)?.invoke(Reply.Error(frame.code, frame.message))
            else -> Unit
        }
        return frame
    }

    /** The socket went away: every waiting caller hears so, once. */
    fun failAll(message: String) {
        val waiting = synchronized(pending) {
            val copy = pending.values.toList()
            pending.clear()
            copy
        }
        for (callback in waiting) callback(Reply.Error(DISCONNECTED, message))
    }

    private fun take(id: Int): ((Reply) -> Unit)? = synchronized(pending) { pending.remove(id) }

    companion object {
        /** Local codes, outside JSON-RPC's reserved range and Hermes's 4xxx. */
        const val NOT_CONNECTED = -1
        const val DISCONNECTED = -2
    }
}
