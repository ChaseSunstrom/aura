package ai.jarvis.app.assist.hermes

import org.json.JSONArray
import org.json.JSONObject

/**
 * The HUD socket's wire format: JSON-RPC 2.0, one newline-free JSON text
 * frame per message (`docs/hermes-hud-protocol.md`).
 *
 * Client → server: `{"jsonrpc":"2.0","id":<int>,"method":"<name>","params":{…}}`.
 * Server → client: a response `{"id","result"}`, an error `{"id","error":{code,message}}`,
 * or an event `{"method":"event","params":{"type","session_id","seq","payload"}}`.
 *
 * Pure — no Android, no socket — so the framing every other Hermes class
 * relies on is pinned by a JVM test rather than by a phone.
 */
object HermesFrames {
    const val VERSION = "2.0"
    const val EVENT = "event"

    sealed interface Frame {
        /** A result for a request this client sent. */
        data class Response(val id: Int, val result: JSONObject) : Frame

        /** An error for a request this client sent. */
        data class Failure(val id: Int, val code: Int, val message: String) : Frame

        /** Something the server said unprompted. */
        data class Event(val type: String, val sessionId: String, val seq: Int, val payload: JSONObject) : Frame

        /** Not a frame this client understands. Logged, never acted on. */
        data class Unknown(val reason: String) : Frame
    }

    /** One request frame. [params] is sent as given — `{}` when there are none. */
    fun request(id: Int, method: String, params: JSONObject = JSONObject()): String =
        JSONObject()
            .put("jsonrpc", VERSION)
            .put("id", id)
            .put("method", method)
            .put("params", params)
            .toString()

    fun parse(text: String): Frame {
        val json = try {
            JSONObject(text)
        } catch (e: Exception) {
            return Frame.Unknown("not a JSON object")
        }
        if (json.optString("method") == EVENT) {
            val params = json.optJSONObject("params") ?: return Frame.Unknown("event without params")
            val type = params.optString("type")
            if (type.isEmpty()) return Frame.Unknown("event without a type")
            return Frame.Event(
                type = type,
                sessionId = params.optString("session_id"),
                seq = params.optInt("seq", 0),
                payload = params.optJSONObject("payload") ?: JSONObject(),
            )
        }
        val id = json.optInt("id", -1)
        val error = json.optJSONObject("error")
        if (error != null) {
            return Frame.Failure(id, error.optInt("code", 0), error.optString("message"))
        }
        if (json.has("result")) {
            val result = json.opt("result")
            // A result that is not an object (`session.list` answers with an
            // array) is wrapped so callers always read a JSONObject.
            val body = when (result) {
                is JSONObject -> result
                is JSONArray -> JSONObject().put(VALUE, result)
                null, JSONObject.NULL -> JSONObject()
                else -> JSONObject().put(VALUE, result)
            }
            return Frame.Response(id, body)
        }
        return Frame.Unknown("neither result, error nor event")
    }

    /** Key a non-object result is wrapped under. */
    const val VALUE = "value"
}
