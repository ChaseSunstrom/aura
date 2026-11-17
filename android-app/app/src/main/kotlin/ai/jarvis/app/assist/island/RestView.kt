package ai.jarvis.app.assist.island

import ai.jarvis.app.assist.island.IslandRest.Network
import ai.jarvis.app.assist.island.IslandRest.Presence
import ai.jarvis.app.glass.ReactorView
import ai.jarvis.app.reactor.core.Core
import ai.jarvis.app.reactor.core.CoreCanvas
import ai.jarvis.app.reactor.core.CoreEngine
import ai.jarvis.app.reactor.core.Galaxy
import ai.jarvis.app.reactor.core.GalaxyCanvas
import ai.jarvis.app.reactor.core.GalaxyModel
import ai.jarvis.app.reactor.core.LIGHT
import ai.jarvis.app.reactor.core.coreStateFor
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisTokens
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.Drawable
import android.util.TypedValue
import android.view.View
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * The resting island drawn (T-0890): a capsule around the camera hole with the worn persona's
 * own core in its left end, and a word either side of the hole, or the time on the left and the
 * phone's status on the right.
 *
 * In the worn pack (the owner, 2026-11-10: "why does the dynamic island not match the theme, and
 * can you make the plane circle be the core"): its fill, edge and type are the pack's, under a
 * starfield pack (AURA) its ground is her space as the conversation's island draws it, and the
 * core is the persona's (AURA's galaxy, JARVIS's reactor) in the presence's state.
 *
 * As the status bar (T-0969) it shows an icon for each app with a notification showing between
 * the core and the time, growing to the left so the camera stays where it is; the icons that do
 * not fit before the screen's edge are counted ("+n"), since the island is never touched.
 *
 * Draws only when what it says changes: the core is one frozen frame, computed once per state
 * and pack, so a phone at rest spends no frames on it. Does not cover the core's motion (that is
 * the conversation's island) or where its window goes ([RestIsland]).
 */
class RestView(context: Context) : View(context) {
    var face: IslandRest.Face = IslandRest.face("", Presence.LISTENING, null)
        set(v) {
            if (field == v) return
            field = v
            invalidate()
        }

    /** The core's state: listening, ready (muted) or offline. */
    var presence: Presence = Presence.LISTENING
        set(v) {
            if (field == v) return
            field = v
            invalidate()
        }

    /** One app's small icon each, newest first ([ai.jarvis.app.car.dash.tiles.HeadwayNotificationListener.islandIcons]). */
    var icons: List<Drawable> = emptyList()
        set(v) {
            if (field == v) return
            field = v
            requestLayout()
            invalidate()
        }

    private fun dp(v: Int) = JarvisUi.dp(context, v).toFloat()
    private fun sp(v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, v, resources.displayMetrics)

    private val density = resources.displayMetrics.density
    private val pillW = dp(JarvisTokens.Material.Floating.REST_WIDTH_DP)
    private var pillH = dp(JarvisTokens.Material.Floating.REST_HEIGHT_DP)
    private val pad = dp(JarvisTokens.Space.ROW)
    private val snug = dp(JarvisTokens.Space.SNUG)
    private val coreSize = dp(JarvisTokens.Material.Floating.CORE_REST_DP)
    private val glyph = dp(JarvisTokens.Space.GAP)
    /** An icon and the space after it. */
    private val step = glyph + snug

    /** How far left of the bare pill it may grow: to the screen's edge, a snug margin short. */
    private var room = max(0f, resources.displayMetrics.widthPixels / 2f - pillW / 2 - snug)
    private val slots: Int get() = min(icons.size, (room / step).toInt())
    private val drawn: Int get() = if (icons.size > slots) max(0, slots - 1) else slots

    /** The icons only counted, in the last slot: no room for them before the screen's edge. */
    val counted: Int get() = icons.size - drawn
    private val grow: Float get() = slots * step

    /** The camera's centre in the view, px: the pill grows to the left of it. */
    val holeX: Float get() = grow + pillW / 2

    /** How far the window moves, px, to keep [holeX] on the camera as the pill grows. */
    val shift: Int get() = -(grow / 2).roundToInt()

    /** How wide a gap the words leave for the camera. */
    private var gap = snug

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(JarvisTokens.Space.HAIRLINE)
    }
    private val word = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.LABEL_FACE
        textSize = sp(JarvisTokens.Type.LABEL)
        letterSpacing = JarvisTokens.Tracking.LABEL
    }
    private val figure = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = JarvisUi.MONO_FACE
        textSize = sp(JarvisTokens.Type.HINT)
    }
    private val mark = Paint(Paint.ANTI_ALIAS_FLAG)
    private val arc = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(JarvisTokens.Space.MICRO) * 0.75f
        strokeCap = Paint.Cap.ROUND
    }
    private val pill = RectF()

    // The pack's colours, read at each draw: the persona can change while the island rests.
    private var bright = 0
    private var dim = 0
    private var ground = 0

    /** The frozen core, keyed by the pack and the state it was drawn for. */
    private var stillKey = ""
    private var still: Any? = null
    private val reactor = CoreCanvas()
    private val galaxy by lazy { GalaxyCanvas(useShader = !ReactorView.canvasOnly) }
    private val shell by lazy { CoreEngine.fibonacci(LIGHT) }
    private var spaceFor = ""
    private var space: IslandSpace? = null

    /** The pill [tall] px ([IslandRest.height]), its words leaving [hole]'s width; its window is centred on the hole. */
    fun place(hole: IslandRest.Box?, tall: Int, clear: Int = 0) {
        pillH = tall.toFloat()
        gap = (hole?.let { (it.right - it.left).toFloat() } ?: 0f) + 2 * snug
        val centre = hole?.let { (it.left + it.right) / 2f } ?: (resources.displayMetrics.widthPixels / 2f)
        room = max(0f, centre - pillW / 2 - snug - clear)
        requestLayout()
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) = setMeasuredDimension((pillW + grow).toInt(), pillH.toInt())

    private fun wear() {
        val theme = ActiveTheme.theme
        fill.color = ActiveTheme.tint(JarvisTokens.Material.Floating.FILL, "bg.panel", "bg.deep")
        edge.color = ActiveTheme.tint(JarvisTokens.Material.Floating.EDGE, "accent")
        word.color = ActiveTheme.tint(JarvisTokens.Color.TEXT_FAINT, "text.label", "text.secondary")
        bright = ActiveTheme.tint(JarvisTokens.Color.TEXT_BRIGHT, "text")
        dim = ActiveTheme.tint(JarvisTokens.Color.TICK, "text.secondary")
        ground = ActiveTheme.tint(JarvisTokens.Color.BG, "bg.void", "bg.deep")
        if (spaceFor != theme.id) {
            spaceFor = theme.id
            space = IslandSpace.of(theme, density)
        }
    }

    override fun onDraw(canvas: Canvas) {
        wear()
        val cx = holeX
        val cy = height / 2f
        pill.set(cx - pillW / 2 - grow, cy - pillH / 2, cx + pillW / 2, cy + pillH / 2)
        val r = pillH / 2
        space?.draw(canvas, pill, r) ?: canvas.drawRoundRect(pill, r, r, fill)
        canvas.drawRoundRect(pill, r, r, edge)

        // The core, concentric with the capsule's left end.
        core(canvas, pill.left + r - coreSize / 2, cy - coreSize / 2)

        val f = face
        val leftPaint = if (f.network == null) word else figure
        figure.color = bright
        // The apps with a notification showing, after the core; the time keeps its place by the camera.
        var ix = pill.left + 2 * r + snug
        for (i in 0 until drawn) {
            icons[i].mutate().apply {
                setTint(bright)
                setBounds(ix.toInt(), (cy - glyph / 2).toInt(), (ix + glyph).toInt(), (cy + glyph / 2).toInt())
                draw(canvas)
            }
            ix += step
        }
        if (counted > 0) {
            text(canvas, "+$counted", figure, ix, ix + glyph, cy, end = false)
            ix += step
        }
        text(canvas, f.left, leftPaint, ix, cx - gap / 2, cy, end = true)

        var x = cx + gap / 2
        if (f.network == null) {
            text(canvas, f.right, word, x, pill.right - pad, cy, end = false)
            return
        }
        // The status, left to right: the network, do not disturb, the battery.
        when (f.network) {
            Network.CELL -> bars(canvas, x, cy, glyph, f.bars)
            Network.WIFI -> wifi(canvas, x, cy, glyph, f.bars)
            Network.NONE -> Unit
        }
        if (f.network != Network.NONE) x += glyph + snug
        if (f.dnd) {
            moon(canvas, x + glyph / 2, cy, glyph / 2)
            x += glyph + snug
        }
        figure.color = if (f.charging) JarvisTokens.Color.OK else bright
        text(canvas, f.right, figure, x, pill.right - pad, cy, end = false)
    }

    /** The persona's core in [presence]'s state, one frozen frame at ([left], [top]). */
    private fun core(canvas: Canvas, left: Float, top: Float) {
        val state = coreStateFor(IslandRest.core(presence).key)
        val theme = ActiveTheme.theme
        val key = "${theme.id}/$state"
        if (key != stillKey) {
            stillKey = key
            val at = ReactorView.STILL_AT_SECONDS * 1000.0
            still = if (theme.renderer == GALAXY) GalaxyModel.at(state, at = at, pack = GalaxyModel.packOf(theme)) else CoreEngine.frozenAt(state, at = at)
        }
        canvas.save()
        canvas.translate(left, top)
        when (val s = still) {
            is Galaxy -> galaxy.draw(canvas, s, coreSize, coreSize)
            is Core -> reactor.draw(canvas, s, shell, coreSize, coreSize, density)
        }
        canvas.restore()
    }

    /**
     * [s] between [from] and [to], against [to]'s side when [end]. A word wider than the room
     * left beside the camera (a wide hole, a long persona, a large font) is drawn smaller to fit
     * rather than running out of the pill.
     */
    private fun text(canvas: Canvas, s: String, paint: Paint, from: Float, to: Float, cy: Float, end: Boolean) {
        if (s.isEmpty() || to <= from) return
        val size = paint.textSize
        val wide = paint.measureText(s)
        if (wide > to - from) paint.textSize = size * (to - from) / wide
        val m = paint.fontMetrics
        canvas.drawText(s, if (end) to - paint.measureText(s) else from, cy - (m.ascent + m.descent) / 2, paint)
        paint.textSize = size
    }

    /** Four rising bars, [lit] of them bright. */
    private fun bars(canvas: Canvas, x: Float, cy: Float, size: Float, lit: Int) {
        val w = size / 5
        for (i in 0 until 4) {
            mark.color = if (i < lit) bright else dim
            val h = size * (i + 1) / 4
            val left = x + i * (w + w / 3)
            canvas.drawRect(left, cy + size / 2 - h, left + w, cy + size / 2, mark)
        }
    }

    /** Wi-Fi's fan: a dot and three arcs, as many bright as the signal reaches. */
    private fun wifi(canvas: Canvas, x: Float, cy: Float, size: Float, lit: Int) {
        val ox = x + size / 2
        val oy = cy + size / 2
        val shown = (lit * 3 + 3) / 4
        mark.color = if (lit > 0) bright else dim
        canvas.drawCircle(ox, oy - arc.strokeWidth, arc.strokeWidth, mark)
        for (i in 1..3) {
            arc.color = if (i <= shown) bright else dim
            val rr = size * i / 3
            canvas.drawArc(ox - rr, oy - rr, ox + rr, oy + rr, 225f, 90f, false, arc)
        }
    }

    /** Do not disturb: a crescent, the island's ground biting the moon. */
    private fun moon(canvas: Canvas, cx: Float, cy: Float, r: Float) {
        mark.color = bright
        canvas.drawCircle(cx, cy, r * 0.8f, mark)
        mark.color = if (space == null) fill.color else ground
        canvas.drawCircle(cx + r * 0.45f, cy - r * 0.3f, r * 0.7f, mark)
    }

    private companion object {
        /** The pack's renderer name for AURA's galaxy, as V2Core reads it. */
        const val GALAXY = "galaxy"
    }
}
