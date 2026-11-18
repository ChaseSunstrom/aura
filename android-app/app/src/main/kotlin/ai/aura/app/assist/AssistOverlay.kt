package ai.aura.app.assist

import ai.aura.app.assist.island.Island
import ai.aura.app.assist.island.IslandRest
import ai.aura.app.assist.island.IslandView
import ai.aura.app.assist.island.RestIsland
import ai.aura.app.config.JarvisConfig
import ai.aura.app.assist.island.RouteActivity
import ai.aura.app.assist.island.RouteGlyph
import ai.aura.app.ui.JarvisUi
import ai.aura.app.glass.ReactorView
import ai.aura.app.reactor.ReactorInputs
import android.animation.LayoutTransition
import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.text.TextUtils
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import ai.aura.app.ui.theme.ActiveTheme
import ai.aura.app.ui.theme.JarvisTokens

/**
 * Jarvis, on screen, over whatever you were doing.
 *
 * Until this existed the wake word could only reach the user through
 * [ai.aura.app.JarvisAssistActivity] — an Activity, which means a task switch,
 * which means Android 12+ silently drops it when the app is in the background
 * and the conversation arrives as a notification the user has to tap. That is
 * why the reported symptom was "it can speak back to me, but there is no
 * overlay on my screen": the voice path worked and the visible half did not
 * exist.
 *
 * A `TYPE_APPLICATION_OVERLAY` window is the thing that actually floats. It
 * needs SYSTEM_ALERT_WINDOW ("display over other apps"), which is a Settings
 * trip the user has to make, so [canShow] is checked before every attempt and
 * the caller keeps the old Activity path as the fallback — see
 * [WakeWordService.onWakeWord].
 *
 * What it draws is the island (T-0695, [IslandView]): a card at the top while Jarvis listens and
 * answers, a pill when it is put out of the way (a flick, or a consent prompt on screen), the
 * approval compact inside the card. It was a scrim across the lower half that the app behind
 * read through (the owner, 2026-11-09: "it shows up over the entire screen instead of something
 * like a dynamic island"). Which of them is up is [Island.stateFor], from what is going on here.
 *
 * **Two deliberate limits.**
 *  * Overlay windows are not shown above the keyguard, by design and with no
 *    way around it. On a locked phone the caller uses the full-screen intent
 *    instead, which is the platform's own mechanism for that case.
 *  * The window is sized to the island, not to the screen. A full-width window
 *    with FLAG_NOT_TOUCH_MODAL still swallows every touch inside its own
 *    bounds, so a screen-sized overlay would eat taps on the app behind it
 *    across the whole display. Only the island (and a margin round it for a
 *    pull to stretch into) is in the way.
 */
class AssistOverlay(
    private val context: Context,
    /** The camera's hole, px, read as the island goes up (a seam for the tests: Robolectric has none). */
    private val hole: (Context) -> IslandRest.Box? = RestIsland::hole,
    /** The user flicked the pill away; the caller should end the conversation. */
    private val onDismiss: () -> Unit,
) {

    private var root: IslandView? = null

    /** What is going on, from which [Island.stateFor] picks the island's state. */
    private var turn = false
    /** A consent prompt is on screen: the island is its pill, out of the prompt's way. */
    private var prompt = false
    /** The user's last gesture: put out of the way, or opened. New content clears both. */
    private var collapsed = false
    private var expanded = false
    private var focus = false
    private var touchable = true
    /** The turn went to a task (tools ran), or the phone behind it was used (T-0885): it stays up for the turn. */
    private var working = false
    private var touched = false
    /** Where the island is now: a talking turn's card at the bottom, everything else at the top. */
    private var place = Island.Place.TOP
    private var flights = 0
    /** The setting that puts every state at the bottom (T-0968), read as the island goes up. */
    private var atBottom = false
    /** On the camera's hole at the top, with the resting island on (T-0968); null: under the status bar. */
    private var anchor: IslandRest.Anchor? = null

    /** The ongoing activity (a route held off the car), its pill and its row in the card. */
    private var ongoing: RouteActivity.Pill? = null
    private var activityLabel: TextView? = null
    private var activityFigure: TextView? = null
    private var activityRow: LinearLayout? = null

    /** The glass reactor in a Service-owned window; see `ReactorView.attachOwners`. */
    private var orb: ReactorView? = null
    private var caption: TextView? = null
    /** The state word beside the core in the pill. */
    private var word: TextView? = null
    private var transcript: TextView? = null
    private var response: TextView? = null
    private var responseScroll: android.widget.ScrollView? = null
    /** The user's line's label, and the reply's fade, streaming Markdown and typing dots (T-0728). */
    private var youLabel: TextView? = null
    private var reveal: ReplyReveal? = null
    private var toolActivity: ToolActivityView? = null
    private var approvalRow: LinearLayout? = null
    private var noticeRow: LinearLayout? = null
    private val noticeTimer = android.os.Handler(android.os.Looper.getMainLooper())

    val isShowing: Boolean get() = root != null

    /**
     * The attached view tree, for the instrumented test.
     *
     * A seam rather than a feature. Whether `TYPE_APPLICATION_OVERLAY` is
     * accepted, and whether what it accepts has a size, is not knowable by
     * reading code — it depends on an appop, a window type and a set of flags —
     * and this surface has now been reported broken three times. `AssistOverlayTest`
     * asks the real WindowManager, and needs a handle on what it added.
     */
    val rootForTest: ViewGroup? get() = root

    /** The island's state, for the tests. */
    val stateForTest: Island.State get() = root?.state ?: Island.State.HIDDEN

    /**
     * Put the island on screen: as the card for a conversation ([turn]), or as whatever the
     * activity set before this asks for ([setActivity]: its pill).
     *
     * @return false if it could not be shown — no permission, or the window
     *   manager refused — so the caller can fall back rather than assume a
     *   surface that is not there.
     */
    fun attach(turn: Boolean = true): Boolean {
        if (root != null) return true
        if (!canShow(context)) return false

        val windows = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
            ?: return false
        val config = JarvisConfig(context)
        atBottom = config.islandBottom
        // The turn's island takes the resting island's place on the camera (the owner, 2026-11-10:
        // "make it work with the dynamic island"), so the two are one island at one place.
        anchor = if (config.islandRest && !atBottom) {
            IslandRest.anchor(
                windows.currentWindowMetrics.bounds.width(), hole(context),
                JarvisUi.dp(context, JarvisTokens.Material.Floating.PILL_HEIGHT_DP), JarvisUi.dp(context, JarvisUi.Space.GAP),
            )
        } else null
        val view = build()
        // A Compose view in a window no Activity owns needs its own
        // lifecycle, or its first frame throws. Before addView, which is the
        // attach the owners follow.
        ReactorView.attachOwners(view)
        this.turn = turn
        ongoing?.let { fillActivity(it) }
        val state = state()
        focus = Island.SIZES.getValue(state).focus
        touchable = state != Island.State.HIDDEN
        place = Island.placeFor(state, inputs(), atBottom)
        // Before addView: its first layout is already the card growing from nothing.
        view.show(state, Island.bare(state, inputs()))
        return try {
            windows.addView(view, params(focus, touchable, place))
            root = view
            true
        } catch (t: Throwable) {
            // A refused addView is the one failure mode that must not take the
            // conversation down with it: the caller still has the notification
            // path, and a wake word that produces nothing at all is the bug
            // this whole class exists to fix.
            Log.w(TAG, "the overlay window was refused", t)
            this.turn = false
            false
        }
    }

    /**
     * Get out of the way of a prompt — by becoming the pill, not by disappearing.
     *
     * A `TYPE_APPLICATION_OVERLAY` window is drawn **above every Activity**, so
     * while this card is up it is on top of `ApprovalActivity`. `FLAG_NOT_TOUCH_MODAL`
     * passes through only the touches that land OUTSIDE the window, so a card over
     * the prompt's buttons left them on screen and unpressable. That was the report:
     * *"it still forces me to click on the tool call to approve"*.
     *
     * Hiding the window instead made Jarvis vanish while it asked, with the conversation
     * still running and no visible surface at all. So it shrinks to the pill at the top,
     * still listening, and every touch aimed at the prompt lands outside it.
     *
     * Never [detach]: that would drop the view tree and the conversation's
     * callbacks with it, and this has to be reversible — the card comes back
     * the moment the prompt is answered, with the turn still running underneath.
     */
    fun setHiddenForPrompt(hidden: Boolean) {
        if (prompt == hidden) return
        // Not `collapsed`: that is the person's own flick, and a prompt answered must not
        // reopen a turn they had put away.
        prompt = hidden
        refresh()
    }

    /** The island leaves: it shrinks to nothing, and the window goes once it has. */
    fun detach() {
        val view = root ?: return
        root = null
        turn = false
        prompt = false
        collapsed = false
        expanded = false
        // A conversation that went to work or was talked over sends only itself to the top;
        // the next one opens at the bottom again (T-0885).
        working = false
        touched = false
        // All of them, not just the orb: a late callback from a conversation
        // that is still winding down would otherwise write into views that are
        // no longer on screen and keep the whole tree alive.
        orb = null
        caption = null
        word = null
        transcript = null
        response = null
        responseScroll = null
        youLabel = null
        reveal = null
        toolActivity = null
        approvalRow = null
        activityLabel = null
        activityFigure = null
        activityRow = null
        noticeTimer.removeCallbacksAndMessages(null)
        noticeRow = null
        view.listener = null
        view.onSettled = { state -> if (state == Island.State.HIDDEN) remove(view) }
        view.show(Island.State.HIDDEN)
    }

    private fun remove(view: View) {
        val windows = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
        try {
            windows?.removeView(view)
        } catch (t: Throwable) {
            Log.w(TAG, "the overlay window was already gone", t)
        }
    }

    // --- the island's state -------------------------------------------------------

    private fun inputs() = Island.Inputs(
        turn = turn,
        ask = approvalRow?.visibility == View.VISIBLE,
        notice = noticeRow?.visibility == View.VISIBLE,
        activity = ongoing != null,
        expanded = expanded,
        collapsed = collapsed || prompt,
        working = working,
        touched = touched,
    )

    private fun state() = Island.stateFor(inputs())

    /** Put the island in the state for what is going on, and the window's focus with it. */
    private fun refresh() {
        val view = root ?: return
        val next = state()
        val wantsFocus = Island.SIZES.getValue(next).focus
        // Hidden but held (a flicked-away route): the empty window passes every touch on.
        val wantsTouch = next != Island.State.HIDDEN
        val wantsPlace = Island.placeFor(next, inputs(), atBottom)
        if (wantsPlace != place) {
            focus = wantsFocus
            touchable = wantsTouch
            place = wantsPlace
            move(view)
        } else if (wantsFocus != focus || wantsTouch != touchable) {
            focus = wantsFocus
            touchable = wantsTouch
            // Once per change, never per frame: the system fades the dim in and out itself.
            relayout(view, params(wantsFocus, wantsTouch, place))
        }
        view.show(next, Island.bare(next, inputs()))
    }

    private fun relayout(view: View, params: WindowManager.LayoutParams) {
        val windows = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
        try {
            windows?.updateViewLayout(view, params)
        } catch (t: Throwable) {
            // A window the platform has already taken away; the island is what matters.
            Log.w(TAG, "could not change the overlay's window", t)
        }
    }

    /**
     * Carry the island from the bottom to the top, or back (T-0885): the window grows to the
     * screen's height, not touchable, while the island slides inside it on the glass spring,
     * then shrinks round the island at its new place. Two relayouts a move, none per frame, as
     * the morph's frame ([Island.frame]). With animations off (reduce motion, the goldens), or
     * before the island has been laid out, it lands at once.
     */
    private fun move(view: IslandView) {
        val settled = params(focus, touchable, place)
        if (!view.isLaidOut || !android.animation.ValueAnimator.areAnimatorsEnabled()) {
            relayout(view, settled)
            return
        }
        val gap = JarvisUi.dp(context, JarvisUi.Space.SNUG).toFloat()
        // A move asked for mid-flight takes over from where the island is; the old loop stops.
        val flight = ++flights
        val was = IntArray(2).also(view::getLocationOnScreen)[1]
        // Not focused either: a dim or blur behind a screen-tall window would cover the screen.
        relayout(view, params(focus = false, touchable = false, Island.Place.TOP).apply {
            height = WindowManager.LayoutParams.MATCH_PARENT
            y = 0
        })
        view.viewTreeObserver.addOnPreDrawListener(object : android.view.ViewTreeObserver.OnPreDrawListener {
            override fun onPreDraw(): Boolean {
                view.viewTreeObserver.removeOnPreDrawListener(this)
                if (root !== view || flight != flights) return true
                // In the tall window: where the island was, then where it is going.
                val top = IntArray(2).also(view::getLocationOnScreen)[1]
                // Aimed again each frame: a pill growing back into a card on its way down (a
                // prompt answered) must land with its bottom edge, not its first height's, at rest.
                fun aim() = landing(view.height, gap) - top
                val spring = Island.Spring.glass(rest = 0.5f).apply { snap((was - top).toFloat()); target = aim() }
                view.translationY = spring.value
                var last = 0L
                view.postOnAnimation(object : Runnable {
                    override fun run() {
                        if (root !== view || flight != flights) return
                        val now = android.os.SystemClock.uptimeMillis()
                        spring.target = aim()
                        val moving = spring.step(if (last == 0L) 0f else minOf((now - last) / MS_PER_S, MAX_STEP_S))
                        last = now
                        view.translationY = spring.value
                        if (moving) {
                            view.postOnAnimation(this)
                        } else {
                            // The window moves to the island and the island to the window's
                            // origin in one traversal, so no frame shows it twice.
                            relayout(view, params(focus, touchable, place))
                            view.translationY = 0f
                        }
                    }
                })
                return true
            }
        })
    }

    /**
     * Where the island's top lands on the screen, px, at [place] with [height]: [gap] over the
     * navigation bar, on the hole ([anchor]), or [gap] under the status bar, as [params] puts it.
     */
    private fun landing(height: Int, gap: Float): Float {
        val windows = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val metrics = windows.currentWindowMetrics
        val bars = metrics.windowInsets.getInsets(android.view.WindowInsets.Type.systemBars())
        val on = anchor
        return when {
            place == Island.Place.BOTTOM -> metrics.bounds.height() - bars.bottom - height - gap
            on != null -> on.at.y.toFloat()
            else -> bars.top + gap
        }
    }

    private fun onGesture(gesture: Island.Gesture) {
        val view = root ?: return
        // The core alone (T-0912): a tap opens its words, at the top or, with the setting, in place.
        val bare = Island.bare(view.state, inputs()) && gesture == Island.Gesture.TAP
        when (if (bare) Island.Action.EXPAND else Island.act(view.state, gesture)) {
            // Each undoes the other first: a pill put away opens back to the card it was, and a
            // route's card opened from its pill closes back to the pill.
            // Never over a prompt still on screen: the card would sit on its buttons again.
            Island.Action.EXPAND -> { if (prompt) return else if (collapsed) collapsed = false else expanded = true }
            Island.Action.COLLAPSE -> if (expanded) expanded = false else collapsed = true
            Island.Action.DISMISS -> { onDismiss(); return }
            Island.Action.NONE -> return
        }
        refresh()
    }

    // --- what the conversation drives ---------------------------------------

    fun setMode(mode: Phase, label: String) {
        orb?.setPhase(mode)
        caption?.text = label
        word?.text = label
        reveal?.thinking(mode == Phase.THINKING)
    }

    /** What the conversation's HUD state says the reactor is doing; outranks the phase. */
    fun setReactor(inputs: ReactorInputs) {
        orb?.setInputs(inputs)
    }

    fun setAmplitude(level: Float) {
        orb?.setAmplitude(level)
    }

    fun setTranscript(text: String) {
        transcript?.text = text
        transcript?.visibility = if (text.isEmpty()) View.GONE else View.VISIBLE
        youLabel?.visibility = transcript?.visibility ?: View.GONE
        root?.show(state(), place == Island.Place.BOTTOM)
    }

    /**
     * Ask for approval on the island itself: what Jarvis wants to do, and the two
     * answers. The island is the approval until it is answered; a flick does not put
     * it away. [clearApproval] takes it down; answering does too.
     */
    fun showApproval(what: String, detail: String, onApprove: () -> Unit, onDeny: () -> Unit) {
        val row = approvalRow ?: return
        row.removeAllViews()
        row.addView(JarvisUi.labelText(context, "${ActiveTheme.name} asks", JarvisTokens.Color.WARN), fullWidth())
        row.addView(start(JarvisUi.responseView(context)).apply { text = what; maxLines = 2; ellipsize = TextUtils.TruncateAt.END }, fullWidth())
        if (detail.isNotBlank()) {
            row.addView(JarvisUi.hint(context, detail).apply { maxLines = 2; ellipsize = TextUtils.TruncateAt.END }, fullWidth())
        }
        val answers = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.STEP), 0, 0)
        }
        answers.addView(JarvisUi.button(context, "Deny") { clearApproval(); onDeny() })
        answers.addView(View(context), LinearLayout.LayoutParams(JarvisUi.dp(context, JarvisUi.Space.ROW), 1))
        answers.addView(JarvisUi.primary(context, "Approve") { clearApproval(); onApprove() })
        row.addView(answers, fullWidth())
        row.visibility = View.VISIBLE
        refresh()
    }

    /**
     * A notice, on the island for [NOTICE_MS]: its title and a line of it. A newer
     * one replaces it, and opens the island again if it was put out of the way.
     */
    fun showNotice(title: String, body: String) {
        val row = noticeRow ?: return
        row.removeAllViews()
        row.addView(JarvisUi.labelText(context, "Notice", ActiveTheme.argb("accent", fallback = JarvisTokens.Color.ACCENT)), fullWidth())
        row.addView(start(JarvisUi.responseView(context)).apply { text = title; maxLines = 2; ellipsize = TextUtils.TruncateAt.END }, fullWidth())
        if (body.isNotBlank() && body != title) {
            row.addView(JarvisUi.hint(context, body).apply { maxLines = 2; ellipsize = TextUtils.TruncateAt.END }, fullWidth())
        }
        row.visibility = View.VISIBLE
        collapsed = false
        refresh()
        noticeTimer.removeCallbacksAndMessages(null)
        noticeTimer.postDelayed({
            noticeRow?.apply { removeAllViews(); visibility = View.GONE }
            refresh()
        }, NOTICE_MS)
    }

    fun clearApproval() {
        approvalRow?.apply { removeAllViews(); visibility = View.GONE }
        refresh()
    }

    fun setResponse(text: String) {
        reveal?.show(text)
    }

    /**
     * The ongoing activity: a route held off the car, as a pill (where to, how far) that opens
     * into a row with its two answers, or null once it has arrived or been cancelled. A new
     * destination opens it again if it was put out of the way; a new distance does not.
     */
    fun setActivity(pill: RouteActivity.Pill?, onOpen: () -> Unit = {}, onCancel: () -> Unit = {}) {
        val fresh = pill != null && (ongoing?.lat != pill.lat || ongoing?.lon != pill.lon)
        ongoing = pill
        this.onOpen = onOpen
        this.onCancel = onCancel
        if (pill == null) expanded = false
        if (fresh && !turn) collapsed = false
        pill?.let { fillActivity(it) } ?: activityRow?.apply { visibility = View.GONE }
        refresh()
    }

    private var onOpen: () -> Unit = {}
    private var onCancel: () -> Unit = {}

    private fun fillActivity(pill: RouteActivity.Pill) {
        activityLabel?.text = pill.label
        activityFigure?.text = pill.figure.orEmpty()
        activityFigure?.visibility = if (pill.figure == null) View.GONE else View.VISIBLE
        val row = activityRow ?: return
        row.removeAllViews()
        row.addView(JarvisUi.labelText(context, "Route", ActiveTheme.argb("accent", fallback = JarvisTokens.Color.ACCENT)), fullWidth())
        row.addView(start(JarvisUi.responseView(context)).apply { text = pill.label; maxLines = 2; ellipsize = TextUtils.TruncateAt.END }, fullWidth())
        pill.figure?.let { row.addView(JarvisUi.hint(context, "$it away, in a straight line"), fullWidth()) }
        val answers = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.STEP), 0, 0)
        }
        answers.addView(JarvisUi.button(context, "End route") { onCancel() })
        answers.addView(View(context), LinearLayout.LayoutParams(JarvisUi.dp(context, JarvisUi.Space.ROW), 1))
        answers.addView(JarvisUi.primary(context, "Open maps") { onOpen() })
        row.addView(answers, fullWidth())
        row.visibility = View.VISIBLE
    }

    /**
     * What the turn is touching, under the caption.
     *
     * The overlay is the surface that is up when a wake word starts a turn from
     * a locked phone on a shelf — which is exactly when nobody can see a console
     * — so it is the surface that most needs to say "this just unlocked the
     * front door" rather than only speaking a sentence about it.
     */
    fun setTools(run: ToolRun) {
        toolActivity?.render(run)
        // A turn that goes to a task steps up out of the way, and stays up for the turn (T-0885).
        if (run.running && !working) {
            working = true
            refresh()
        }
    }

    // --- construction --------------------------------------------------------

    private fun build(): IslandView {
        val island = IslandView(context)
        island.listener = IslandView.Listener(::onGesture)
        // A touch on the phone behind the reactor at the bottom (FLAG_WATCH_OUTSIDE_TOUCH): the
        // person is using it, so the turn steps up into the island (T-0885). Reported, never
        // taken: the touch still reaches what it was aimed at.
        island.setOnTouchListener { _, e ->
            if (e.actionMasked != MotionEvent.ACTION_OUTSIDE) return@setOnTouchListener false
            if (turn && !touched) {
                touched = true
                refresh()
            }
            true
        }
        val coreCard = JarvisUi.dp(context, JarvisTokens.Material.Floating.CORE_CARD_DP)
        val corePill = JarvisUi.dp(context, JarvisTokens.Material.Floating.CORE_PILL_DP)
        val side = JarvisUi.dp(context, JarvisUi.Space.SECTION)
        val gap = JarvisUi.dp(context, JarvisUi.Space.GAP)
        // On the camera's hole (T-0968): the pills widen by the hole and a little air either side
        // and keep that middle empty, the core left of it and the words right of it; the card's
        // rows start below it.
        val on = anchor
        island.notch = on?.let { it.notch + 2 * JarvisUi.dp(context, JarvisUi.Space.SNUG) } ?: 0
        /** The empty middle of a pill [width] px wide, after its core: to the hole's right edge. */
        fun notchGap(width: Int) = View(context).takeIf { on != null }?.also {
            it.layoutParams = LinearLayout.LayoutParams(width / 2 + island.notch / 2 - gap - corePill, 0)
        }
        val wordPad = if (on != null) 0 else JarvisUi.dp(context, JarvisUi.Space.ROW)

        // The pill: the core's slot and the state word beside it.
        val pillRow = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(gap, 0, if (on != null) gap else side, 0)
        }
        val pillSlot = View(context)
        pillRow.addView(pillSlot, LinearLayout.LayoutParams(corePill, corePill))
        notchGap(island.pillWidth())?.let(pillRow::addView)
        val wordView = V2Words.label(TextView(context)).apply {
            text = "Listening"
            gravity = Gravity.START or Gravity.CENTER_VERTICAL
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
            setPadding(wordPad, 0, 0, 0)
        }
        word = wordView
        pillRow.addView(wordView, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        island.pill.addView(pillRow, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        // The activity's pill: its glyph where the core sits in the turn's, where to, how far.
        val activityPill = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(gap, 0, side, 0)
        }
        activityPill.addView(RouteGlyph(context), LinearLayout.LayoutParams(corePill, corePill))
        notchGap(island.activityWidth())?.let(activityPill::addView)
        val where = JarvisUi.hint(context, "").apply {
            setTextColor(JarvisTokens.Color.TEXT)
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
            setPadding(wordPad, 0, JarvisUi.dp(context, JarvisUi.Space.ROW), 0)
        }
        activityLabel = where
        activityPill.addView(where, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val far = JarvisUi.readout(context, "", ActiveTheme.argb("accent", fallback = JarvisTokens.Color.ACCENT))
        activityFigure = far
        activityPill.addView(far, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        island.activity.addView(activityPill, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        // The card: the core's slot with the state and what the turn is doing beside it,
        // then the two lines, an approval, a notice.
        val column = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(side, gap + (on?.clear ?: 0), side, side)
            // Rows coming and going (the transcript, the reply, an approval, a notice)
            // slide and fade inside the card while the island's spring resizes it.
            layoutTransition = LayoutTransition().apply { enableTransitionType(LayoutTransition.CHANGING) }
        }
        val head = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val cardSlot = View(context)
        head.addView(cardSlot, LinearLayout.LayoutParams(coreCard, coreCard))
        val headText = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(JarvisUi.dp(context, JarvisUi.Space.GAP), 0, 0, 0)
        }
        // The state as the console's Label (V2Words): the core beside it carries
        // the state's colour, so the word stays one quiet tone.
        val captionView = V2Words.label(TextView(context)).apply {
            text = "Listening"
            gravity = Gravity.START
        }
        caption = captionView
        headText.addView(captionView, fullWidth())
        // What Jarvis is DOING sits above what it is saying, because the doing is what a
        // person in the room needs to be able to stop.
        val tools = ToolActivityView(context).apply {
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.SNUG), 0, 0)
        }
        toolActivity = tools
        headText.addView(tools, fullWidth())
        head.addView(headText, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        column.addView(head, fullWidth())

        // The user's line and the reply as the console's Subtitles (V2Words).
        val transcriptView = V2Words.subtitle(JarvisUi.transcriptView(context), jarvis = false).apply {
            maxLines = 3
            ellipsize = TextUtils.TruncateAt.END
            visibility = View.GONE
        }
        // All of it, scrolling, not four lines and an ellipsis: a reply longer
        // than that was cut off mid-sentence with no way to read the rest (the
        // owner, 2026-10-08: the popup "looks weird and cut off").
        val responseView = V2Words.subtitle(JarvisUi.responseView(context), jarvis = true).apply {
            visibility = View.GONE
        }
        // Each line under who said it, as the console's conversation turns (T-0728).
        val you = V2Words.who(TextView(context), jarvis = false).apply {
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.ROW), 0, JarvisUi.dp(context, JarvisUi.Space.TIGHT))
        }
        val jarvis = V2Words.who(TextView(context), jarvis = true).apply {
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.GAP), 0, 0)
        }
        transcript = transcriptView
        response = responseView
        youLabel = you
        // Follow the reply as it streams, the way it is being spoken, its last word included.
        reveal = ReplyReveal(responseView, jarvis, drawn = {
            responseScroll?.let { it.post { it.fullScroll(View.FOCUS_DOWN) } }
            root?.show(state(), place == Island.Place.BOTTOM)
        })
        column.addView(you, fullWidth())
        column.addView(transcriptView, fullWidth())
        column.addView(jarvis, fullWidth())
        val scroll = CappedScroll(context, JarvisUi.dp(context, REPLY_CAP_DP)).apply {
            isVerticalScrollBarEnabled = false
            isVerticalFadingEdgeEnabled = true
            addView(responseView)
        }
        responseScroll = scroll
        column.addView(scroll, fullWidth())

        // An approval, answered here (the owner, 2026-09-25: "if it asks for
        // approval for something, I can't click it without closing Jarvis").
        val approvals = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.GAP), 0, 0)
        }
        approvalRow = approvals
        column.addView(approvals, fullWidth())

        // A notice Jarvis pulled up while the island is out (the owner: "things it
        // can pull up for notifications"): shown for a moment, then gone.
        val notices = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.GAP), 0, 0)
        }
        noticeRow = notices
        column.addView(notices, fullWidth())

        // The route, while one is held: under the turn when there is one, alone when there is not.
        val activities = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            visibility = View.GONE
            setPadding(0, JarvisUi.dp(context, JarvisUi.Space.GAP), 0, 0)
        }
        activityRow = activities
        column.addView(activities, fullWidth())
        island.card.addView(column, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val orbView = ReactorView(context)
        orb = orbView
        island.host(orbView, pillSlot, cardSlot)
        return island
    }

    /** The shared builders centre their words; on the island everything reads from the start. */
    private fun start(view: TextView) = view.apply { gravity = Gravity.START }

    private fun fullWidth() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    )

    /**
     * The island's window: top-centre under the status bar, as large as the island needs.
     *
     * @param focus the card is open: what is behind it dims, and blurs where the platform
     *   blurs across windows (API 31+, and not under battery saver or with it switched off).
     *   The words never depend on the blur: the island's backing is opaque
     *   (`overlay_scrim_test.py`).
     */
    private fun params(focus: Boolean, touchable: Boolean, place: Island.Place): WindowManager.LayoutParams {
        val windows = context.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
        return WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // NOT_FOCUSABLE so the app behind keeps its keyboard and its focus —
            // an overlay that steals focus while somebody is typing is worse
            // than no overlay. NOT_TOUCH_MODAL so touches outside the island go
            // where they were aimed.
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED or
                // A wake word is for the phone you are NOT holding, so the
                // screen is usually off and the keyguard usually up. These are
                // deprecated for Activities — replaced by setShowWhenLocked()
                // and setTurnScreenOn(), which only exist on Activity — and are
                // still the only way to say it for a window put up by a
                // service. Where a build ignores them the overlay simply sits
                // behind the keyguard, which is why WakeWordService posts the
                // full-screen intent as well whenever the phone is locked.
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            PixelFormat.TRANSLUCENT,
        ).apply {
            // The status bar is outside the window's frame (an overlay fits the system
            // bars by default), so this is a gap under it, not under the screen's edge.
            // At the bottom it is a gap over the navigation bar, likewise (T-0885).
            gravity = (if (place == Island.Place.BOTTOM) Gravity.BOTTOM else Gravity.TOP) or Gravity.CENTER_HORIZONTAL
            y = JarvisUi.dp(context, JarvisUi.Space.SNUG)
            val on = anchor?.takeIf { place == Island.Place.TOP }
            if (on != null) {
                // On the camera, as the resting island it takes over from (RestIsland): not fitted
                // below the bars, which is the gap under the status bar it sat in before (T-0968).
                x = on.at.x
                y = on.at.y
                flags = flags or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
                layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                fitInsetsTypes = 0
            }
            windowAnimations = 0
            if (!touchable) flags = flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
            if (place == Island.Place.BOTTOM) flags = flags or WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH
            // At the bottom the core's own glow is all there is: nothing dimmed or blurred past
            // it (the owner, 2026-11-09, T-0912: "not the entire screen").
            if (focus && place == Island.Place.TOP) {
                flags = flags or WindowManager.LayoutParams.FLAG_DIM_BEHIND
                dimAmount = Color.alpha(JarvisTokens.Material.Floating.DIM) / OPAQUE
                if (windows?.isCrossWindowBlurEnabled == true) {
                    flags = flags or WindowManager.LayoutParams.FLAG_BLUR_BEHIND
                    blurBehindRadius = JarvisUi.dp(context, JarvisTokens.Material.Floating.BLUR_DP)
                }
            }
        }
    }

    companion object {
        /** How long a notice stays on the island: the console popup's `NOTICE_MS`. */
        const val NOTICE_MS = 8000L

        private const val TAG = "JarvisOverlay"

        private const val OPAQUE = 255f
        private const val MS_PER_S = 1000f
        /** A frame late by more than this moves the spring this far only, not in one leap. */
        private const val MAX_STEP_S = 0.05f

        /** The reply scrolls past this height inside the card (the old orb slot's side). */
        private const val REPLY_CAP_DP = 176

        /**
         * Whether "display over other apps" is granted.
         *
         * `Settings.canDrawOverlays` and nothing else: the manifest permission
         * is declared but that only makes the toggle appear in Settings, and
         * checking `checkSelfPermission` for SYSTEM_ALERT_WINDOW returns
         * GRANTED on some ROMs while `addView` still throws.
         */
        fun canShow(context: Context): Boolean =
            android.provider.Settings.canDrawOverlays(context)
    }
}
