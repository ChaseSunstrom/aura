package ai.aura.app.assist

import ai.aura.app.ui.JarvisUi
import ai.aura.app.ui.MarkdownText
import ai.aura.app.ui.theme.JarvisTokens
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.TextPaint
import android.text.style.CharacterStyle
import android.text.style.UpdateAppearance
import android.view.Choreographer
import android.view.View
import android.widget.TextView
import kotlin.math.PI
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * A reply on the phone's assist windows as the console's Conversation draws it (T-0728): while it
 * streams it is drawn to its last whole word with its open Markdown closed, and only the words
 * that just arrived fade and rise in, a step behind each other, so nothing already read moves.
 * While a turn runs with no words yet, three dots type. [who] (the JARVIS label) shows only while
 * there is something under it, and so does the reply's own view. The owner, 2026-11-08: "make it smoother, animated".
 *
 * The windows get the whole reply on every delta and no "finished" signal (HermesConversation
 * sends notices, errors and the streamed text through the same onResponse), so the held-back last
 * word is drawn once nothing new has arrived for [SETTLE_MS]. A stream that stalls mid-word longer
 * than that shows the half word, its marks still closed. `tests/contracts/streaming_markdown.json`
 * binds [wholeWords] and [closeOpen] to the console's `conversation.ts`. Under reduced motion
 * words appear without moving and the dots hold still. Does not cover scrolling (each window's).
 */
internal class ReplyReveal(
    private val view: TextView,
    private val who: View? = null,
    private val clock: () -> Long = SystemClock::uptimeMillis,
    /** After every draw, the settled last word's included: the windows follow the newest line. */
    private val drawn: () -> Unit = {},
) {
    private var text = ""
    /** Per word of the reply, when its fade began on [clock] (the console's splitWords). */
    private val born = mutableListOf<Long>()
    private var fades: List<Fade> = emptyList()
    private var dots: List<Pulse> = emptyList()
    // The main looper and the frame clock, not the view's post: a view's queue waits for its
    // window, and the overlay's words are set while it is being put up.
    private val main = Handler(Looper.getMainLooper())
    private val settle = Runnable { draw(whole = true) }
    private val frame = Choreographer.FrameCallback { ticking = false; tick() }
    /** A frame is already asked for: each delta redraws, and one loop runs the fades. */
    private var ticking = false
    /** `--jv-space-1`, how far a word rises as it fades in. */
    private val rise = JarvisUi.dp(view.context, JarvisTokens.Space.TIGHT).toFloat()
    private val still by lazy { JarvisUi.reducedMotion(view.context) }

    /** The reply so far; a text that does not extend the last one is a new reply. */
    fun show(reply: String) {
        dots = emptyList()
        if (!reply.startsWith(text)) born.clear()
        text = reply
        main.removeCallbacks(settle)
        draw(whole = false)
        if (reply.isNotEmpty()) main.postDelayed(settle, SETTLE_MS)
    }

    /** A turn is running: three dots while there is no reply yet; off clears them. */
    fun thinking(on: Boolean) {
        if (on == dots.isNotEmpty() || (on && text.isNotEmpty())) return
        if (!on) {
            dots = emptyList()
            draw(whole = true)
            return
        }
        val out = SpannableStringBuilder(DOTS)
        dots = (0 until 3).map { i -> Pulse(i).also { out.setSpan(it, i * 2, i * 2 + 1, SPAN) } }
        view.text = out
        shown(true)
        tick()
    }

    private fun draw(whole: Boolean) {
        val md = closeOpen(if (whole) text else wholeWords(text))
        val out = SpannableStringBuilder(MarkdownText.spanned(md))
        val now = clock()
        val first = born.size
        val running = mutableListOf<Fade>()
        for ((i, word) in WORD.findAll(out).withIndex()) {
            if (i >= born.size) born.add(now + JarvisTokens.Motion.Stagger.PROJECT * (i - first))
            if (still || now - born[i] >= LIFE) continue
            running += Fade(born[i], rise).also { out.setSpan(it, word.range.first, word.range.last + 1, SPAN) }
        }
        fades = running
        view.text = out
        shown(out.isNotEmpty())
        tick()
        drawn()
    }

    /** The reply and its label take room only while there is something in them. */
    private fun shown(on: Boolean) {
        val v = if (on) View.VISIBLE else View.GONE
        view.visibility = v
        who?.visibility = v
    }

    private fun tick() {
        val now = clock()
        fades.forEach { it.at(now) }
        if (!still) dots.forEach { it.at(now) }
        view.invalidate()
        if (!ticking && ((dots.isNotEmpty() && !still) || fades.any { now - it.born < LIFE })) {
            ticking = true
            Choreographer.getInstance().postFrameCallback(frame)
        }
    }

    /** A word fading and rising in; drawn, never measured, so the line around it never moves. */
    internal class Fade(val born: Long, private val rise: Float) : CharacterStyle(), UpdateAppearance {
        private var a = 0f

        fun at(now: Long) {
            a = EASE.getInterpolation(((now - born).toFloat() / LIFE).coerceIn(0f, 1f))
        }

        override fun updateDrawState(tp: TextPaint) {
            fade(tp, a)
            tp.baselineShift += ((1 - a) * rise).roundToInt()
        }
    }

    /** One typing dot: the console's `dot` keyframes, each a sixth of a beat behind the last. */
    private class Pulse(private val i: Int) : CharacterStyle(), UpdateAppearance {
        /** Full until the first frame: under reduced motion the dots hold still, lit. */
        private var a = 1f

        fun at(now: Long) {
            val t = ((now + BEAT - i * BEAT / 6) % BEAT).toFloat() / BEAT
            a = DIM + (1 - DIM) * sin(PI * (t / PEAK).coerceAtMost(1f)).toFloat().coerceAtLeast(0f)
        }

        override fun updateDrawState(tp: TextPaint) {
            tp.color = JarvisTokens.Color.ACCENT
            fade(tp, a)
        }
    }

    companion object {
        /** A streaming reply's last word is drawn once nothing has arrived for this long. */
        const val SETTLE_MS = JarvisTokens.Motion.Dur.FADE.toLong()
        private const val LIFE = JarvisTokens.Motion.Dur.FADE
        private const val BEAT = JarvisTokens.Motion.Dur.BEAT
        private const val SPAN = Spanned.SPAN_EXCLUSIVE_EXCLUSIVE
        private const val DOTS = "• • •"
        /** The dots' rest and the share of the beat their rise takes (`dot` at 40%). */
        private const val DIM = 0.25f
        private const val PEAK = 0.8f
        private val WORD = Regex("""\S+""")
        private val EASE = JarvisUi.EASE_OUT
        private val WHOLE_WORDS_END = Regex("""\s\S*\z""")
        private val FENCE_LINE = Regex("""(?m)^[ \t]*```""")
        private val BARE_FENCE = Regex("""(^|\n)[ \t]*```[^\n]*\z""")
        private val END_MARK = Regex("""(\*\*|__|~~|`)\s*\z""")
        private val END_MARKER = Regex("""(^|\n)[ \t]*(?:[-*+]|\d+[.)]|#{1,6}|>)[ \t]*\z""")
        private val FENCED = Regex("""(?m)^[ \t]*```[\s\S]*?^[ \t]*```""")
        private val INLINE_CODE = Regex("""`[^`]*`""")
        private val TRAILING_SPACE = Regex("""\s*\z""")

        /** The text and its glow at [a] of their alpha. */
        private fun fade(tp: TextPaint, a: Float) {
            tp.color = scaled(tp.color, a)
            if (tp.shadowLayerRadius > 0f) {
                tp.setShadowLayer(tp.shadowLayerRadius, tp.shadowLayerDx, tp.shadowLayerDy, scaled(tp.shadowLayerColor, a))
            }
        }

        private fun scaled(color: Int, a: Float): Int =
            (((color ushr 24) * a).roundToInt() shl 24) or (color and 0xFFFFFF)

        /** A streaming reply cut after its last whole word: a half-arrived word is not drawn yet. */
        fun wholeWords(text: String): String {
            val end = WHOLE_WORDS_END.find(text) ?: return ""
            return text.substring(0, end.range.first + 1)
        }

        /**
         * A streaming reply's open Markdown closed: an unclosed fence, inline code or ** gets its
         * pair, and a marker with nothing after it yet (a bare "- " or "**" at the end) is held back.
         */
        fun closeOpen(md: String): String {
            if (FENCE_LINE.findAll(md).count() % 2 == 1) {
                // Inside a code block: a fence with nothing under it yet is held back, else closed.
                val bare = BARE_FENCE.find(md)
                return if (bare != null) md.substring(0, bare.range.first + bare.groupValues[1].length) else "${md.trimEnd()}\n```"
            }
            // A mark at the very end that opens rather than closes has nothing to style yet.
            var out = END_MARK.find(md)?.let { m -> if (open(md.substring(0, m.range.first), m.groupValues[1])) md else md.substring(0, m.range.first) } ?: md
            out = END_MARKER.find(out)?.let { m -> out.substring(0, m.range.first) + m.groupValues[1] } ?: out
            val prose = FENCED.replace(out, "")
            val outside = INLINE_CODE.replace(prose, "")
            val closers = if (open(prose, "`")) "`" else listOf("**", "__", "~~").filter { open(outside, it) }.joinToString("")
            // Closed against the last word, not after the space that follows it.
            val ws = TRAILING_SPACE.find(out)!!
            return out.substring(0, ws.range.first) + closers + ws.value
        }

        /** An odd count of [mark] in [text]: the last one opened something not yet closed. */
        private fun open(text: String, mark: String) = text.split(mark).size % 2 == 0
    }
}
