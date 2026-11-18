package ai.aura.app.ui

import android.graphics.Typeface
import android.text.Spanned
import android.text.style.StyleSpan
import android.text.style.TypefaceSpan
import android.text.style.URLSpan
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.io.File

/**
 * A reply's Markdown on the phone reads as it does on the console (T-0727).
 *
 * `tests/contracts/markdown_text.json` holds each case's visible text and its bold, italic,
 * code and link runs; `aura-web/src/lib/markdown.contract.test.ts` holds the console's
 * renderer to the same table. Does not cover the look (sizes, colours): each view's own.
 */
@RunWith(RobolectricTestRunner::class)
class MarkdownTextTest {
    private val cases = JSONObject(File("../../tests/contracts/markdown_text.json").readText()).getJSONArray("cases")

    private fun strings(a: JSONArray) = (0 until a.length()).map { a.getString(it) }

    private fun runs(s: Spanned, keep: (Any) -> Boolean): List<String> =
        s.getSpans(0, s.length, Any::class.java)
            .filter(keep)
            .sortedBy { s.getSpanStart(it) }
            .map { s.subSequence(s.getSpanStart(it), s.getSpanEnd(it)).toString() }

    @Test
    fun `every case reads as the console draws it`() {
        assertTrue("a table with the owner's reply", (0 until cases.length()).any { cases.getJSONObject(it).getString("md").contains("NUFI") })
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val name = c.getString("name")
            val out = MarkdownText.spanned(c.getString("md")) as Spanned
            assertEquals(name, c.getString("text"), out.toString())
            assertEquals("$name: bold", strings(c.getJSONArray("bold")), runs(out) { it is StyleSpan && it.style == Typeface.BOLD })
            assertEquals("$name: italic", strings(c.getJSONArray("italic")), runs(out) { it is StyleSpan && it.style == Typeface.ITALIC })
            assertEquals("$name: code", strings(c.getJSONArray("code")), runs(out) { it is TypefaceSpan })
            val links = c.getJSONArray("links")
            val urls = out.getSpans(0, out.length, URLSpan::class.java).sortedBy { out.getSpanStart(it) }
            assertEquals("$name: links", links.length(), urls.size)
            urls.forEachIndexed { k, u ->
                val want = links.getJSONObject(k)
                assertEquals(want.getString("href"), u.url)
                assertEquals(want.getString("text"), out.subSequence(out.getSpanStart(u), out.getSpanEnd(u)).toString())
            }
        }
    }
}
