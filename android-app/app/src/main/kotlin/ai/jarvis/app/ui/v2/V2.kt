package ai.jarvis.app.ui.v2

import ai.jarvis.app.assist.island.IslandSpace
import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.AppTheme
import ai.jarvis.app.ui.theme.JarvisThemes
import ai.jarvis.app.ui.theme.JarvisTokens
import android.graphics.RectF
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.input.pointer.isOutOfBounds
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.onLongClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import kotlinx.coroutines.launch
import kotlin.math.max

/**
 * The phone's half of the v2 language (docs/design/v2/PROMPT.md §1, `aura-web/src/lib/ds`):
 * the same few parts the console draws, in Compose, so a native screen and the console read
 * as one thing. Content is projected light: a dark radial behind the words, one bracket at
 * the top-left, glowing text. No cards, no glass, no blur, no big radii.
 *
 * Every value is a [JarvisTokens] name. The web's `--jv-projection-bracket` (16px) and
 * `--jv-projection-bleed` (30px) have no Kotlin token of their own yet, so they ride the
 * nearest spacing steps ([JarvisTokens.Space.SECTION], [JarvisTokens.Size.SHEET]).
 *
 * Does not cover the core (the web's nine-state `Core`): the phone's reactor is still
 * `reactor/`, and porting the v2 engine is its own task.
 */
object V2 {
    // Getters, not values: each reads the worn pack (ActiveTheme, T-0869), so a persona
    // switch repaints a screen already up. JARVIS's pack equals these tokens.
    val Bg: Color get() = ActiveTheme.color("bg.void", fallback = JarvisTokens.Color.BG)
    val Text: Color get() = ActiveTheme.color("text", fallback = JarvisTokens.Color.TEXT)
    val TextDim: Color get() = ActiveTheme.color("text.secondary", fallback = JarvisTokens.Color.TEXT_DIM)
    val TextFaint: Color get() = ActiveTheme.color("text.label", fallback = JarvisTokens.Color.TEXT_FAINT)

    /** The ~2.4 s a press must last before an irreversible thing happens (`--jv-dur-hold`). */
    const val HOLD_MS = JarvisTokens.Motion.Dur.HOLD
}

/**
 * The five lights the language has; each names what it means, not how it looks. Each
 * is the worn pack's [role] (AURA's violet accent, JARVIS's cyan), the token otherwise.
 */
enum class Tone(private val role: String, private val argb: Int, private val textArgb: Int? = null) {
    ACCENT("accent", JarvisTokens.Color.ACCENT),
    /** Needs you: an approval, a held action. */
    WARN("attention", JarvisTokens.Color.WARN),
    DANGER("danger", JarvisTokens.Color.DANGER, JarvisTokens.Color.DANGER_TEXT),
    OK("ok", JarvisTokens.Color.OK),
    /** Memory and models. */
    VIOLET("memory", JarvisTokens.Color.VIOLET);

    val color: Color get() = ActiveTheme.color(role, fallback = argb)

    /** The colour for words in this tone: danger's mark is too dark to read as text. */
    val text: Color get() = textArgb?.let { Color(it) } ?: color
}

private val SQUARE = RoundedCornerShape(JarvisTokens.Radius.SM.dp)
private val PILL = RoundedCornerShape(percent = 50)

/** AURA's controls are pills, JARVIS's square-cornered (the pack's `toggle`). */
private val SHAPE: RoundedCornerShape get() = if (ActiveTheme.pill) PILL else SQUARE

/** The glow every projected word carries (`--jv-glow-text`). */
@Composable
private fun glow(tone: Tone = Tone.ACCENT): Shadow =
    Shadow(color = tone.color.copy(alpha = 0.45f), blurRadius = JarvisTokens.Space.STEP.toFloat())

/**
 * The stage's ground: never flat, lit from the core outward (`--jv-chrome-stage-ground`).
 * Drawn under every v2 screen, so a native screen opened from the console lands on the
 * same light it left. Under a pack whose ground is a starfield (AURA) that is her space, the
 * void, the nebulae and the still sky the island draws ([IslandSpace], T-0949).
 *
 * Does not cover the twinkle or a blur: the stars hold still and nothing is frosted.
 */
@Composable
fun Stage(modifier: Modifier = Modifier, content: @Composable BoxScope.() -> Unit) {
    AppTheme {
        // Read here, in composition, so a persona switch recomposes the stage.
        val glow = ActiveTheme.color("bg.nebula", fallback = JarvisTokens.Color.CORE_GLOW)
        val haze = ActiveTheme.color("bg.deep", fallback = JarvisTokens.Color.CORE_HAZE)
        val theme = ActiveTheme.theme
        val density = LocalDensity.current.density
        val space = remember(theme, density) { IslandSpace.of(theme, density) }
        val window = remember { RectF() }
        Box(
            modifier
                .fillMaxSize()
                .background(V2.Bg)
                .drawBehind {
                    if (space != null) {
                        window.set(0f, 0f, size.width, size.height)
                        space.draw(drawContext.canvas.nativeCanvas, window, 0f)
                        return@drawBehind
                    }
                    val center = Offset(size.width / 2f, size.height * 0.44f)
                    drawRect(
                        Brush.radialGradient(
                            0f to glow,
                            0.42f to haze,
                            0.8f to V2.Bg,
                            center = center,
                            radius = max(size.width, size.height) * 0.6f,
                        ),
                    )
                },
            content = content,
        )
    }
}

/**
 * Words projected into the room (`$lib/ds` Projection): the dark radial backing and the
 * tone's faint bleed reaching past the box, one bracket at the top-left glowing outward,
 * and an edge line down the left only when [pinned]. Under a starfield the box is also the
 * pack's panel glass (`panel.fill`, `panel.edge`, as the console's Projection wears them) and
 * the backing stays inside it, so the sky reads between the panels (T-0949).
 *
 * Does not cover the console's blur, its gradient rim or its star mark: the glass here is a
 * fill and a hairline.
 *
 * [bracket] and [stroke] grow for the one thing that must not be missed (the approval
 * board's 22 px, 2 px amber bracket).
 */
@Composable
fun Projection(
    modifier: Modifier = Modifier,
    tone: Tone = Tone.ACCENT,
    bracket: Dp = JarvisTokens.Space.SECTION.dp,
    stroke: Dp = JarvisTokens.Space.HAIRLINE.dp,
    pinned: Boolean = false,
    content: @Composable ColumnScope.() -> Unit,
) {
    val theme = ActiveTheme.theme
    val glass = remember(theme) { Glass.of(theme) }
    Column(
        modifier
            .drawBehind { projection(tone, bracket.toPx(), stroke.toPx(), pinned, glass) }
            .padding(horizontal = JarvisTokens.Space.SECTION.dp, vertical = JarvisTokens.Space.GAP.dp),
        verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
    ) { CompositionLocalProvider(LocalBacked provides true) { content() } }
}

/**
 * The island's card (T-0695) for an Activity: the wake overlay's shape and backing, at the top,
 * for the consent door. Opaque, so nothing behind it reads through, and the one v2 surface with
 * an edge (DECISIONS #80). Does not cover the window's dim or blur, which are the Activity's.
 */
@Composable
fun IslandCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val shape = RoundedCornerShape(JarvisTokens.Material.Floating.RADIUS_DP.dp)
    Column(
        modifier
            .widthIn(max = JarvisTokens.Material.Floating.CARD_WIDTH_DP.dp)
            .background(Color(JarvisTokens.Material.Floating.FILL), shape)
            .border(JarvisTokens.Space.HAIRLINE.dp, Color(JarvisTokens.Material.Floating.EDGE), shape)
            .padding(horizontal = JarvisTokens.Space.SECTION.dp, vertical = JarvisTokens.Space.GAP.dp),
        verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
    ) { CompositionLocalProvider(LocalBacked provides true) { content() } }
}

/** A starfield pack's panel glass: its fill, top to bottom, and its edge. Null for any other pack. */
private class Glass(val fill: Brush, val edge: Color?) {
    companion object {
        fun of(theme: JarvisThemes.Theme): Glass? {
            if (theme.ground != "starfield") return null
            val fill = ActiveTheme.rgbas(theme.colors["panel.fill"]).takeIf { it.size >= 2 } ?: return null
            return Glass(Brush.verticalGradient(fill.map { Color(it) }), ActiveTheme.rgbas(theme.colors["panel.edge"]).firstOrNull()?.let { Color(it) })
        }
    }
}

// How much of the ground a projection's backing puts under its words, at its strongest.
private const val BACKING = 0.85f

private fun DrawScope.projection(tone: Tone, bracket: Float, stroke: Float, pinned: Boolean, glass: Glass?) {
    // The backing is an ellipse on the box grown by the bleed (CSS `closest-side` on the
    // inset box): squash a circle rather than stretch a gradient, so the falloff is the
    // same on a wide card as on a tall one. Glass has no bleed (the console's `inset: 0`):
    // a backing past the box would put out the stars round every panel.
    val bleed = if (glass == null) JarvisTokens.Size.SHEET.dp.toPx() else 0f
    val rx = size.width / 2f + bleed
    val ry = size.height / 2f + bleed
    val center = Offset(size.width / 2f, size.height / 2f)
    val corner = CornerRadius(JarvisTokens.Radius.MD.dp.toPx())
    // Under glass the box is first the ground at the backing's strength, edge to edge: the
    // round backing fades out at the sides, and a star there at full strength sat behind the
    // first letter of a row (1.13:1 in the accessibility gate).
    if (glass != null) drawRoundRect(V2.Bg.copy(alpha = BACKING), cornerRadius = corner)
    scale(scaleX = rx / ry, scaleY = 1f, pivot = center) {
        drawCircle(
            Brush.radialGradient(
                0f to tone.color.copy(alpha = 0.09f),
                1f to Color.Transparent,
                center = center,
                radius = ry,
            ),
            radius = ry,
            center = center,
        )
        drawCircle(
            Brush.radialGradient(
                0f to V2.Bg.copy(alpha = BACKING),
                0.7f to V2.Bg.copy(alpha = 0.5f),
                1f to Color.Transparent,
                center = center,
                radius = ry,
            ),
            radius = ry,
            center = center,
        )
    }
    if (glass != null) {
        drawRoundRect(glass.fill, cornerRadius = corner)
        val hair = JarvisTokens.Space.HAIRLINE.dp.toPx()
        glass.edge?.let {
            drawRoundRect(it, Offset(hair / 2f, hair / 2f), Size(size.width - hair, size.height - hair), corner, Stroke(hair))
        }
    }
    // The bracket's glow first, under its line (`--jv-glow-bracket`).
    val halo = tone.color.copy(alpha = 0.3f)
    drawLine(halo, Offset.Zero, Offset(bracket, 0f), stroke * 4f)
    drawLine(halo, Offset.Zero, Offset(0f, bracket), stroke * 4f)
    drawLine(tone.color, Offset(0f, stroke / 2f), Offset(bracket, stroke / 2f), stroke)
    drawLine(tone.color, Offset(stroke / 2f, 0f), Offset(stroke / 2f, bracket), stroke)
    if (pinned) {
        drawLine(
            Brush.verticalGradient(listOf(Color.Transparent, tone.color.copy(alpha = 0.6f), Color.Transparent)),
            Offset(0f, 0f),
            Offset(0f, size.height),
            stroke,
        )
    }
}

/** A chrome label (`$lib/ds` Label): mono, uppercase, wide, faint unless it means something. */
@Composable
fun Label(text: String, modifier: Modifier = Modifier, tone: Tone? = null) {
    val bare = onBareStage()
    val layout = remember { arrayOfNulls<TextLayoutResult>(1) }
    Text(
        text.uppercase(),
        if (bare) modifier.drawBehind { stageScrim(layout[0]) } else modifier,
        onTextLayout = { layout[0] = it },
        color = tone?.text ?: V2.TextFaint,
        style = TextStyle(
            fontFamily = ActiveTheme.mono,
            fontWeight = FontWeight(JarvisTokens.Weight.LABEL),
            fontSize = JarvisTokens.Type.LABEL.sp,
            letterSpacing = JarvisTokens.Tracking.LABEL.em,
        ),
    )
}

/** The one line a projection is about: the display face, light, glowing. */
@Composable
fun Headline(text: String, modifier: Modifier = Modifier) {
    val bare = onBareStage()
    val layout = remember { arrayOfNulls<TextLayoutResult>(1) }
    Text(
        text,
        if (bare) modifier.drawBehind { stageScrim(layout[0]) } else modifier,
        onTextLayout = { layout[0] = it },
        color = V2.Text,
        style = TextStyle(
            fontFamily = ActiveTheme.display,
            fontWeight = FontWeight(JarvisTokens.Weight.DISPLAY),
            fontSize = JarvisTokens.Type.TITLE.sp,
            shadow = glow(),
        ),
    )
}

/** True inside a [Projection] or an [IslandCard]: the words already sit on their own backing. */
private val LocalBacked = compositionLocalOf { false }

/** Text of the kit ([Label], [Headline], [Body]) that stands on the starfield's bare stage and so needs a [stageScrim]. */
@Composable
private fun onBareStage() = !LocalBacked.current && ActiveTheme.theme.ground == "starfield"

/**
 * Under a starfield, the ground under bare stage text: the stage's void, strong only
 * under the lines and feathered out to nothing, so a star falls dark under a word and there is
 * no edge to see. The stars are as bright as the words (1.03:1 for dim text on the
 * brightest, T-1323), so no star cap could hold AA and keep a star. Not a card: no edge, no
 * fill colour of its own, and the same dark the sky is already.
 *
 * Does not cover text wider than its own layout (a shadow's bleed) or a screen that draws
 * words outside the kit's [Body], [Label] and [Headline] on the bare stage.
 */
private fun DrawScope.stageScrim(layout: TextLayoutResult?) {
    if (layout == null || layout.lineCount == 0) return
    var left = Float.MAX_VALUE
    var right = 0f
    for (i in 0 until layout.lineCount) {
        left = minOf(left, layout.getLineLeft(i))
        right = maxOf(right, layout.getLineRight(i))
    }
    // Outermost first. Each rounded layer sits one dp further out, its corners growing with it,
    // so the ends are round and the whole is an offset of one shape, not a rectangle.
    for ((pad, alpha) in SCRIM_LAYERS) {
        val p = pad.dp.toPx()
        drawRoundRect(
            V2.Bg.copy(alpha = alpha),
            Offset(left - p, -p),
            Size(right - left + 2 * p, size.height + 2 * p),
            CornerRadius(JarvisTokens.Radius.SM.dp.toPx() + p),
        )
    }
}

/** The scrim's strength under the glyph lines: below [BACKING], and still 7.4:1 for dim text on the brightest star. */
private const val SCRIM_CORE = 0.8f

/**
 * (distance out from the core in dp, the layer's own alpha), outermost first. The strength at a
 * distance falls from [SCRIM_CORE] to nothing over [JarvisTokens.Space.WIDE] dp along a
 * smoothstep, flat at both ends; each layer's alpha is what, laid over the ones outside it, makes
 * that cumulative strength, so no step is more than a level of the sky's own wash (T-1323).
 */
private val SCRIM_LAYERS: List<Pair<Float, Float>> = run {
    val n = JarvisTokens.Space.WIDE
    fun strength(k: Int): Float {
        if (k >= n) return 0f
        val t = k.toFloat() / n
        return SCRIM_CORE * (1f - t * t * (3f - 2f * t))
    }
    (n - 1 downTo 0).map { k ->
        (JarvisTokens.Space.MICRO + k).toFloat() to (1f - (1f - strength(k)) / (1f - strength(k + 1)))
    }
}

/**
 * Running text. [dim] for the line under the line that matters. On the bare stage of a
 * starfield pack it carries a [stageScrim], so a star under it cannot sink it below AA; inside
 * a [Projection] the projection's own backing does that and nothing is added.
 */
@Composable
fun Body(text: String, modifier: Modifier = Modifier, dim: Boolean = false, tone: Tone? = null) {
    val bare = onBareStage()
    // A plain holder, not state: a state write in onTextLayout left a global apply pending
    // after any Compose draw with no rule behind it, and later tests never got idle. Draw
    // always follows the layout that filled it, so nothing needs to observe it.
    val layout = remember { arrayOfNulls<TextLayoutResult>(1) }
    Text(
        text,
        if (bare) modifier.drawBehind { stageScrim(layout[0]) } else modifier,
        onTextLayout = { layout[0] = it },
        color = tone?.text ?: if (dim) V2.TextDim else V2.Text,
        style = TextStyle(
            fontFamily = ActiveTheme.mono,
            fontWeight = FontWeight(JarvisTokens.Weight.BODY),
            fontSize = JarvisTokens.Type.BODY.sp,
        ),
    )
}

/** A number with its unit (`$lib/ds` Figure): the display face, the unit as a label. */
@Composable
fun Figure(value: String, unit: String = "", modifier: Modifier = Modifier, tone: Tone = Tone.ACCENT) {
    Row(modifier, verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.TIGHT.dp)) {
        Text(
            value,
            color = V2.Text,
            style = TextStyle(
                fontFamily = ActiveTheme.display,
                fontWeight = FontWeight(JarvisTokens.Weight.DISPLAY),
                fontSize = JarvisTokens.Type.TITLE.sp,
                shadow = glow(tone),
            ),
        )
        if (unit.isNotEmpty()) Label(unit, Modifier.padding(bottom = JarvisTokens.Space.TIGHT.dp), tone)
    }
}

/** The faint rule between two parts of one projection. */
@Composable
fun Hairline(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(JarvisTokens.Space.HAIRLINE.dp).background(ActiveTheme.color("line", "bg.nebula", fallback = JarvisTokens.Color.LINE)))
}

/**
 * A control (the board's "Change" / "Don't send"): uppercase, tracked, a 1 dp edge in its
 * tone at half strength over a faint wash. [filled] is the screen's one primary.
 * At least [JarvisTokens.Size.TOUCH] tall and wide, whatever its words.
 */
@Composable
fun ActionButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    tone: Tone = Tone.ACCENT,
    enabled: Boolean = true,
    filled: Boolean = false,
) {
    Box(
        modifier
            .heightIn(min = JarvisTokens.Size.TOUCH.dp)
            .widthIn(min = JarvisTokens.Size.TOUCH.dp)
            .alpha(if (enabled) 1f else 0.4f)
            .border(JarvisTokens.Space.HAIRLINE.dp, tone.color.copy(alpha = if (filled) 1f else 0.5f), SHAPE)
            .background(tone.color.copy(alpha = if (filled) 0.22f else 0.08f), SHAPE)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .padding(horizontal = JarvisTokens.Space.SECTION.dp, vertical = JarvisTokens.Space.STEP.dp),
        contentAlignment = Alignment.Center,
    ) {
        ControlText(text, tone)
    }
}

@Composable
private fun ControlText(text: String, tone: Tone) {
    Text(
        text.uppercase(),
        color = tone.text,
        style = TextStyle(
            fontFamily = ActiveTheme.mono,
            fontWeight = FontWeight(JarvisTokens.Weight.STRONG),
            fontSize = JarvisTokens.Type.MONO.sp,
            letterSpacing = JarvisTokens.Tracking.CONTROL.em,
        ),
    )
}

/**
 * Hold to confirm (`$lib/ds` HoldButton): the press must last [holdMs] before [onConfirm]
 * runs, the tone's fill sweeping across while it does. Lifting early, sliding off, a
 * cancelled touch, the screen going away or [enabled] turning false mid-press all cancel,
 * and the fill falls back.
 *
 * TalkBack's double-tap-and-hold is the same deliberate gesture: it reaches [onConfirm]
 * through the long-click action, never through a plain double-tap.
 *
 * Does not refuse a touch that passes through another window: the host sets
 * `filterTouchesWhenObscured` on its root view, which covers every control on the screen.
 */
@Composable
fun HoldButton(
    text: String,
    onConfirm: () -> Unit,
    modifier: Modifier = Modifier,
    tone: Tone = Tone.WARN,
    enabled: Boolean = true,
    hint: String = "Hold to confirm",
    holdMs: Int = V2.HOLD_MS,
) {
    val progress = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val confirm by rememberUpdatedState(onConfirm)
    val live by rememberUpdatedState(enabled)
    Box(
        modifier
            .heightIn(min = JarvisTokens.Size.DROP.dp)
            .widthIn(min = JarvisTokens.Size.TOUCH.dp)
            .alpha(if (enabled) 1f else 0.4f)
            .border(JarvisTokens.Space.HAIRLINE.dp, tone.color, SHAPE)
            .drawBehind {
                drawRect(tone.color.copy(alpha = 0.22f), size = Size(size.width * progress.value, size.height))
            }
            .pointerInput(enabled, holdMs) {
                if (!enabled) return@pointerInput
                awaitEachGesture {
                    val down = awaitFirstDown()
                    down.consume()
                    val hold = scope.launch {
                        progress.snapTo(0f)
                        progress.animateTo(1f, tween(holdMs, easing = LinearEasing))
                        if (live) confirm()
                    }
                    try {
                        while (true) {
                            val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id } ?: break
                            if (!change.pressed || change.isOutOfBounds(size, extendedTouchPadding)) break
                            change.consume()
                        }
                    } finally {
                        // Every way out of the press lands here, including this block being
                        // cancelled because `enabled` flipped or the screen left.
                        if (hold.isActive) {
                            hold.cancel()
                            scope.launch { progress.animateTo(0f, tween(JarvisTokens.Motion.Dur.FAST)) }
                        }
                    }
                }
            }
            .semantics(mergeDescendants = true) {
                role = Role.Button
                if (enabled) onLongClick(label = hint) { confirm(); true } else disabled()
            }
            .padding(horizontal = JarvisTokens.Space.SECTION.dp, vertical = JarvisTokens.Space.STEP.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            ControlText(text, tone)
            Label(hint, tone = null)
        }
    }
}

/**
 * A rule, as a choice (`$lib/ds` RuleChip): its kind as a label, then its words. [selected]
 * lights its edge; tapping it picks it. At least [JarvisTokens.Size.TOUCH] tall.
 */
@Composable
fun RuleChip(
    text: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    kind: String = "Rule",
    tone: Tone = Tone.ACCENT,
    enabled: Boolean = true,
) {
    Row(
        modifier
            .heightIn(min = JarvisTokens.Size.TOUCH.dp)
            .alpha(if (enabled) 1f else 0.4f)
            .border(JarvisTokens.Space.HAIRLINE.dp, tone.color.copy(alpha = if (selected) 1f else 0.4f), SHAPE)
            .background(if (selected) tone.color.copy(alpha = 0.12f) else Color.Transparent, SHAPE)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onClick)
            .padding(horizontal = JarvisTokens.Space.GAP.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.STEP.dp),
    ) {
        Label(kind, tone = if (selected) tone else null)
        Text(
            text,
            color = V2.Text,
            style = TextStyle(
                fontFamily = ActiveTheme.mono,
                fontSize = JarvisTokens.Type.BODY.sp,
                shadow = if (selected) glow(tone) else null,
            ),
        )
    }
}

/** The four states every screen has as real states (the design system's rule). */
enum class Screen { LOADING, EMPTY, ERROR, OFFLINE }

/**
 * A screen's not-ready face as one projection: what is happening in one line, and the one
 * thing to do about it when there is one.
 */
@Composable
fun ScreenState(
    state: Screen,
    line: String,
    modifier: Modifier = Modifier,
    action: String? = null,
    onAction: (() -> Unit)? = null,
) {
    val tone = when (state) {
        Screen.LOADING, Screen.EMPTY -> Tone.ACCENT
        Screen.ERROR -> Tone.DANGER
        Screen.OFFLINE -> Tone.WARN
    }
    val word = when (state) {
        Screen.LOADING -> "Loading"
        Screen.EMPTY -> "Nothing yet"
        Screen.ERROR -> "Something went wrong"
        Screen.OFFLINE -> "Offline"
    }
    Projection(modifier.fillMaxWidth(), tone = tone) {
        Label(word, tone = tone)
        Body(line, dim = state == Screen.LOADING)
        if (action != null && onAction != null) ActionButton(action, onAction, tone = tone)
    }
}

/**
 * A yes/no setting: its words, then a control that says its state in words and lights when on.
 * The whole row is the touch target, at least [JarvisTokens.Size.TOUCH] tall, and TalkBack
 * hears it as a switch. Off reads dim, so a column of them shows at a glance what is on.
 */
@Composable
fun Toggle(text: String, checked: Boolean, onChange: (Boolean) -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Row(
        modifier
            .fillMaxWidth()
            .heightIn(min = JarvisTokens.Size.TOUCH.dp)
            .alpha(if (enabled) 1f else 0.4f)
            .toggleable(value = checked, enabled = enabled, role = Role.Switch, onValueChange = onChange),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
    ) {
        Body(text, Modifier.weight(1f), dim = !checked)
        Box(
            Modifier
                .widthIn(min = JarvisTokens.Size.TOUCH.dp)
                .border(JarvisTokens.Space.HAIRLINE.dp, Tone.ACCENT.color.copy(alpha = if (checked) 1f else 0.4f), SHAPE)
                .background(if (checked) Tone.ACCENT.color.copy(alpha = 0.22f) else Color.Transparent, SHAPE)
                .padding(horizontal = JarvisTokens.Space.STEP.dp, vertical = JarvisTokens.Space.TIGHT.dp),
            contentAlignment = Alignment.Center,
        ) {
            Label(if (checked) "On" else "Off", tone = if (checked) Tone.ACCENT else null)
        }
    }
}

/**
 * A typed value: its name as a label, the value in the mono face on the field's ground, an edge
 * that lights while it has focus. [secret] hides the characters. The field's accessibility name
 * is [label], so a screen reader and a UI test find it by that, not by its placeholder.
 */
@Composable
fun Field(
    label: String,
    value: String,
    onChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    secret: Boolean = false,
    enabled: Boolean = true,
) {
    var focused by remember { mutableStateOf(false) }
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.TIGHT.dp)) {
        Label(label)
        BasicTextField(
            value = value,
            onValueChange = onChange,
            enabled = enabled,
            singleLine = true,
            textStyle = TextStyle(fontFamily = ActiveTheme.mono, fontSize = JarvisTokens.Type.FIELD.sp, color = V2.Text),
            cursorBrush = SolidColor(Tone.ACCENT.color),
            visualTransformation = if (secret) PasswordVisualTransformation() else VisualTransformation.None,
            keyboardOptions = if (secret) KeyboardOptions(keyboardType = KeyboardType.Password) else KeyboardOptions.Default,
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = JarvisTokens.Size.TOUCH.dp)
                .alpha(if (enabled) 1f else 0.4f)
                .onFocusChanged { focused = it.isFocused }
                .semantics { contentDescription = label }
                .border(JarvisTokens.Space.HAIRLINE.dp, Tone.ACCENT.color.copy(alpha = if (focused) 1f else 0.4f), SHAPE)
                .background(ActiveTheme.color("bg.deep", fallback = JarvisTokens.Color.FIELD), SHAPE)
                .padding(horizontal = JarvisTokens.Space.GAP.dp, vertical = JarvisTokens.Space.STEP.dp),
            decorationBox = { inner ->
                Box(contentAlignment = Alignment.CenterStart) {
                    if (value.isEmpty() && placeholder.isNotEmpty()) Body(placeholder, dim = true)
                    inner()
                }
            },
        )
    }
}

/**
 * A part of a long screen folded to one line (v2 §1, progressive disclosure): its name, what it
 * is set to now, and the rest only once opened. The header is a heading for TalkBack and says
 * whether it is open. A [tone] other than the accent lights the name and the line while folded:
 * the part needs the person (a missing grant).
 */
@Composable
fun Fold(
    title: String,
    summary: String,
    open: Boolean,
    onToggle: () -> Unit,
    modifier: Modifier = Modifier,
    tone: Tone = Tone.ACCENT,
    content: @Composable ColumnScope.() -> Unit,
) {
    val alarm = tone != Tone.ACCENT
    Projection(modifier.fillMaxWidth(), tone = tone) {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = JarvisTokens.Size.TOUCH.dp)
                .clickable(role = Role.Button, onClickLabel = if (open) "Fold" else "Open", onClick = onToggle)
                .semantics(mergeDescendants = true) {
                    heading()
                    stateDescription = if (open) "Open" else "Folded"
                },
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(JarvisTokens.Space.GAP.dp),
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(JarvisTokens.Space.MICRO.dp)) {
                Label(title, tone = if (open || alarm) tone else null)
                if (summary.isNotEmpty()) Body(summary, dim = !alarm, tone = if (alarm) tone else null)
            }
            Label(if (open) "Fold" else "Open", tone = tone)
        }
        if (open) content()
    }
}

/** How far a long job has come; [fraction] null when it cannot say (the track alone). */
@Composable
fun Meter(fraction: Float?, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(JarvisTokens.Space.TIGHT.dp).background(ActiveTheme.color("line", "bg.nebula", fallback = JarvisTokens.Color.LINE))) {
        if (fraction != null) {
            Box(Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).fillMaxHeight().background(Tone.ACCENT.color))
        }
    }
}
