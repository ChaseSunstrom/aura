package ai.jarvis.app.car.holo

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.glass.ReactorFeed
import ai.jarvis.app.reactor.Derived
import ai.jarvis.app.reactor.ReactorInputs
import ai.jarvis.app.reactor.ReactorState
import ai.jarvis.app.reactor.derive
import ai.jarvis.app.reactor.core.Core
import ai.jarvis.app.reactor.core.CoreCanvas
import ai.jarvis.app.reactor.core.CoreEngine
import ai.jarvis.app.reactor.core.Galaxy
import ai.jarvis.app.reactor.core.GalaxyCanvas
import ai.jarvis.app.reactor.core.GalaxyModel
import ai.jarvis.app.reactor.core.GalaxyQuality
import ai.jarvis.app.reactor.core.Inputs
import ai.jarvis.app.reactor.core.LIGHT
import ai.jarvis.app.reactor.core.coreStateFor
import ai.jarvis.app.ui.JarvisUi
import ai.jarvis.app.ui.theme.ActiveTheme
import ai.jarvis.app.ui.theme.JarvisTokens.Color
import ai.jarvis.app.ui.theme.JarvisTokens.Radius
import ai.jarvis.app.ui.theme.JarvisTokens.Size
import ai.jarvis.app.ui.theme.JarvisTokens.Space
import ai.jarvis.app.ui.theme.JarvisTokens.Tracking
import ai.jarvis.app.ui.theme.JarvisTokens.Type
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.text.Layout
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils
import androidx.core.graphics.ColorUtils
import java.util.EnumSet
import kotlin.math.max

/** One thing the driver can do about the card: a drawn button, a strip button and a phrase. */
data class CarAction(val id: String, val label: String, val primary: Boolean = false)

/**
 * The one contextual card (Car.dc.html). [Kind]'s order is the order a card wins the one
 * slot in: a ringing call beats an approval, which beats a message, down to the music.
 */
data class CarCard(
    val kind: Kind,
    val caption: String,
    val title: String,
    val detail: String = "",
    val actions: List<CarAction> = emptyList(),
) {
    enum class Kind { CALL, APPROVAL, MESSAGE, HOME, CALENDAR, MUSIC }
}

/** The card that gets the slot: the most urgent kind, and the first of that kind on a tie. */
fun pickCard(cards: Iterable<CarCard>): CarCard? = cards.minByOrNull { it.kind.ordinal }

/**
 * Everything the zones say, already in words: the screen formats, the drive only draws. The
 * same value draws the same zones.
 */
data class DriveView(
    val reactor: ReactorInputs = ReactorFeed.inputsFrom(Phase.IDLE),
    val turn: Turn? = null,
    /** Said in the turn zone when there is no turn: no fix yet, no route, the house away. */
    val status: String = "",
    val trip: Trip? = null,
    val speed: String? = null,
    val speedUnit: String = "",
    /** What the driver said last, quoted above [said]. */
    val heard: String = "",
    val said: String = "",
    val card: CarCard? = null,
) {
    /**
     * One maneuver, the next. There is no "then" line: a driver reads one instruction at a
     * time, and the route gives the next one when this one is done. [sign] is +1 for a
     * right turn, -1 left, 0 straight on.
     */
    data class Turn(val distance: String, val instruction: String, val sign: Int)
    data class Trip(val destination: String, val eta: String, val detail: String)
    data class Pose(val lat: Double, val lon: Double, val headingDeg: Float)
}

/**
 * The hologram drive on one surface: the scene, then the fixed zones over it (Car.dc.html).
 * [CarSurface] owns one and calls it from its render thread; nothing here is thread-safe.
 *
 * Per frame it allocates nothing of its own: text is laid out again only when its words or
 * the surface change, and every paint, path and shader is made in [resize] or once. The
 * core is the exception: JARVIS's ([CoreCanvas]) makes two gradients, its ring order and
 * its shock list each frame, and either theme's one [Inputs].
 *
 * Does not cover right-to-left text in the right-aligned trip zone (it stays right-aligned)
 * or a card with more than [MAX_ACTIONS] actions: the rest are dropped here and on the strip.
 */
class CarDrive {
    private val ground = HoloGround()
    val scene: HoloScene get() = ground.scene
    /** This drive's own wall clock for the map's light (T-1107); null is the app's. */
    var utc: (() -> Long)?
        get() = ground.utc
        set(v) { ground.utc = v }

    var zones: CarZones = CarZones.layout(Box(0f, 0f, 0f, 0f), 1f); private set
    private var u = 1f
    private var density = 1f

    // --- the zones' type ---------------------------------------------------------------
    private val words = ArrayList<Words>()
    private fun words(
        face: Typeface,
        color: Int,
        align: Layout.Alignment = Layout.Alignment.ALIGN_NORMAL,
        tracking: Float = 0f,
        shape: ((String) -> String)? = null,
    ) = Words(TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = face
        this.color = color
        letterSpacing = tracking
    }, align, shape).also { words += it }

    private val caps: (String) -> String = { it.uppercase() }
    private val right = Layout.Alignment.ALIGN_OPPOSITE

    private val distance = words(JarvisUi.DISPLAY_FACE, Color.TEXT_BRIGHT)
    private val instruction = words(JarvisUi.BODY_FACE, Color.TEXT_BRIGHT)
    private val status = words(JarvisUi.BODY_FACE, Color.TEXT_BRIGHT)
    private val destination = words(JarvisUi.LABEL_FACE, Color.TEXT_DIM, right, Tracking.LABEL, caps)
    private val eta = words(JarvisUi.DISPLAY_FACE, Color.TEXT_BRIGHT, right)
    private val tripDetail = words(JarvisUi.BODY_FACE, Color.TEXT_DIM, right)
    private val speed = words(JarvisUi.DISPLAY_FACE, Color.TEXT_BRIGHT)
    private val speedUnit = words(JarvisUi.LABEL_FACE, Color.TEXT_DIM, tracking = Tracking.LABEL, shape = caps)
    private val heard = words(JarvisUi.BODY_FACE, Color.TEXT_DIM, shape = { "“$it”" })
    private val said = words(JarvisUi.DISPLAY_FACE, Color.TEXT_BRIGHT)
    private val caption = words(JarvisUi.LABEL_FACE, Color.TEXT_DIM, tracking = Tracking.LABEL, shape = caps)
    private val titleBig = words(JarvisUi.DISPLAY_FACE, Color.TEXT_BRIGHT)
    private val titleLine = words(JarvisUi.DISPLAY_FACE, Color.TEXT_BRIGHT)
    private val detail = words(JarvisUi.BODY_FACE, Color.TEXT_DIM)
    private val labels = Array(MAX_ACTIONS) {
        words(JarvisUi.LABEL_FACE, Color.TEXT_BRIGHT, Layout.Alignment.ALIGN_CENTER, Tracking.CONTROL, caps)
    }

    // --- the zones' marks --------------------------------------------------------------
    /** The board's `.proj::before`: a dark ellipse that fades out past the zone's edge. */
    private val backingShader = RadialGradient(
        0f, 0f, 1f,
        intArrayOf(
            ColorUtils.setAlphaComponent(Color.BG, alpha(0.92f)),
            ColorUtils.setAlphaComponent(Color.BG, alpha(0.78f)),
            ColorUtils.setAlphaComponent(Color.BG, 0),
        ),
        floatArrayOf(0f, 0.65f, 1f),
        Shader.TileMode.CLAMP,
    )
    private val backingMatrix = Matrix()
    private val backing = Paint().apply { shader = backingShader }
    private val bracket = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val connector = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
    }
    private val connectorPts = FloatArray(8)
    private val buttonFill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val buttonEdge = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val rect = RectF()
    private val icon = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        color = Color.ACCENT
        strokeWidth = ICON_STROKE
    }

    /** The board's turn glyph, `M7 20V11a3 3 0 0 1 3-3h8M14 4l4 4-4 4`, in its 24-box. */
    private val turnIcon = Path().apply {
        moveTo(7f, 20f)
        lineTo(7f, 11f)
        arcTo(RectF(7f, 8f, 13f, 14f), 180f, 90f)
        lineTo(18f, 8f)
        moveTo(14f, 4f)
        lineTo(18f, 8f)
        lineTo(14f, 12f)
    }

    /** Straight on: the same stroke, pointing up the road. */
    private val aheadIcon = Path().apply {
        moveTo(12f, 20f)
        lineTo(12f, 5f)
        moveTo(7f, 10f)
        lineTo(12f, 5f)
        lineTo(17f, 10f)
    }

    // --- the card ----------------------------------------------------------------------
    private var shownCard: CarCard? = null
    private var cardStale = true
    private var actions: List<CarAction> = emptyList()
    private var buttons: List<Box> = emptyList()
    private var cardInk = Color.ACCENT

    // --- the core ----------------------------------------------------------------------
    private var inputs: ReactorInputs? = null
    private var derived = Derived(ReactorState.IDLE, false)
    // JARVIS's v2 core (T-0722). Internal so a test can read its state and clock, as [galaxy]'s.
    internal var core: Core? = null
        private set
    private val coreCanvas by lazy { CoreCanvas() }
    // The light shell, as the island's core at rest: the dock is a corner beside a whole scene.
    private val shell by lazy { CoreEngine.fibonacci(LIGHT) }
    // Internal so a test can read its clock: a seconds-for-milliseconds slip froze it (review of T-0871).
    internal var galaxy: Galaxy? = null
        private set
    private val galaxyCanvas by lazy { GalaxyCanvas() }
    private var galaxyMs = -1L

    /**
     * The surface is [width] by [height] px at [density] px per dp. The camera frames the
     * drive in [visible], the area the host's own controls leave; the zones go in [stable],
     * the part of it no control ever slides over, or [visible] when the host gives none.
     */
    fun resize(width: Int, height: Int, density: Float, visible: Box, stable: Box? = null) {
        this.density = density
        val area = if (visible.width > 0f && visible.height > 0f) visible else Box(0f, 0f, width.toFloat(), height.toFloat())
        val safe = stable?.takeIf { it.width > 0f && it.height > 0f } ?: area
        ground.resize(width.toFloat(), height.toFloat(), density, area)
        zones = CarZones.layout(safe, density)
        u = zones.unit

        distance.size(Size.CAR_TEXT_TURN * u)
        instruction.size(Type.FIELD * u)
        status.size(Type.FIELD * u)
        destination.size(Type.LABEL * u)
        eta.size(Size.CAR_TEXT_ETA * u)
        tripDetail.size(Type.HINT * u)
        speed.size(Size.CAR_TEXT_SPEED * u)
        speedUnit.size(Type.LABEL * u)
        heard.size(Type.MONO * u)
        said.size(Size.CAR_TEXT_LINE * u)
        caption.size(Type.LABEL * u)
        titleBig.size(Size.CAR_TEXT_TITLE * u)
        titleLine.size(Size.CAR_TEXT_LINE * u)
        detail.size(Type.MONO * u)
        for (l in labels) l.size(Type.HINT * u)
        // The board's `text-shadow: 0 0 12px` on every zone, and a stronger one on the subtitle.
        for (w in words) w.glow(Color.ACCENT, TEXT_GLOW * u, TEXT_GLOW_ALPHA)
        said.glow(Color.ACCENT, SAID_GLOW * u, SAID_GLOW_ALPHA)

        bracket.strokeWidth = Space.HAIRLINE * density
        bracket.color = Color.ACCENT
        bracket.setShadowLayer(BRACKET_GLOW * u, -Space.MICRO * u, -Space.MICRO * u, Color.ACCENT)
        connector.strokeWidth = CONNECTOR_WIDTH * u
        buttonEdge.strokeWidth = Space.HAIRLINE * density
        cardStale = true
    }

    /**
     * One frame: the scene from [pose] (the sky alone before the first fix), then the zones
     * from [view], at [timeMs] on the drive's clock and the voice at [level] (0..1).
     */
    fun draw(canvas: Canvas, view: DriveView, pose: DriveView.Pose?, timeMs: Long, level: Float = 0f) {
        ground.draw(canvas, pose, timeMs)
        card(canvas, view.card)
        turn(canvas, view)
        trip(canvas, view)
        subtitle(canvas, view)
        core(canvas, view.reactor, timeMs, level)
    }

    /** The action whose drawn button is under ([x], [y]) in the last frame, if any. */
    fun actionAt(x: Float, y: Float): CarAction? {
        for (i in actions.indices) {
            val b = buttons[i]
            if (x >= b.left && x <= b.right && y >= b.top && y <= b.bottom) return actions[i]
        }
        return null
    }

    // --- zones ---------------------------------------------------------------------------

    private fun turn(canvas: Canvas, view: DriveView) {
        val z = zones.turn
        val t = view.turn
        if (t == null && view.status.isEmpty()) return
        val padX = Space.SCREEN * u
        val pad = Space.SECTION * u
        val left = z.left + padX
        val top = z.top + pad
        if (t == null) {
            status.fit(view.status, z.right - padX - left, 2)
            panel(canvas, z.left, z.top, left + status.widest + padX, top + status.height + pad, Color.ACCENT)
            status.draw(canvas, left, top)
            return
        }
        val iconSize = Size.CAR_TURN_ICON * u
        val x = left + iconSize + Size.INSET * u
        val w = z.right - padX - x
        distance.fit(t.distance, w, 1)
        instruction.fit(t.instruction, w, 1)
        val h = distance.height + Space.TIGHT * u + instruction.height
        val textW = max(distance.widest, instruction.widest)
        panel(canvas, z.left, z.top, x + textW + padX, top + max(h, iconSize) + pad, Color.ACCENT)
        val iconTop = top + (max(h, iconSize) - iconSize) / 2
        glyph(canvas, left, iconTop, iconSize, t.sign)
        var y = top + (max(h, iconSize) - h) / 2
        distance.draw(canvas, x, y)
        y += distance.height + Space.TIGHT * u
        instruction.draw(canvas, x, y)
    }

    private fun glyph(canvas: Canvas, left: Float, top: Float, size: Float, sign: Int) {
        val s = size / ICON_BOX
        canvas.save()
        canvas.translate(left, top)
        canvas.scale(s, s)
        if (sign < 0) canvas.scale(-1f, 1f, ICON_BOX / 2, 0f)
        icon.setShadowLayer(ICON_GLOW * u / s, 0f, 0f, Color.ACCENT)
        canvas.drawPath(if (sign == 0) aheadIcon else turnIcon, icon)
        canvas.restore()
    }

    private fun trip(canvas: Canvas, view: DriveView) {
        val t = view.trip
        val mph = view.speed
        if (t == null && mph == null) return
        val z = zones.trip
        val padX = Space.SCREEN * u
        val pad = Space.SECTION * u
        val w = z.width - 2 * padX
        val xr = z.right - padX
        destination.fit(t?.destination.orEmpty(), w, 1)
        eta.fit(t?.eta.orEmpty(), w, 1)
        tripDetail.fit(t?.detail.orEmpty(), w, 1)
        speed.fit(mph.orEmpty(), w, 1)
        speedUnit.fit(view.speedUnit, w, 1)
        val gap = Space.SNUG * u
        val speedW = speed.widest + if (speedUnit.widest > 0f) gap + speedUnit.widest else 0f
        val textW = max(max(destination.widest, eta.widest), max(tripDetail.widest, speedW))
        val speedTop = z.top + pad + destination.height + eta.height + tripDetail.height + Space.ROW * u
        val bottom = speedTop + speed.height + pad
        panel(canvas, xr - textW - padX, z.top, z.right, bottom, Color.ACCENT)
        var y = z.top + pad
        destination.draw(canvas, xr - w, y)
        y += destination.height
        eta.draw(canvas, xr - w, y)
        y += eta.height
        tripDetail.draw(canvas, xr - w, y)
        if (speed.height <= 0f) return
        // The number and its unit share a baseline, the unit at the right edge.
        val baseline = speedTop + speed.baseline
        val unitX = xr - speedUnit.widest
        speedUnit.draw(canvas, unitX, baseline - speedUnit.baseline)
        speed.draw(canvas, unitX - (if (speedUnit.widest > 0f) gap else 0f) - speed.widest, speedTop)
    }

    private fun subtitle(canvas: Canvas, view: DriveView) {
        if (view.heard.isEmpty() && view.said.isEmpty()) return
        val z = zones.subtitle
        val padX = Space.SCREEN * u
        val pad = Space.SECTION * u
        val w = z.width - 2 * padX
        heard.fit(view.heard, w, 1)
        said.fit(view.said, w, Int.MAX_VALUE)
        val saidH = said.tailHeight(MAX_SAID_LINES)
        val h = saidH + if (heard.height > 0f) heard.height + Space.TIGHT * u else 0f
        val bottom = z.bottom - pad
        val top = bottom - h
        panel(canvas, z.left, top - pad, z.right, z.bottom, Color.ACCENT)
        heard.draw(canvas, z.left + padX, top)
        said.drawTail(canvas, z.left + padX, bottom, MAX_SAID_LINES)
    }

    private fun card(canvas: Canvas, c: CarCard?) {
        if (cardStale || c != shownCard) {
            cardStale = false
            shownCard = c
            actions = c?.actions?.take(MAX_ACTIONS).orEmpty()
            buttons = zones.buttons(actions.size)
            // An approval is the warning colour: something is about to leave on the driver's word.
            cardInk = if (c?.kind == CarCard.Kind.APPROVAL) Color.WARN else Color.ACCENT
            caption.paint.color = if (c?.kind == CarCard.Kind.APPROVAL) Color.WARN else Color.TEXT_DIM
            for (w in listOf(caption, titleBig, titleLine, detail)) w.glow(cardInk, TEXT_GLOW * u, TEXT_GLOW_ALPHA)
        }
        if (c == null) return
        val z = zones.card
        val padX = Space.SCREEN * u
        val pad = Space.SECTION * u
        val inner = z.width - 2 * padX
        val textBottom = if (buttons.isEmpty()) z.bottom - pad else buttons[0].top - Space.SECTION * u
        val title = if (c.kind in BIG_TITLE) titleBig else titleLine
        caption.fit(c.caption, inner, 1)
        title.fit(c.title, inner, if (c.detail.isEmpty() || actions.isEmpty()) 2 else 1)
        detail.fit(c.detail, inner, 2)
        val h = caption.height + Space.TIGHT * u + title.height +
            if (detail.height > 0f) Space.TIGHT * u + detail.height else 0f
        val textTop = textBottom - h

        val core = zones.core
        if (z.right <= core.left && z.bottom > core.top) {
            // The board's line from the core to the card it raised.
            val p = connectorPts
            p[0] = core.left + CONNECTOR_FROM_X * u; p[1] = core.top + CONNECTOR_FROM_Y * u
            p[2] = core.left + CONNECTOR_BEND_X * u; p[3] = core.top + CONNECTOR_BEND_Y * u
            p[4] = p[2]; p[5] = p[3]
            p[6] = z.right + CONNECTOR_TO_X * u; p[7] = p[3]
            connector.color = cardInk
            connector.setShadowLayer(CONNECTOR_GLOW * u, 0f, 0f, cardInk)
            canvas.drawLines(p, connector)
        }
        panel(canvas, z.left, textTop - pad, z.right, z.bottom, cardInk)
        var y = textTop
        caption.draw(canvas, z.left + padX, y)
        y += caption.height + Space.TIGHT * u
        title.draw(canvas, z.left + padX, y)
        y += title.height + Space.TIGHT * u
        detail.draw(canvas, z.left + padX, y)

        val r = Radius.SM * density
        for (i in actions.indices) {
            val b = buttons[i]
            val label = labels[i]
            rect.set(b.left, b.top, b.right, b.bottom)
            if (actions[i].primary) {
                buttonFill.color = Color.ACCENT
                canvas.drawRoundRect(rect, r, r, buttonFill)
                label.paint.color = Color.ACCENT_INK
                label.paint.clearShadowLayer()
            } else {
                buttonFill.color = ColorUtils.setAlphaComponent(Color.ACCENT, alpha(BUTTON_FILL))
                canvas.drawRoundRect(rect, r, r, buttonFill)
                val half = buttonEdge.strokeWidth / 2
                rect.inset(half, half)
                buttonEdge.color = ColorUtils.setAlphaComponent(Color.ACCENT, alpha(BUTTON_EDGE))
                canvas.drawRoundRect(rect, r, r, buttonEdge)
                label.paint.color = Color.TEXT_BRIGHT
                label.paint.setShadowLayer(BUTTON_GLOW * u, 0f, 0f, ColorUtils.setAlphaComponent(Color.ACCENT, alpha(0.6f)))
            }
            label.fit(actions[i].label, b.width - 2 * Space.SNUG * u, 1)
            label.draw(canvas, b.left + Space.SNUG * u, (b.top + b.bottom - label.height) / 2)
        }
    }

    private fun core(canvas: Canvas, reactor: ReactorInputs, timeMs: Long, level: Float) {
        if (reactor != inputs) {
            inputs = reactor
            derived = derive(reactor)
        }
        val z = zones.core
        if (ActiveTheme.theme.renderer == GALAXY) {
            galaxy(canvas, z, timeMs, level)
            return
        }
        // The v2 core (T-0722), the phone's and the console's. The engine is told the state each
        // frame at the drive's clock, so a change plays its entry (the kick, the shock ring).
        val target = coreStateFor(derived.state.key)
        val now = timeMs.toDouble()
        // A clock that went backwards (a new drive) is a fresh core: the old one's kick would
        // hold the glow at full until the clock caught up with it.
        val c = core?.takeIf { it.t <= now } ?: CoreEngine.createCore(target, now).also { core = it }
        CoreEngine.setState(c, target, now)
        CoreEngine.step(c, now, Inputs(level = level.coerceIn(0f, 1f).toDouble()))
        canvas.save()
        canvas.translate(z.left, z.top)
        coreCanvas.draw(canvas, c, shell, z.width, z.height, density)
        canvas.restore()
    }

    /**
     * AURA's galaxy in the core's corner (T-0871), at the pack's dock budget, stepped by the
     * drive clock's delta. Made on the first galaxy frame, so JARVIS's drive never holds its stars,
     * and made again when the worn pack is no longer the one it was built from.
     */
    private fun galaxy(canvas: Canvas, z: Box, timeMs: Long, level: Float) {
        val pack = GalaxyModel.worn()
        val g = galaxy?.takeIf { it.pack === pack }
            ?: GalaxyModel.create(GalaxyQuality.MINI, coreStateFor(derived.state.key), pack = pack).also { galaxy = it }
        GalaxyModel.setState(g, coreStateFor(derived.state.key))
        // A clock that went backwards (a new drive) is a fresh start, not a step.
        // Milliseconds, as GalaxyModel.step takes them (V2Core passes `now - last`).
        val dt = if (galaxyMs in 0..timeMs) (timeMs - galaxyMs).toDouble() else 0.0
        galaxyMs = timeMs
        GalaxyModel.step(g, dt, Inputs(level = level.coerceIn(0f, 1f).toDouble()))
        canvas.save()
        canvas.translate(z.left, z.top)
        galaxyCanvas.draw(canvas, g, z.width, z.height)
        canvas.restore()
    }

    /** The board's `.proj`: the dark backing, then the lit corner bracket at the top left. */
    private fun panel(canvas: Canvas, l: Float, t: Float, r: Float, b: Float, ink: Int) {
        val bleed = Size.GUTTER * u
        backingMatrix.setScale((r - l) / 2 + bleed, (b - t) / 2 + bleed)
        backingMatrix.postTranslate((l + r) / 2, (t + b) / 2)
        backingShader.setLocalMatrix(backingMatrix)
        canvas.drawRect(l - bleed, t - bleed, r + bleed, b + bleed, backing)
        val arm = Space.SECTION * u
        bracket.color = ink
        canvas.drawLine(l, t, l + arm, t, bracket)
        canvas.drawLine(l, t, l, t + arm, bracket)
    }

    /**
     * One piece of text, laid out again only when its words, width or line cap change (or
     * the surface does, through [size]): a StaticLayout per frame would be the frame's cost.
     */
    private class Words(
        val paint: TextPaint,
        private val align: Layout.Alignment,
        private val shape: ((String) -> String)?,
    ) {
        private var source: String? = null
        private var width = -1
        private var max = -1
        private var layout: StaticLayout? = null
        var widest = 0f; private set
        val height: Float get() = layout?.height?.toFloat() ?: 0f
        val baseline: Float get() = layout?.getLineBaseline(0)?.toFloat() ?: 0f

        fun size(px: Float) {
            paint.textSize = px
            source = null
        }

        fun glow(color: Int, radius: Float, alpha: Float) {
            paint.setShadowLayer(radius, 0f, 0f, ColorUtils.setAlphaComponent(color, alpha(alpha)))
        }

        fun fit(text: String, width: Float, maxLines: Int) {
            val w = width.toInt()
            if (text == source && w == this.width && maxLines == max) return
            source = text
            this.width = w
            max = maxLines
            val shown = shape?.invoke(text) ?: text
            val l = if (shown.isEmpty() || w <= 0) null else {
                StaticLayout.Builder.obtain(shown, 0, shown.length, paint, w)
                    .setAlignment(align)
                    .setIncludePad(false)
                    .setMaxLines(maxLines)
                    .setEllipsize(TextUtils.TruncateAt.END)
                    .build()
            }
            layout = l
            widest = 0f
            if (l != null) for (i in 0 until l.lineCount) widest = max(widest, l.getLineWidth(i))
        }

        fun draw(canvas: Canvas, x: Float, y: Float) {
            val l = layout ?: return
            canvas.save()
            canvas.translate(x, y)
            l.draw(canvas)
            canvas.restore()
        }

        /** The height of the last [lines] lines: the latest of what is being said. */
        fun tailHeight(lines: Int): Float {
            val l = layout ?: return 0f
            return l.height - l.getLineTop((l.lineCount - lines).coerceAtLeast(0)).toFloat()
        }

        /** The last [lines] lines, their bottom at [bottom]; the older ones are cut off above. */
        fun drawTail(canvas: Canvas, x: Float, bottom: Float, lines: Int) {
            val l = layout ?: return
            val h = tailHeight(lines)
            canvas.save()
            canvas.clipRect(x - l.width, bottom - h, x + 2f * l.width, bottom + l.height)
            canvas.translate(x, bottom - l.height)
            l.draw(canvas)
            canvas.restore()
        }
    }

    companion object {
        /** The strip holds four actions and Talk is one of them. */
        const val MAX_ACTIONS = 3

        /** The pack's `core.renderer` that puts AURA's galaxy in the corner (T-0871). */
        private const val GALAXY = "galaxy"

        /** What is being said, at most this many lines, the newest at the bottom. */
        private const val MAX_SAID_LINES = 3

        /** Cards whose title is a name or a time, set large on one line. */
        private val BIG_TITLE = EnumSet.of(CarCard.Kind.CALL, CarCard.Kind.CALENDAR, CarCard.Kind.MUSIC)

        // The board's geometry and light, in its dp. Not tokens: they are this drawing's
        // shape (the glyph's own 24-box, the connector's path), not the app's sizes.
        private const val ICON_BOX = 24f
        private const val ICON_STROKE = 2f
        private const val ICON_GLOW = 6f
        private const val TEXT_GLOW = 12f
        private const val TEXT_GLOW_ALPHA = 0.5f
        private const val SAID_GLOW = 14f
        private const val SAID_GLOW_ALPHA = 0.55f
        private const val BRACKET_GLOW = 4f
        private const val BUTTON_GLOW = 8f
        private const val BUTTON_FILL = 0.08f
        private const val BUTTON_EDGE = 0.5f
        private const val CONNECTOR_WIDTH = 1.4f
        private const val CONNECTOR_GLOW = 4f
        private const val CONNECTOR_FROM_X = 38f
        private const val CONNECTOR_FROM_Y = 104f
        private const val CONNECTOR_BEND_X = -2f
        private const val CONNECTOR_BEND_Y = 54f
        private const val CONNECTOR_TO_X = 6f

        private fun alpha(a: Float) = (a.coerceIn(0f, 1f) * 255f + 0.5f).toInt()
    }
}
