package ai.aura.app.config

import ai.aura.app.ui.theme.ActiveTheme
import org.json.JSONObject

/**
 * The adapter's pairing QR, parsed: `{"url": …, "key": …, "kind": "hermes-adapter"}`.
 *
 * `GET /api/pair` on the jarvis-adapter answers exactly this object, and the
 * console draws it as a QR (`docs/hermes-hud-protocol.md`). It is the third
 * thing a scanned square can be, after [PairingPayload]'s `jarvis://pair`
 * URL and a bare token, and it is tried only once [PairingPayload] has said
 * "not addressed to me" — a refused `jarvis://` payload must never fall
 * through to here.
 *
 * Unlike [PairingPayload] there is no claim step: the QR carries the key
 * itself, because the adapter's key IS the long-lived credential and the
 * adapter has no short-lived-code endpoint. That is the adapter's choice, and
 * the phone's job is only to store it where the token already lives
 * (`JarvisConfig.token`). The URL goes through [ServerUrl.check] — the same
 * validator the typed field uses — so a printed address cannot be a way around
 * the cleartext rule.
 *
 * Pure: no Android, mirrored by `android-app/tools/hermes_server_kind_test.py`.
 */
data class HermesPairing(
    /** The adapter's base URL, normalised. */
    val url: String,
    /** The bearer key. Never logged. */
    val key: String,
    /** The model the adapter fronts, when it said. Display only. */
    val model: String,
) {
    sealed interface Result {
        data class Ok(val pairing: HermesPairing) : Result
        /** Not a JSON object with the adapter's `kind` — the caller may try other formats. */
        data class NotAPayload(val reason: String) : Result
        /** Recognisably the adapter's payload, and refused. Show [message]. */
        data class Refused(val message: String) : Result
    }

    companion object {
        /** Same cap as [PairingPayload.MAX_LENGTH]: nothing legitimate is longer. */
        const val MAX_LENGTH = 512

        /** A key shorter than the adapter would ever mint is not one. */
        const val MIN_KEY = 16
        const val MAX_KEY = 256

        fun parse(raw: String?): Result {
            val text = raw?.trim().orEmpty()
            if (text.isEmpty()) return Result.NotAPayload("empty")
            if (!text.startsWith("{")) return Result.NotAPayload("not a JSON object")
            if (text.length > MAX_LENGTH) {
                return Result.Refused("That QR code is too long to be a ${ActiveTheme.name} pairing code.")
            }
            if (text.any { it.isISOControl() && it != '\n' && it != '\r' && it != '\t' }) {
                return Result.Refused("That QR code contains characters a pairing code cannot.")
            }
            val json = try {
                JSONObject(text)
            } catch (e: Exception) {
                return Result.NotAPayload("not readable JSON")
            }
            if (json.optString("kind") != ServerEndpoint.HERMES_KIND) {
                return Result.NotAPayload("not an adapter pairing payload")
            }

            val rawUrl = json.optString("url")
            if (rawUrl.isEmpty()) return Result.Refused("That pairing code has no server address in it.")
            if (rawUrl.any { it.isISOControl() }) {
                return Result.Refused("That QR code contains characters a pairing code cannot.")
            }
            val check = ServerUrl.check(rawUrl)
            if (!check.isValid) {
                return Result.Refused(check.error ?: "That pairing code's server address is not usable.")
            }

            val key = json.optString("key")
            if (key.length !in MIN_KEY..MAX_KEY || key.any { it.isWhitespace() || it.isISOControl() }) {
                return Result.Refused("That pairing code's key is malformed.")
            }

            return Result.Ok(HermesPairing(url = check.normalized, key = key, model = json.optString("model")))
        }
    }
}
