package ai.aura.app.config

import org.json.JSONArray
import org.json.JSONObject

/**
 * The console's settings, for the phone's own settings screen (the owner,
 * 2026-09-25: "it should only be the phone with the phone settings, with all of
 * the console settings"). The phone used to show the console itself in a
 * WebView; now it draws these natively from the same two sources the console
 * reads:
 *
 *  * `tests/contracts/hermes-settings.json`, generated from `aura-schema`,
 *    bundled as an asset: what each setting is -- its group, widget, words,
 *    choices and bounds;
 *  * `jsettings @<sid> list` over the Hermes link: what each one is set to now.
 *
 * Only the leaves the contract marks `settable` are offered: the rest are fixed
 * outside the container (the host's `.env`), and the plugin refuses them.
 * Pure, so the parsing and the value encoding are pinned on the JVM.
 */
object HermesSettings {

    /** One setting as the contract describes it. */
    data class Spec(
        val key: String,
        val group: String,
        val widget: String,
        val description: String,
        val tier: String,
        val choices: List<String>,
        val min: Double?,
        val max: Double?,
    )

    /** A group, in the contract's order, with its sentence. */
    data class Group(val id: String, val title: String, val description: String)

    /** What `jsettings set` answered. */
    sealed interface Written {
        /** Written; nothing more to do. */
        data object Done : Written

        /** The approval matrix asks about this key: [id] is the proposal the person's tap applies. */
        data class Proposed(val id: String) : Written

        /** Refused, in the plugin's words. */
        data class Refused(val message: String) : Written
    }

    /** The settable leaves and the groups, from the bundled contract's text. */
    fun contract(text: String): Pair<List<Group>, List<Spec>> {
        val json = JSONObject(text)
        val groups = json.optJSONArray("groups").objects().map {
            Group(it.optString("id"), it.optString("title"), it.optString("description"))
        }
        val specs = json.optJSONArray("settings").objects()
            .filter { it.optBoolean("settable") && it.optString("widget") != "secret" }
            .map {
                Spec(
                    key = it.optString("key"),
                    group = it.optString("group"),
                    widget = it.optString("widget"),
                    description = it.optString("description"),
                    tier = it.optString("tier", "advanced"),
                    choices = it.optJSONArray("choices").strings(),
                    min = if (it.has("min")) it.optDouble("min") else null,
                    max = if (it.has("max")) it.optDouble("max") else null,
                )
            }
        return groups to specs
    }

    /** `jsettings list`'s output: each key's current value as the plugin shows it. */
    fun values(output: String?): Map<String, Any?> {
        val rows = runCatching { JSONArray(output.orEmpty()) }.getOrNull() ?: return emptyMap()
        return rows.objects().associate { it.optString("key") to it.opt("value").takeUnless { v -> v == JSONObject.NULL } }
    }

    /**
     * The JSON a value is written as, or null when [input] is not one this
     * setting takes: a number out of its bounds, a choice it does not offer.
     * A list is typed comma-separated.
     */
    fun encode(spec: Spec, input: String): String? = when (spec.widget) {
        "toggle" -> when (input) {
            "true", "false" -> input
            else -> null
        }
        "number" -> input.trim().toDoubleOrNull()
            ?.takeIf { v -> (spec.min == null || v >= spec.min) && (spec.max == null || v <= spec.max) }
            ?.let { v -> if (v % 1.0 == 0.0) v.toLong().toString() else v.toString() }
        "select" -> input.takeIf { it in spec.choices }?.let { JSONObject.quote(it) }
        "multi" -> JSONArray(input.split(',').map { it.trim() }.filter { it.isNotEmpty() }).toString()
        else -> JSONObject.quote(input.trim())
    }

    /** What a value reads as on screen: a list comma-separated, a toggle as on or off. */
    fun shown(value: Any?): String = when (value) {
        null -> ""
        is JSONArray -> value.strings().joinToString(", ")
        is Boolean -> if (value) "On" else "Off"
        else -> value.toString()
    }

    /** `jsettings set`'s output, interpreted. */
    fun written(output: String?): Written {
        val json = runCatching { JSONObject(output.orEmpty()) }.getOrNull()
            ?: return Written.Refused(output?.takeIf { it.isNotBlank() } ?: "The setting was not written.")
        json.optString("error").takeIf { it.isNotEmpty() }?.let { return Written.Refused(it) }
        if (json.optString("status") == "proposed") {
            val id = json.optJSONObject("proposal")?.optString("id").orEmpty()
            return if (id.isNotEmpty()) Written.Proposed(id) else Written.Refused("The proposal had no id.")
        }
        return Written.Done
    }

    private fun JSONArray?.objects(): List<JSONObject> =
        if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

    private fun JSONArray?.strings(): List<String> =
        if (this == null) emptyList() else (0 until length()).map { optString(it) }
}
