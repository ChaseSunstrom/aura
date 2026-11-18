package ai.aura.app.ui

import android.graphics.Typeface
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.StrikethroughSpan
import android.text.style.StyleSpan
import android.text.style.TypefaceSpan
import android.text.style.URLSpan

/**
 * A reply's Markdown as spans on a TextView, read as the console's `renderMarkdown` draws it
 * (T-0727): blocks on their own lines, bullets as "• ", numbered items keep their numbers, table
 * cells joined by " · ", headings bold, fences and inline code monospace, http(s) links tappable.
 * `tests/contracts/markdown_text.json` binds the two; MarkdownTextTest and the console's
 * markdown.contract.test.ts both read it.
 *
 * Nothing is parsed as HTML: raw tags stay text, as on the console. A link to anything but
 * http(s) is its label alone. Does not cover nested lists' indentation, images or footnotes.
 */
object MarkdownText {
    private const val SPAN = Spanned.SPAN_EXCLUSIVE_EXCLUSIVE

    private val FENCE = Regex("""^\s*```""")
    private val HEADING = Regex("""^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$""")
    private val BULLET = Regex("""^\s*[-*+]\s+(.*)$""")
    private val NUMBER = Regex("""^\s*(\d+)[.)]\s+(.*)$""")
    private val QUOTE = Regex("""^\s*>\s?(.*)$""")
    private val TABLE_RULE = Regex("""^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$""")
    private val SAFE_HREF = Regex("""^https?://[^\s<>"']+$""", RegexOption.IGNORE_CASE)
    private val INLINE = Regex(
        """`([^`]+)`|\[([^\]]+)]\(([^)\s]+)\)|\*\*(.+?)\*\*|__(.+?)__|~~(.+?)~~|\*([^*\s][^*]*?)\*|(?<!\w)_([^_\s][^_]*?)_(?!\w)""",
    )

    fun spanned(md: String): CharSequence {
        val out = SpannableStringBuilder()
        val lines = md.replace("\r\n", "\n").split("\n")
        var i = 0
        while (i < lines.size) {
            val line = lines[i]
            when {
                FENCE.containsMatchIn(line) -> {
                    val body = mutableListOf<String>()
                    i++
                    while (i < lines.size && !FENCE.containsMatchIn(lines[i])) body += lines[i++]
                    block(out)
                    val start = out.length
                    out.append(body.joinToString("\n"))
                    out.setSpan(TypefaceSpan("monospace"), start, out.length, SPAN)
                }
                line.isBlank() -> Unit
                HEADING.matches(line) -> {
                    block(out)
                    val start = out.length
                    inline(out, HEADING.find(line)!!.groupValues[1])
                    out.setSpan(StyleSpan(Typeface.BOLD), start, out.length, SPAN)
                }
                line.trimStart().startsWith("|") -> {
                    if (!TABLE_RULE.matches(line)) {
                        block(out)
                        val cells = line.trim().removePrefix("|").removeSuffix("|").split("|").map { it.trim() }
                        cells.forEachIndexed { k, cell ->
                            if (k > 0) out.append(" · ")
                            inline(out, cell)
                        }
                    }
                }
                BULLET.matches(line) -> {
                    block(out)
                    out.append("• ")
                    inline(out, BULLET.find(line)!!.groupValues[1])
                }
                NUMBER.matches(line) -> {
                    val m = NUMBER.find(line)!!
                    block(out)
                    out.append("${m.groupValues[1]}. ")
                    inline(out, m.groupValues[2])
                }
                QUOTE.matches(line) -> {
                    block(out)
                    inline(out, QUOTE.find(line)!!.groupValues[1])
                }
                else -> {
                    // A paragraph: its lines join with a space, or break where one ends in two spaces.
                    block(out)
                    inline(out, line.trim())
                    while (i + 1 < lines.size && isProse(lines[i + 1])) {
                        out.append(if (lines[i].endsWith("  ")) "\n" else " ")
                        inline(out, lines[++i].trim())
                    }
                }
            }
            i++
        }
        return out
    }

    private fun isProse(line: String) =
        line.isNotBlank() && !FENCE.containsMatchIn(line) && !HEADING.matches(line) && !line.trimStart().startsWith("|") &&
            !BULLET.matches(line) && !NUMBER.matches(line) && !QUOTE.matches(line)

    /** A new block starts on its own line. */
    private fun block(out: SpannableStringBuilder) {
        if (out.isNotEmpty() && out[out.length - 1] != '\n') out.append('\n')
    }

    private fun inline(out: SpannableStringBuilder, text: String) {
        var at = 0
        for (m in INLINE.findAll(text)) {
            out.append(text, at, m.range.first)
            at = m.range.last + 1
            val g = m.groupValues
            val start = out.length
            when {
                g[1].isNotEmpty() -> {
                    out.append(g[1])
                    out.setSpan(TypefaceSpan("monospace"), start, out.length, SPAN)
                }
                g[2].isNotEmpty() -> {
                    inline(out, g[2])
                    if (SAFE_HREF.matches(g[3])) out.setSpan(URLSpan(g[3]), start, out.length, SPAN)
                }
                g[4].isNotEmpty() || g[5].isNotEmpty() -> {
                    inline(out, g[4].ifEmpty { g[5] })
                    out.setSpan(StyleSpan(Typeface.BOLD), start, out.length, SPAN)
                }
                g[6].isNotEmpty() -> {
                    inline(out, g[6])
                    out.setSpan(StrikethroughSpan(), start, out.length, SPAN)
                }
                else -> {
                    inline(out, g[7].ifEmpty { g[8] })
                    out.setSpan(StyleSpan(Typeface.ITALIC), start, out.length, SPAN)
                }
            }
        }
        out.append(text, at, text.length)
    }
}
