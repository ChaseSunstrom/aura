package ai.jarvis.app.assist

import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisTokens
import android.content.Context
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.widget.EditText
import android.widget.ScrollView
import android.widget.TextView

/**
 * The console's words on the two assist windows (T-0712): the state as a `Label`, the user's
 * line and the reply as `Subtitles`, the typed line as the shell's dock field
 * (`$lib/ds/Label.svelte`, `Subtitles.svelte`, `Shell.svelte`). Views, because the overlay and
 * the popup are View windows; every value is a JarvisTokens one.
 *
 * The label is TEXT_DIM where the console's is TEXT_FAINT: these windows float over any app,
 * and faint over the scrim on white content is under WCAG AA (overlay_scrim_test.py measures
 * it). The glow replaces the dark legibility shadow: one shadow per TextView, and the scrim is
 * what the contrast test holds. Does not cover sizes, scrolling or line limits: each window
 * keeps its own.
 */
internal object V2Words {
    fun label(view: TextView) = view.apply {
        setTextColor(JarvisTokens.Color.TEXT_DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisTokens.Type.LABEL)
        typeface = JarvisUi.LABEL_FACE
        letterSpacing = JarvisTokens.Tracking.LABEL
        isAllCaps = true
        gravity = Gravity.CENTER
    }

    /**
     * Who said the line under it, as the console's Conversation labels a turn (T-0728): the worn
     * pack's name in its accent (T-0885: it said JARVIS with AURA worn), the user dim,
     * start-aligned over the words. Hidden until there is a line.
     */
    fun who(view: TextView, jarvis: Boolean, name: String = ActiveTheme.name) = label(view).apply {
        text = if (jarvis) name else YOU
        if (jarvis) setTextColor(ActiveTheme.argb("accent", fallback = JarvisTokens.Color.ACCENT))
        gravity = Gravity.START
        visibility = android.view.View.GONE
    }

    /**
     * [jarvis] false is the user's line: dim, as `.user` is. Running text is never tracked.
     * Start-aligned, as the console's conversation turns (T-0728): a centred list read badly.
     */
    fun subtitle(view: TextView, jarvis: Boolean) = view.apply {
        setTextColor(if (jarvis) JarvisTokens.Color.TEXT else JarvisTokens.Color.TEXT_DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, JarvisTokens.Type.RESPONSE)
        typeface = JarvisUi.DISPLAY_FACE
        letterSpacing = 0f
        gravity = Gravity.START
        // `--jv-glow-subtitle`, as V2.kt's glow: the accent's light around the glyphs.
        setShadowLayer(JarvisUi.dp(context, JarvisTokens.Space.STEP).toFloat(), 0f, 0f, JarvisTokens.Color.GLOW)
    }

    /** The dock's field: a pill on the sunken ground, a soft line, the console's hint. */
    fun dock(field: EditText) = field.apply {
        hint = dockHint
        contentDescription = "Message ${ActiveTheme.name}"
        background = GradientDrawable().apply {
            cornerRadius = JarvisUi.dp(context, JarvisTokens.Radius.PILL).toFloat()
            setColor(JarvisTokens.Color.SURFACE_SUNKEN)
            setStroke(JarvisUi.dp(context, JarvisUi.Space.HAIRLINE), JarvisTokens.Color.LINE_SOFT)
        }
        val side = JarvisUi.dp(context, JarvisUi.Space.SECTION)
        setPadding(side, paddingTop, side, paddingBottom)
    }

    /** The shell's placeholder and its accessible name (`chat-input`). */
    val dockHint: String get() = "Ask ${ActiveTheme.name} anything"

    /** The user's turn label (`Conversation.svelte`'s `.who`); the assistant's is the worn name. */
    const val YOU = "You"
}

/**
 * A ScrollView no taller than [cap]: a short reply wraps, a long one scrolls (T-0727: the popup
 * cut a Markdown list off at four lines). Both assist windows hold their reply in one.
 */
internal class CappedScroll(context: Context, private val cap: Int) : ScrollView(context) {
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(cap, MeasureSpec.AT_MOST))
    }
}
