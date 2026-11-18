package ai.aura.app.assist.island

import ai.aura.app.assist.island.Island.State
import ai.aura.app.ui.JarvisUi
import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.ui.theme.JarvisTokens
import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Outline
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.VelocityTracker
import android.view.View
import android.view.ViewConfiguration
import android.view.ViewGroup
import android.view.ViewOutlineProvider
import android.view.animation.AnimationUtils
import android.view.animation.DecelerateInterpolator
import android.widget.FrameLayout
import kotlin.math.abs
import kotlin.math.min

/**
 * The island (T-0695): one opaque shape at the top of the screen whose bounds move on the
 * glass spring between a pill, an activity's pill and a card ([Island.State]), with one core
 * moved and scaled between the pill's slot and the card's, so it is the same Jarvis growing
 * rather than one box swapped for another. The popup it replaces was a scrim with no edge across
 * the lower half, and the launcher read through its words (the owner, 2026-11-09).
 *
 * The overlay fills what each state shows ([pill], [activity], [card]) and gives the island its
 * core and the two slots it sits in; the island lays them out, draws the backing and its edge,
 * clips them to its shape, and turns drags into [Island.Gesture]s.
 *
 * The window is sized once per change, never per frame ([Island.frame]): while the shape moves
 * this view measures the larger of where it was and where it goes, and the target once it has
 * settled. Does not cover: the window's flags, dim and blur (the overlay sets those per state),
 * or the content's own animation.
 */
class IslandView(context: Context) : FrameLayout(context) {

    fun interface Listener {
        fun onGesture(gesture: Island.Gesture)
    }

    var listener: Listener? = null

    /** Once the shape has come to rest, with the state it rests in (HIDDEN: the window can go). */
    var onSettled: ((State) -> Unit)? = null

    /** What each state shows; each is laid out at its state's size, centred at the top. */
    val pill = FrameLayout(context)
    val activity = FrameLayout(context)
    val card = FrameLayout(context)

    var state: State = State.HIDDEN
        private set

    /**
     * The reactor that talks at the bottom (T-0912): the core alone in a lit orb whose soft glow
     * fades out round it ([IslandOrb], T-0968), with no box and no words (a tap opens them). The owner,
     * 2026-11-09: "just be the core, not a box around it, with a slight smooth blur going off of
     * it, but not the entire screen". Does not blur what is behind the glow: a window put up by
     * a service blurs the whole screen behind it or nothing.
     */
    private var bare = false
    internal val bareForTest get() = bare
    internal val tickingForTest get() = ticking

    private var core: View? = null
    private var pillSlot: View? = null
    private var cardSlot: View? = null

    private fun dp(v: Int): Int = JarvisUi.dp(context, v)
    /** Room round the shape for a pull to stretch into and the spring's overshoot to land in. */
    private val margin = dp(JarvisUi.Space.GAP)

    // Not `width`/`height`: those are View's own.
    private val wide = Island.Spring.glass(rest = HALF_PX)
    private val tall = Island.Spring.glass(rest = HALF_PX)
    private val lift = Island.Spring.snap(rest = HALF_PX)
    /** 0 in the pill's slot, 1 in the card's: the core's place and size between them. */
    private val grown = Island.Spring.glass(rest = REST_FRACTION)
    private val springs = listOf(wide, tall, lift, grown)

    /** The window's frame (the shape's room, without the margin), held while a morph runs. */
    private var frameW = 0
    private var frameH = 0
    private var morphing = false
    private var ticking = false
    private var lastFrameMs = 0L

    // The worn pack's, read once per overlay (T-0885: AURA worn, the island was JARVIS's cyan).
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = ActiveTheme.tint(JarvisTokens.Material.Floating.FILL, "bg.panel", "bg.deep") }
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = dp(JarvisUi.Space.HAIRLINE).toFloat()
        color = ActiveTheme.tint(JarvisTokens.Material.Floating.EDGE, "accent")
    }
    private val shape = RectF()
    // Under a starfield pack (AURA) her space is the card's ground instead of the flat fill (T-0898).
    private val space = IslandSpace.of(ActiveTheme.theme, resources.displayMetrics.density)
    // The core alone as a lit orb in the pack's light, dark enough to read on any app (T-0968).
    private val orb = IslandOrb(space)

    init {
        setWillNotDraw(false)
        // A new state's description is announced as it lands (the approval's ask especially).
        accessibilityLiveRegion = ACCESSIBILITY_LIVE_REGION_POLITE
        for (layer in layers()) {
            layer.alpha = 0f
            layer.visibility = View.INVISIBLE
            addView(layer, LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT))
        }
        // Clipped to the shape by its outline: antialiased, and cheap enough to move every frame.
        outlineProvider = object : ViewOutlineProvider() {
            override fun getOutline(view: View, outline: Outline) {
                val r = Rect()
                shape.roundOut(r)
                outline.setRoundRect(r, radius())
            }
        }
        clipToOutline = true
    }

    private fun layers() = listOf(card, pill, activity)

    /**
     * The one core, shared by the pill and the card, and the two empty views in each that say
     * where it sits. It is laid out at the card's size and scaled into the pill's slot.
     */
    fun host(core: View, pillSlot: View, cardSlot: View) {
        this.core?.let(::removeView)
        this.core = core
        this.pillSlot = pillSlot
        this.cardSlot = cardSlot
        val size = dp(JarvisTokens.Material.Floating.CORE_CARD_DP)
        addView(core, LayoutParams(size, size))
    }

    /**
     * The room the pill and the activity make for the camera's hole when the island sits on it
     * (T-0968), px: their width grows by it and their rows keep it empty. 0 elsewhere.
     */
    var notch = 0

    /**
     * Move to [next], or re-measure the one it is in (the card's words changed); [bare]: as the
     * core alone ([Island.bare]). With the system's animations off (reduce motion, the goldens)
     * everything lands at once. Not covered: a [notch] changed while shown takes the next measure.
     */
    fun show(next: State, bare: Boolean = false) {
        // Leaving, it goes as it was: the core alone that shrank with a card's backing drawn
        // round it was the flash as it went (the owner, 2026-11-10, T-0968).
        val nowBare = if (next == State.HIDDEN) this.bare else bare && next == State.CARD
        if (next != state || nowBare != this.bare) {
            val leaving = layerFor(state)
            state = next
            // Set once per state; the live region makes a screen reader say it.
            contentDescription = next.said(ActiveTheme.spokenName).ifEmpty { null }
            this.bare = nowBare
            crossfade(leaving, layerFor(next))
        }
        grown.target = if (next == State.CARD || next == State.APPROVAL) 1f else 0f
        requestLayout()
    }

    private fun animates() = ValueAnimator.areAnimatorsEnabled()

    // --- layout -----------------------------------------------------------------------------

    private fun layerFor(s: State): ViewGroup? = when (s) {
        State.PILL -> pill
        State.ACTIVITY -> activity
        State.CARD -> if (bare) null else card
        State.APPROVAL -> card
        // The resting island is its own window (RestIsland, T-0890): the conversation's never rests.
        State.HIDDEN, State.REST -> null
    }

    private fun available() = context.resources.displayMetrics.widthPixels - 2 * margin

    private fun cardWidth() = min(available(), dp(JarvisTokens.Material.Floating.CARD_WIDTH_DP))
    internal fun pillWidth() = min(available(), dp(JarvisTokens.Material.Floating.PILL_WIDTH_DP) + notch)
    internal fun activityWidth() = min(available(), dp(JarvisTokens.Material.Floating.ACTIVITY_WIDTH_DP) + notch)

    /** The shape's size for [s] in px, the card's height from what it shows. */
    private fun targetFor(s: State): Pair<Int, Int> {
        val pillH = dp(JarvisTokens.Material.Floating.PILL_HEIGHT_DP)
        return when (s) {
            State.HIDDEN, State.REST -> 0 to 0
            State.PILL -> pillWidth() to pillH
            State.ACTIVITY -> activityWidth() to pillH
            State.CARD, State.APPROVAL ->
                if (bare) dp(JarvisTokens.Material.Floating.GLOW_DP).let { it to it } else cardWidth() to card.measuredHeight
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val pillH = MeasureSpec.makeMeasureSpec(dp(JarvisTokens.Material.Floating.PILL_HEIGHT_DP), MeasureSpec.EXACTLY)
        val tallest = (context.resources.displayMetrics.heightPixels * MAX_HEIGHT_SHARE).toInt()
        card.measure(
            MeasureSpec.makeMeasureSpec(cardWidth(), MeasureSpec.EXACTLY),
            MeasureSpec.makeMeasureSpec(tallest, MeasureSpec.AT_MOST),
        )
        pill.measure(MeasureSpec.makeMeasureSpec(pillWidth(), MeasureSpec.EXACTLY), pillH)
        activity.measure(MeasureSpec.makeMeasureSpec(activityWidth(), MeasureSpec.EXACTLY), pillH)
        core?.let {
            val size = MeasureSpec.makeMeasureSpec(dp(JarvisTokens.Material.Floating.CORE_CARD_DP), MeasureSpec.EXACTLY)
            it.measure(size, size)
        }
        val (w, h) = targetFor(state)
        wide.target = w.toFloat()
        tall.target = h.toFloat()
        if (!animates()) {
            springs.forEach { it.snap(it.target) }
            morphing = false
        } else if (springs.any { it.moving }) {
            // The frame grows now if the shape will; the tick that settles it shrinks it.
            morphing = true
            post(::start)
        }
        val (fw, fh) = if (morphing) Island.frame(frameW to frameH, w to h) else w to h
        frameW = fw
        frameH = fh
        setMeasuredDimension(frameW + 2 * margin, frameH + margin)
    }

    override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {
        val mid = (r - l) / 2
        for (layer in layers()) {
            val left = mid - layer.measuredWidth / 2
            layer.layout(left, 0, left + layer.measuredWidth, layer.measuredHeight)
        }
        core?.let { it.layout(0, 0, it.measuredWidth, it.measuredHeight) }
        apply()
        if (!morphing && !ticking) onSettled?.invoke(state)
    }

    /** Where [slot]'s centre is in this view, or null before it is laid out. */
    private fun centre(slot: View?): Pair<Float, Float>? {
        if (slot == null || slot.width == 0) return null
        val at = Rect()
        slot.getDrawingRect(at)
        offsetDescendantRectToMyCoords(slot, at)
        return at.exactCenterX() to at.exactCenterY()
    }

    /** The springs' values onto the shape, its outline, the layers and the core. */
    private fun apply() {
        val left = (measuredWidth - wide.value) / 2f
        // The core alone grows and shrinks about its frame's middle, where it stands (T-0968); a
        // pill or card hangs from the frame's top.
        val top = lift.value + if (bare) (frameH - tall.value) / 2f else 0f
        shape.set(left, top, left + wide.value, top + tall.value)
        invalidateOutline()
        invalidate()
        for (layer in layers()) layer.translationY = lift.value
        val ball = core ?: return
        // Alone, it never travels: it came in sliding from the pill's slot to the middle.
        val to = if (bare) measuredWidth / 2f to frameH / 2f else centre(cardSlot)
        val from = if (bare) to else centre(pillSlot)
        if (ball.measuredWidth == 0 || (from == null && to == null)) {
            ball.visibility = View.INVISIBLE
            return
        }
        val g = grown.value
        val a = from ?: to!!
        val b = to ?: from!!
        val small = dp(JarvisTokens.Material.Floating.CORE_PILL_DP).toFloat() / ball.measuredWidth
        val scale = if (bare) g * orbCore(ball) else small + (1f - small) * g
        ball.scaleX = scale
        ball.scaleY = scale
        ball.translationX = a.first + (b.first - a.first) * g - ball.measuredWidth / 2f
        ball.translationY = a.second + (b.second - a.second) * g - ball.measuredHeight / 2f + lift.value
        // An activity's pill has its own glyph, not the core: it fades as the shape leaves the card.
        ball.alpha = if (state == State.ACTIVITY || state == State.HIDDEN) g else 1f
        ball.visibility = View.VISIBLE
    }

    /** The scale at which [ball] fills its share of the orb's body. */
    private fun orbCore(ball: View) = dp(JarvisTokens.Material.Floating.ORB_DP) * ORB_CORE_SHARE / ball.measuredWidth

    private fun radius(): Float = min(dp(JarvisTokens.Material.Floating.RADIUS_DP).toFloat(), tall.value / 2f)

    override fun dispatchDraw(canvas: Canvas) {
        if (shape.isEmpty) return
        if (bare) {
            val g = min(shape.width(), shape.height()) / 2f
            if (g > 0f) orb.under(canvas, shape.centerX(), shape.centerY(), g)
            super.dispatchDraw(canvas)
            if (g > 0f) orb.over(canvas, shape.centerX(), shape.centerY(), g)
            return
        }
        // The backing first: the outline clips everything else to the same shape.
        val r = radius()
        if (space != null) space.draw(canvas, shape, r) else canvas.drawRoundRect(shape, r, r, fill)
        super.dispatchDraw(canvas)
        val inset = edge.strokeWidth / 2f
        canvas.drawRoundRect(
            shape.left + inset, shape.top + inset, shape.right - inset, shape.bottom - inset,
            r - inset, r - inset, edge,
        )
    }

    // --- motion -----------------------------------------------------------------------------

    /** The old state's words leave quickly; the new state's arrive as the shape opens round them. */
    private fun crossfade(leaving: ViewGroup?, coming: ViewGroup?) {
        if (leaving === coming) return
        for (layer in layers()) layer.animate().cancel()
        if (!animates()) {
            for (layer in layers()) {
                val on = layer === coming
                layer.alpha = if (on) 1f else 0f
                layer.visibility = if (on) View.VISIBLE else View.INVISIBLE
            }
            return
        }
        leaving?.animate()?.alpha(0f)?.setDuration(JarvisTokens.Motion.Dur.FAST.toLong())
            ?.withEndAction { if (layerFor(state) !== leaving) leaving.visibility = View.INVISIBLE }?.start()
        coming?.let {
            it.visibility = View.VISIBLE
            it.animate().alpha(1f).setStartDelay(JarvisTokens.Motion.Dur.INSTANT.toLong())
                .setDuration(JarvisTokens.Motion.Dur.BASE.toLong())
                .setInterpolator(DecelerateInterpolator()).start()
        }
    }

    private fun start() {
        if (ticking) return
        ticking = true
        lastFrameMs = AnimationUtils.currentAnimationTimeMillis()
        postOnAnimation(tick)
    }

    private val tick = object : Runnable {
        override fun run() {
            val now = AnimationUtils.currentAnimationTimeMillis()
            val dt = (now - lastFrameMs).coerceIn(1L, MAX_FRAME_MS) / MS_PER_S.toFloat()
            lastFrameMs = now
            var moving = false
            for (s in springs) moving = s.step(dt) or moving
            apply()
            if (moving) {
                postOnAnimation(this)
            } else {
                ticking = false
                morphing = false
                // Settled: the window shrinks to the shape it ended on (and says so from onLayout).
                requestLayout()
            }
        }
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(tick)
        ticking = false
        morphing = false
        super.onDetachedFromWindow()
    }

    // --- gestures ---------------------------------------------------------------------------

    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private val fling = ViewConfiguration.get(context).scaledMinimumFlingVelocity * FLING_FACTOR
    private var downY = 0f
    private var downX = 0f
    private var dragging = false
    private var tracker: VelocityTracker? = null

    private fun down(e: MotionEvent) {
        downX = e.rawX
        downY = e.rawY
        dragging = false
        tracker?.recycle()
        tracker = VelocityTracker.obtain().also { it.addMovement(e) }
    }

    /** A vertical drag is the island's, unless what is under the finger scrolls that way (the reply). */
    override fun onInterceptTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> down(e)
            MotionEvent.ACTION_MOVE -> {
                tracker?.addMovement(e)
                val dy = e.rawY - downY
                if (!dragging && abs(dy) > slop && abs(dy) > abs(e.rawX - downX) && !scrolls(this, e.x, e.y, dy)) {
                    dragging = true
                }
            }
        }
        return dragging
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (state == State.HIDDEN) return false
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                // The margin round the shape is the window's but not the island's.
                if (!shape.contains(e.x, e.y)) return false
                down(e)
            }
            MotionEvent.ACTION_MOVE -> {
                tracker?.addMovement(e)
                val dy = e.rawY - downY
                if (abs(dy) > slop) dragging = true
                if (dragging) {
                    // Up follows the finger; down stretches into the margin and no further.
                    lift.snap(if (dy < 0) dy else min(dy * PULL_GIVE, margin.toFloat()))
                    apply()
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                tracker?.addMovement(e)
                tracker?.computeCurrentVelocity(MS_PER_S)
                val vy = tracker?.yVelocity ?: 0f
                tracker?.recycle()
                tracker = null
                val gesture = if (e.actionMasked == MotionEvent.ACTION_CANCEL) null
                else gestureFor(dragging, e.rawY - downY, vy)
                dragging = false
                // Back to its place, the fling's speed carried into the spring.
                lift.target = 0f
                lift.velocity = vy
                if (animates()) start() else { lift.snap(0f); apply() }
                if (gesture != null && Island.act(state, gesture) != Island.Action.NONE) {
                    performHapticFeedback(
                        if (gesture == Island.Gesture.FLICK_UP) HapticFeedbackConstants.GESTURE_END
                        else HapticFeedbackConstants.CONFIRM,
                    )
                    listener?.onGesture(gesture)
                }
            }
        }
        return true
    }

    /** A drag's end as a gesture: far or fast enough up is a flick, down a pull; no drag a tap. */
    private fun gestureFor(dragged: Boolean, dy: Float, vy: Float): Island.Gesture? = when {
        !dragged -> Island.Gesture.TAP
        dy < -dp(FLICK_DP) || vy < -fling -> Island.Gesture.FLICK_UP
        dy > dp(PULL_DP) || vy > fling -> Island.Gesture.PULL_DOWN
        else -> null
    }

    /** Whether a view under (x, y) can scroll in the drag's direction, so the drag is its. */
    private fun scrolls(group: ViewGroup, x: Float, y: Float, dy: Float): Boolean {
        for (i in group.childCount - 1 downTo 0) {
            val child = group.getChildAt(i)
            if (child.visibility != View.VISIBLE) continue
            val cx = x + group.scrollX - child.left - child.translationX
            val cy = y + group.scrollY - child.top - child.translationY
            if (cx < 0 || cy < 0 || cx > child.width || cy > child.height) continue
            if (child.canScrollVertically(if (dy < 0) 1 else -1)) return true
            if (child is ViewGroup && scrolls(child, cx, cy, dy)) return true
        }
        return false
    }

    private companion object {
        const val HALF_PX = 0.5f
        const val REST_FRACTION = 0.002f
        /** A frame is never counted longer than this, so a stall does not throw the spring. */
        const val MAX_FRAME_MS = 32L
        const val MS_PER_S = 1000
        /** The card is at most this share of the screen's height; the reply scrolls inside it. */
        const val MAX_HEIGHT_SHARE = 0.6f
        /** The core alone fills this share of its orb's body across. */
        const val ORB_CORE_SHARE = 0.72f
        /** How far a drag goes before it is a flick or a pull, and how much a pull gives. */
        const val FLICK_DP = 28
        const val PULL_DP = 20
        const val PULL_GIVE = 0.4f
        /** A deliberate flick, not a twitch: the platform's minimum is a crawl. */
        const val FLING_FACTOR = 8
    }
}
