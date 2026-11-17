package ai.jarvis.app.automation.notify

/**
 * The one-time code an SMS or notification carries, or null (T-0135).
 *
 * The rule and its cases are `tests/contracts/otp_extract.json`; `OtpExtractorTest` runs them
 * here and `android-app/tools/otp_extract_test.py` runs the same cases on the mirror. Only the
 * code ever leaves the phone, never the text it came from. Not covered: whether the sender is
 * who it claims to be. A spoofed text yields a code like any other, so the popup names its
 * source and apps reach it only through the owner's allow-list.
 */
object OtpExtractor {
    val KEYWORDS = listOf("code", "otp", "passcode", "verification", "verify", "one-time", "2fa", "pin", "login", "sign-in")

    private val keyword = Regex(
        "(?<![\\w-])(?:" + KEYWORDS.joinToString("|") { Regex.escape(it) } + ")(?![\\w-])",
        RegexOption.IGNORE_CASE,
    )

    // Two groups of three, or 4-8 digits with an optional letter prefix ("G-123456").
    private val candidate = Regex(
        "(?<![\\w-])(?:[A-Za-z]-)?(\\d{3})[- ](\\d{3})(?!\\w)|(?<!\\w)(?:[A-Za-z]-)?(\\d{4,8})(?!\\w)",
    )

    fun extract(text: String): String? {
        val kw = keyword.find(text) ?: return null
        var best: Pair<Int, String>? = null
        for (m in candidate.findAll(text)) {
            val start = m.range.first
            val end = m.range.last + 1
            val before = if (start > 0) text[start - 1] else null
            if (before != null && before in "$€£#+/") continue
            if (before == '-' && start >= 2 && text[start - 2].isDigit()) continue
            val a0 = text.getOrNull(end)
            val a1 = text.getOrNull(end + 1)
            if (a0 == '/') continue
            if (a0 != null && a0 in ".,-:" && a1 != null && a1.isDigit()) continue
            val code = if (m.groupValues[1].isNotEmpty()) m.groupValues[1] + m.groupValues[2] else m.groupValues[3]
            val dist = if (end <= kw.range.first) kw.range.first - end else maxOf(start - (kw.range.last + 1), 0)
            if (best == null || dist < best.first) best = dist to code
        }
        return best?.second
    }
}
