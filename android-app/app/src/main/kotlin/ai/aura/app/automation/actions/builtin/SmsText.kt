package ai.aura.app.automation.actions.builtin

/**
 * A text as the carrier wants it, and split when Android will not split it.
 *
 * On 2026-10-08 three texts were refused with "getGroupIdLevel1" while plain ones went
 * through seconds apart. Each held an em dash, which puts a text in UCS-2, and was longer
 * than 70 characters; for exactly that case `SmsManager.divideMessage` asks
 * `TelephonyManager.getGroupIdLevel1()` whether the network supports EMS, and that needs a
 * phone-state permission Jarvis does not hold. The owner saw it as "texts fail when the
 * phone is locked": the long texts were the ones written while he was away from it.
 *
 * [plain] maps the typography a model writes to its GSM-7 twin, so most texts stay in the
 * 160-character alphabet (cheaper, and never on that path); [ucs2Parts] is the split for a
 * text that still needs UCS-2 (an emoji, a non-Latin name). Does not cover a carrier that
 * rejects concatenated UCS-2 outright.
 */
object SmsText {
    private val TYPOGRAPHY = mapOf(
        '—' to "-", '–' to "-", '‒' to "-", '−' to "-",
        '‘' to "'", '’' to "'", '‚' to "'", '′' to "'",
        '“' to "\"", '”' to "\"", '„' to "\"", '″' to "\"",
        '…' to "...", ' ' to " ", ' ' to " ", ' ' to " ", '•' to "-",
    )

    fun plain(body: String): String = buildString(body.length) {
        for (c in body) append(TYPOGRAPHY[c] ?: c)
    }

    /** Characters per part of a concatenated UCS-2 text (134 bytes after the 6-byte header). */
    const val UCS2_PART = 67

    /** [body] in parts of at most [UCS2_PART] UTF-16 units, never between a surrogate pair. */
    fun ucs2Parts(body: String): ArrayList<String> {
        val out = ArrayList<String>()
        var start = 0
        while (start < body.length) {
            var end = minOf(start + UCS2_PART, body.length)
            if (end < body.length && Character.isHighSurrogate(body[end - 1])) end--
            out.add(body.substring(start, end))
            start = end
        }
        return out
    }
}
