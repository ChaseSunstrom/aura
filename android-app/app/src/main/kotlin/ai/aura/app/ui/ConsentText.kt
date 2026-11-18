package ai.aura.app.ui

import org.json.JSONArray
import org.json.JSONObject

/**
 * What a consent prompt shows of its parameters: one "key: value" line each.
 *
 * The owner, 2026-10-08: "it just shows an unformatted json object". The screen's rule
 * is that nothing is summarised or cut, so this changes the layout, not the content:
 * every value is shown whole and as sent, strings without their quotes, a nested object's
 * keys dotted ("params.to"), a list joined with commas. RAW still shows the exact text,
 * which is the only view that keeps a hostile payload's duplicate keys.
 */
object ConsentText {
    fun fields(raw: String): String {
        val trimmed = raw.trim()
        val root = runCatching { if (trimmed.startsWith("{")) JSONObject(trimmed) else null }.getOrNull()
            ?: return raw
        val lines = ArrayList<String>()
        flatten("", root, lines)
        return if (lines.isEmpty()) raw else lines.joinToString("\n")
    }

    private fun flatten(prefix: String, o: JSONObject, out: MutableList<String>) {
        for (key in o.keys()) {
            val name = if (prefix.isEmpty()) key else "$prefix.$key"
            when (val v = o.opt(key)) {
                is JSONObject -> if (v.length() == 0) out.add("$name: (none)") else flatten(name, v, out)
                is JSONArray -> out.add("$name: " + (0 until v.length()).joinToString(", ") { v.opt(it).toString() })
                JSONObject.NULL, null -> out.add("$name: (none)")
                else -> out.add("$name: $v")
            }
        }
    }
}
