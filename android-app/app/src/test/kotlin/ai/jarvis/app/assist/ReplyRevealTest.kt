package ai.jarvis.app.assist

import ai.jarvis.app.ui.MarkdownText
import android.os.Looper
import android.text.Spanned
import android.view.View
import android.widget.TextView
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import java.io.File
import java.time.Duration

/**
 * A reply on the phone's assist windows reads as the console's conversation draws it (T-0728):
 * streamed to its last whole word with its open Markdown closed, only the words that just arrived
 * fading in, typing dots while a turn has no words. `tests/contracts/streaming_markdown.json`
 * binds the streaming rules to the console's `conversation.ts`. Does not cover the look of a
 * frame mid-fade (the goldens are settled) or scrolling.
 */
@RunWith(RobolectricTestRunner::class)
class ReplyRevealTest {
    private val table = JSONObject(File("../../tests/contracts/streaming_markdown.json").readText())
    private val nufi = JSONObject(File("../../tests/contracts/markdown_text.json").readText()).getJSONArray("cases").let { cases ->
        (0 until cases.length()).map { cases.getJSONObject(it).getString("md") }.first { "NUFI" in it }
    }
    private val view = TextView(RuntimeEnvironment.getApplication())
    private val who = View(RuntimeEnvironment.getApplication()).apply { visibility = View.GONE }
    private val reveal = ReplyReveal(view, who)

    private fun idle(ms: Long) = shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(ms))

    private fun fading(): List<String> {
        val s = view.text as Spanned
        return s.getSpans(0, s.length, ReplyReveal.Fade::class.java)
            .sortedBy { s.getSpanStart(it) }
            .map { s.subSequence(s.getSpanStart(it), s.getSpanEnd(it)).toString() }
    }

    @Test
    fun `the streaming rules are the console's`() {
        for (key in listOf("whole_words", "close_open")) {
            val cases = table.getJSONArray(key)
            for (i in 0 until cases.length()) {
                val c = cases.getJSONObject(i)
                val got = if (key == "whole_words") ReplyReveal.wholeWords(c.getString("in")) else ReplyReveal.closeOpen(c.getString("in"))
                assertEquals("$key ${JSONObject.quote(c.getString("in"))}", c.getString("out"), got)
            }
        }
    }

    @Test
    fun `the NUFI reply streamed by the character never shows ** or an inline bullet`() {
        for (n in 1..nufi.length) {
            reveal.show(nufi.substring(0, n))
            val shown = view.text.toString()
            assertFalse("after $n characters: $shown", "**" in shown)
            assertFalse("after $n characters: $shown", Regex("""(^|\s)- """, RegexOption.MULTILINE).containsMatchIn(shown))
        }
        idle(ReplyReveal.SETTLE_MS)
        assertEquals(MarkdownText.spanned(nufi).toString(), view.text.toString())
    }

    @Test
    fun `a half-arrived word waits until the stream pauses`() {
        reveal.show("Hello the")
        assertEquals("Hello", view.text.toString().trim())
        idle(ReplyReveal.SETTLE_MS)
        assertEquals("Hello the", view.text.toString())
    }

    @Test
    fun `only the words that just arrived fade in, and they settle`() {
        reveal.show("one two ")
        assertEquals(listOf("one", "two"), fading())
        idle(1000)
        reveal.show("one two three ")
        assertEquals(listOf("three"), fading())
        idle(1000)
        reveal.show("one two three four ")
        assertEquals(listOf("four"), fading())
    }

    @Test
    fun `a reply that does not extend the last one fades in whole`() {
        reveal.show("one two ")
        idle(1000)
        reveal.show("Didn't catch that ")
        assertEquals(listOf("Didn't", "catch", "that"), fading())
    }

    @Test
    fun `a running turn with no words types, and the label shows only over something`() {
        assertEquals(View.GONE, who.visibility)
        reveal.thinking(true)
        assertEquals("• • •", view.text.toString())
        assertEquals(View.VISIBLE, who.visibility)
        reveal.show("Hi there ")
        assertEquals("Hi there", view.text.toString().trim())
        reveal.thinking(false)
        assertEquals("Hi there", view.text.toString().trim())
        reveal.show("")
        assertEquals(View.GONE, who.visibility)
        assertEquals(View.GONE, view.visibility)
        reveal.thinking(true)
        reveal.thinking(false)
        assertTrue(view.text.isEmpty())
    }
}
