package ai.jarvis.app.ui

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.provider.Settings
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import ai.jarvis.app.ui.theme.JarvisTokens
import kotlin.math.min

/**
 * Shared look-and-feel for every Jarvis surface, on Reactor II: near-black
 * ground, flat hairline panels, one cyan spent on the one thing that is live,
 * the body face for words and mono only for data. Built programmatically on
 * purpose — the assist popup and the consent prompt must draw their first
 * frame without inflating a layout tree, and keeping every screen on the same
 * helpers is what stops the app from drifting into four different visual
 * languages.
 *
 * What is NOT here any more, and why (M51): the filled cyan pill, the ghost
 * outline with the rounded corners, and the corner brackets. They were the
 * previous direction — the console's own pages have already lost them — and a
 * phone that keeps them is the "kind of similar but not really" report in a
 * different shape. A control is [button] (a hairline, quiet) or [primary]
 * (filled, exactly one per screen); a surface is [panel] (flat, a hairline);
 * a frame is the screen's own edge.
 */
object JarvisUi {

    // Every one of these is a `--jv-*` token from `design/tokens.json`, reached
    // through the generated `JarvisTokens`; `design_token_test.py` checks the
    // aliases against the source.
    //
    // They were a second palette that happened to look similar: three of the
    // eight matched a web token and five were near misses nobody could see
    // were misses, because the two are never on screen together. The phone's
    // settings sit inside the console's frame now, under the console's own nav
    // — *"have the settings for the android app be in that same web view look?
    // so we can dedup the things"* — and one nav over two palettes is exactly
    // the drift that produces.
    //
    // One of the five was not merely different. FAINT was #5A7A86, which is
    // 4.38:1 on this ground — under WCAG AA — and it is the colour every hint
    // on every screen is drawn in. `--jv-text-faint` is 9.01:1.
    const val ACCENT = JarvisTokens.Color.ACCENT // --jv-accent
    /** [ACCENT] in the primary persona's hue (PersonaAccent): what a control is drawn in. */
    val accent: Int get() = ai.jarvis.app.ui.theme.PersonaAccent.argb
    /**
     * Body text. `--jv-text`.
     *
     * Added for the assist overlay, which floats over whatever the user was
     * looking at: DIM is the palette's quiet colour and reaches WCAG AA there
     * only if the scrim behind it is nearly opaque, which is the dark slab this
     * surface has already had removed twice. A brighter line needs less ground.
     */
    const val TEXT = JarvisTokens.Color.TEXT // --jv-text
    /** `--jv-text-dim`, at the 80% alpha the phone has always drawn it with. */
    const val DIM = JarvisTokens.Color.TEXT_DIM_80 // --jv-text-dim
    const val BG = JarvisTokens.Color.BG // --jv-bg
    const val SURFACE = JarvisTokens.Color.PANEL_SOLID // --jv-panel-solid
    const val FAINT = JarvisTokens.Color.TEXT_FAINT // --jv-text-faint
    const val APPROVE = JarvisTokens.Color.OK // --jv-ok
    const val DENY = JarvisTokens.Color.DANGER // --jv-danger

    /**
     * Danger as WORDS. `--jv-danger-text`.
     *
     * [DENY] is the mark colour — a dot, a stroke, the fill of the no button's
     * edge — and the console never sets a sentence in it (`CallLine.svelte`
     * draws the failed dot in `--jv-danger` and the reason beside it in
     * `--jv-danger-text`). The phone did: every "it failed" line was in the
     * mark colour, one step short of the text colour that was made for it.
     */
    const val DENY_TEXT = JarvisTokens.Color.DANGER_TEXT // --jv-danger-text

    /**
     * Wants attention, but nothing is broken.
     *
     * DENY reads as "this failed"; a permission you have simply not granted has
     * not failed. Matches `--jv-warn` in the console, which marks the same idea
     * there — held, not wrong.
     */
    const val GOLD = JarvisTokens.Color.WARN // --jv-warn

    /**
     * The type scale.
     *
     * The colours in this file have been tokenised — and pinned against
     * `jarvis-web/src/lib/tokens.ts` by `tools/design_token_test.py` — since the
     * phone and the console were found to be running two palettes that merely
     * looked alike. Type had exactly the same problem and no such treatment:
     * sizes were inline SP literals (11, 12, 13, 14, 15, 20, 21, 22) scattered
     * across every activity, so "the same size as a hint" was a number somebody
     * remembered rather than a name, and `CompanionAskActivity`'s question was
     * 21sp against `JarvisUi.responseView`'s 20sp for no reason anybody could
     * state.
     *
     * Seven steps, named for the job rather than for the number, so a screen
     * asks for "a label" and gets whatever a label is.
     */
    object Type {
        /** All-caps section labels and chrome captions. */
        const val LABEL = JarvisTokens.Type.LABEL

        /** Explanatory body copy, quiet buttons, status lines. */
        const val HINT = JarvisTokens.Type.HINT

        /** Verbatim machine text, checklist glyphs. */
        const val MONO = JarvisTokens.Type.MONO

        /** Ordinary interface text: switch rows, checklist titles, tags. */
        const val BODY = JarvisTokens.Type.BODY

        /** Text the user typed or is about to, plus consent buttons. */
        const val FIELD = JarvisTokens.Type.FIELD

        /** What Jarvis said. The largest thing on a conversation surface. */
        const val RESPONSE = JarvisTokens.Type.RESPONSE

        /** The JARVIS wordmark and screen titles. */
        const val TITLE = JarvisTokens.Type.TITLE
    }

    /**
     * The spacing scale, in dp, for use with [dp].
     *
     * Same argument as [Type]: `dp(ctx, Space.ROW)`, `dp(ctx, Space.GAP)`, `dp(ctx, Size.CHIP)`,
     * `dp(ctx, Space.SECTION)`, `dp(ctx, Space.SCREEN)` and `dp(ctx, Space.WIDE)` all appeared across the
     * screens with nothing to say which was which. These are the steps that were
     * actually in use, named — not a new rhythm imposed on a working layout,
     * which is why the numbers are unchanged.
     */
    object Space {
        /** A hairline stroke. */
        const val HAIRLINE = JarvisTokens.Space.HAIRLINE

        /** Between a line of text and its own line spacing. */
        const val TIGHT = JarvisTokens.Space.TIGHT

        /** Line spacing, and the inset on a small radius. */
        const val MICRO = JarvisTokens.Space.MICRO

        /** Between a label and the thing it labels. */
        const val SNUG = JarvisTokens.Space.SNUG

        /** Between a row and the next thing that is not part of it. */
        const val STEP = JarvisTokens.Space.STEP

        /** Inside a row: a glyph and its text. */
        const val ROW = JarvisTokens.Space.ROW

        /** Between two controls. */
        const val GAP = JarvisTokens.Space.GAP

        /** Between one section and the next. */
        const val SECTION = JarvisTokens.Space.SECTION

        /** A screen's own margin. */
        const val SCREEN = JarvisTokens.Space.SCREEN

        /** A screen's margin where the content is a single centred column. */
        const val WIDE = JarvisTokens.Space.WIDE
    }

    /**
     * How big a THING is, as distinct from the gap beside it.
     *
     * A size snapped to the spacing scale is how a 34 dp consent button — wide
     * on purpose, because it is the one nobody may press by accident — quietly
     * becomes a 32 dp one. Named in `design/tokens.json` like everything else
     * here, and generated into [JarvisTokens].
     */
    object Size {
        /** A spacer between two buttons, and the smallest tap ornament. */
        const val CHIP = JarvisTokens.Size.CHIP

        /** A sheet's own padding, inset from the screen's. */
        const val INSET = JarvisTokens.Size.INSET

        /** A dialog's side gutter. */
        const val GUTTER = JarvisTokens.Size.GUTTER

        /** The consent button's corner radius (kept for callers; buttons are `Radius.MD` now). */
        const val EDGE = JarvisTokens.Size.EDGE

        /** The margin a sheet keeps from the screen edge. */
        const val SHEET = JarvisTokens.Size.SHEET

        /** The side padding of a consent button. */
        const val WIDE_BUTTON = JarvisTokens.Size.WIDE_BUTTON

        /** How far below the top edge a floating panel sits. */
        const val DROP = JarvisTokens.Size.DROP

        /** The widest a floating panel gets, whatever the screen. */
        const val PANEL_MAX = JarvisTokens.Size.PANEL_MAX

        /** The shortest a sheet gets before it scrolls. */
        const val SHEET_MIN = JarvisTokens.Size.SHEET_MIN

        /** The smallest a control a finger lands on may be (GLASS_HUD §12: 48 dp). */
        const val TOUCH = JarvisTokens.Size.TOUCH
    }

    fun dp(context: Context, v: Int): Int =
        (v * context.resources.displayMetrics.density).toInt()

    // --- motion ---------------------------------------------------------------

    /** `--jv-ease-out`, for an animator: a tab sliding, a row entering. */
    val EASE_OUT: PathInterpolator
        get() = JarvisTokens.Motion.Ease.OUT.let { PathInterpolator(it[0], it[1], it[2], it[3]) }

    /** `--jv-ease-in-out`, for something that breathes. */
    val EASE_IN_OUT: PathInterpolator
        get() = JarvisTokens.Motion.Ease.IN_OUT.let { PathInterpolator(it[0], it[1], it[2], it[3]) }

    /**
     * The person asked for no motion.
     *
     * Android has no `prefers-reduced-motion`. What it has is two scales in
     * developer options that a battery saver also drives to zero: the animator
     * duration scale, which kills every `ValueAnimator` on its first frame, and
     * the transition scale, which the boot sequence already reads as "do not
     * play this". Either at zero is the only way the platform lets somebody
     * say it, so either counts. Nothing decorative moves when this is true —
     * the reactor's drift, a live dot's pulse, the task sweep, a row's
     * entrance — while the things that carry information (the level arc,
     * a colour change, a bar's width) still change, without easing.
     *
     * Read at the moment it is needed rather than cached for the process: a
     * user flips the setting and comes back to the app, and a value read at
     * startup would describe the phone they no longer have. Callers that draw
     * sixty times a second read it once per attach, not per frame — this is a
     * content-provider query.
     */
    fun reducedMotion(context: Context): Boolean = try {
        val resolver = context.contentResolver
        Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) <= 0f ||
            Settings.Global.getFloat(resolver, Settings.Global.TRANSITION_ANIMATION_SCALE, 1f) <= 0f
    } catch (t: Throwable) {
        // GrapheneOS is strict about what a third-party app may read out of
        // Settings, and a decoration must never be the thing that throws.
        false
    }

    /**
     * Bring [view] in the way the console's panels arrive: from a step below,
     * fading up over `--jv-dur-enter`, [index] steps of `--jv-stagger-step`
     * after the first, capped at `--jv-stagger-cap`.
     *
     * The stagger tokens were generated into [JarvisTokens.Motion.Stagger]
     * and read by nothing; every list on the phone simply appeared. Starts on
     * attach when the view is not on screen yet — a screen builds its rows
     * before `setContentView` — and does nothing at all when the view is
     * never attached (a golden is drawn off a window), so a screenshot cannot
     * record a row at the alpha it started from. Under reduced motion the row
     * is simply there.
     */
    fun enter(view: View, index: Int = 0) {
        if (reducedMotion(view.context)) return
        val start = {
            view.alpha = 0f
            view.translationY = dp(view.context, Space.STEP).toFloat()
            view.animate()
                .alpha(1f)
                .translationY(0f)
                .setStartDelay(
                    min(index * JarvisTokens.Motion.Stagger.STEP, JarvisTokens.Motion.Stagger.CAP)
                        .toLong()
                )
                .setDuration(JarvisTokens.Motion.Dur.ENTER.toLong())
                .setInterpolator(EASE_OUT)
                .start()
        }
        if (view.isAttachedToWindow) {
            start()
            return
        }
        view.addOnAttachStateChangeListener(object : View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v: View) {
                v.removeOnAttachStateChangeListener(this)
                start()
            }

            override fun onViewDetachedFromWindow(v: View) = Unit
        })
    }

    // --- the faces ------------------------------------------------------------
    //
    // The brief (BUILD_BRIEF §1) sets interface text and data in JetBrains Mono and
    // big numbers in Space Grotesk Light, bundled in res/font. These were the platform
    // sans and mono, so every View screen was set in Roboto beside a Compose one
    // (Car setup) and a console in Barlow -- the owner, 2026-09-27: "make them match
    // the look of everything else, and consistent". [loadFaces] swaps them in at
    // process start; until then (and if a font fails to load) the platform faces
    // stand in, so nothing is ever drawn without a face.

    /** Words: interface text and prose (JetBrains Mono 400). */
    var BODY_FACE: Typeface = Typeface.SANS_SERIF
        private set

    /** Chrome labels and controls: a shade heavier (JetBrains Mono 500). */
    var LABEL_FACE: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        private set

    /** Big numbers and the wordmark (Space Grotesk 300). */
    var DISPLAY_FACE: Typeface = Typeface.create("sans-serif-light", Typeface.NORMAL)
        private set

    /** Data: ids, timings, parameters, log lines. Never prose (JetBrains Mono 400). */
    var MONO_FACE: Typeface = Typeface.MONOSPACE
        private set

    /** The bundled faces, once per process (`JarvisApp.onCreate`). */
    fun loadFaces(context: Context) {
        fun face(id: Int, fallback: Typeface) =
            runCatching { androidx.core.content.res.ResourcesCompat.getFont(context, id) }.getOrNull() ?: fallback
        BODY_FACE = face(ai.jarvis.app.R.font.jetbrains_mono_400, BODY_FACE)
        LABEL_FACE = face(ai.jarvis.app.R.font.jetbrains_mono_500, LABEL_FACE)
        DISPLAY_FACE = face(ai.jarvis.app.R.font.space_grotesk_300, DISPLAY_FACE)
        MONO_FACE = face(ai.jarvis.app.R.font.jetbrains_mono_400, MONO_FACE)
    }

    // No tracking on a label or a button: HUD.md §3 is sentence case with none,
    // and the console dropped its label and heading steps (2026-09-24) after a
    // tracked word read as spaced-out letters. The owner's word for the phone's
    // tracked capitals ("SET UP JARVIS", "SYSTEM CHECK") was "weird".

    /** A hair of tracking on running text set in the display face and on mono readouts. */
    const val TRACK_SNUG = JarvisTokens.Tracking.SNUG

    // --- accessibility --------------------------------------------------------
    //
    // There were NO `contentDescription`, `announceForAccessibility` or
    // `accessibilityLiveRegion` calls anywhere in `app/src/main/kotlin` outside
    // `automation/accessibility/`, which is the module that READS OTHER APPS'
    // screens for the automation engine. Jarvis could drive another app's UI for
    // a blind user and could not describe its own: the orb was an unlabelled
    // custom View, every state caption was silent, tool-activity rows were
    // silent, and a pipeline moving from listening to thinking to speaking
    // announced nothing at all — on a voice assistant, whose users include
    // people who cannot see the screen it is drawing.
    //
    // These three helpers are deliberately tiny and deliberately here rather
    // than per screen: `tools/accessibility_labels_test.py` requires every
    // surface to use them, and a screen that has to write its own
    // `sendAccessibilityEvent` boilerplate is a screen that will not.

    /**
     * Mark [view] as a region TalkBack re-reads whenever its text changes.
     *
     * `POLITE`, never `ASSERTIVE`: a pipeline state changes several times a
     * turn, and assertive interrupts whatever the user is currently listening to
     * — including Jarvis's own reply. Polite queues behind it, which is the
     * behaviour the words themselves have.
     */
    fun liveRegion(view: View) {
        view.accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
    }

    /**
     * Say [text] out loud now, whether or not anything on screen changed.
     *
     * For the transitions that are not a text change: the orb entering, a turn
     * ending, a question arriving on a surface that was already up. A live
     * region cannot cover those because nothing it contains moved.
     *
     * A no-op when the text is blank, so a caller may pass a value that is
     * sometimes empty without guarding it.
     */
    fun announce(view: View, text: String) {
        if (text.isBlank()) return
        view.announceForAccessibility(text)
    }

    /**
     * Give [view] a spoken label, or take one away.
     *
     * Blank clears it rather than setting an empty description — an empty string
     * is a description, and TalkBack reads a control with one as unlabelled
     * *and* silent, which is worse than the default.
     */
    fun describe(view: View, text: String?) {
        view.contentDescription = text?.takeIf { it.isNotBlank() }
    }

    /**
     * Inset a screen's root so its content is not under the status bar or the
     * navigation bar.
     *
     * ## Why every ordinary screen needs this now
     *
     * Reported as *"the tabs on the settings for the android app are too high
     * up, and I can't click on them"*, which is exactly what it looks like:
     * the nav strip is the first thing in the layout, so it was drawn beneath
     * the status bar, and a tap up there goes to the system rather than to the
     * button under it.
     *
     * The cause is `targetSdk = 35`. **Android 15 enforces edge-to-edge for
     * apps that target it**: the window is laid out behind the system bars
     * whether or not the app asked, and `android:statusBarColor` /
     * `android:navigationBarColor` — which `Theme.JarvisBase` sets, and which
     * used to reserve that space — are deprecated and ignored. Nothing warns.
     * The screens that hide the bars outright ([immersive]) were unaffected and
     * still are, which is why this only ever showed up on Settings, Manage,
     * SYSTEM CHECK, the crash log and the two automation screens.
     *
     * Padding rather than `setDecorFitsSystemWindows(true)`: the latter is
     * deprecated on the same release, and this keeps the window background
     * drawn edge to edge — so the bars still sit over Jarvis's own black rather
     * than over a strip of grey.
     *
     * `displayCutout` is folded in because a punch-hole or notch in landscape
     * eats the left or right edge in exactly the same way.
     */
    fun fitSystemBars(root: android.view.View) {
        root.setOnApplyWindowInsetsListener { view, insets ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bars = insets.getInsets(
                    android.view.WindowInsets.Type.systemBars() or
                        android.view.WindowInsets.Type.displayCutout()
                )
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                // minSdk is 29, where `getInsets(int)` does not exist yet.
                @Suppress("DEPRECATION")
                view.setPadding(
                    insets.systemWindowInsetLeft,
                    insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight,
                    insets.systemWindowInsetBottom,
                )
            }
            insets
        }
        // A view that is already attached has had its insets dispatched, and
        // the listener above has just missed them.
        if (root.isAttachedToWindow) root.requestApplyInsets()
    }

    /**
     * Edge-to-edge, system bars hidden (swipe to reveal).
     *
     * Every caller runs this from `onCreate`, **before** `setContentView` — so
     * the controller has to be fetched through the decor view and never through
     * `Window.insetsController`. On API 30 `PhoneWindow.getInsetsController()`
     * is a bare `return mDecor.getWindowInsetsController();`, and until a
     * `setContentView` or a `getDecorView()` has installed the decor, `mDecor`
     * is null. The NPE is thrown *inside the getter*, so the `?.` below cannot
     * catch it and the Activity dies before its first frame:
     *
     *     java.lang.RuntimeException: Unable to start activity
     *       ComponentInfo{ai.jarvis.app/ai.jarvis.app.MainActivity}:
     *     java.lang.NullPointerException: Attempt to invoke virtual method
     *       'android.view.WindowInsetsController
     *        com.android.internal.policy.DecorView.getWindowInsetsController()'
     *       on a null object reference
     *       at com.android.internal.policy.PhoneWindow.getInsetsController(…)
     *       at ai.jarvis.app.ui.JarvisUi.immersive(JarvisUi.kt:46)
     *
     * That is a real crash on Android 11, caught by `AppLaunchTest` the first
     * time the instrumented suite ever ran on an emulator.
     *
     * Asking the window for its decor installs it, and a DecorView that is not
     * attached yet hands back a *pending* controller which replays these two
     * calls the moment the window is attached — which is exactly the ordering
     * this call site wants. `android-app/tools/window_insets_test.py` pins it.
     */
    fun immersive(activity: Activity) {
        val w = activity.window
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            w.setDecorFitsSystemWindows(false)
            w.decorView.windowInsetsController?.let { c ->
                c.hide(android.view.WindowInsets.Type.systemBars())
                c.systemBarsBehavior =
                    android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            w.decorView.systemUiVisibility = (
                android.view.View.SYSTEM_UI_FLAG_FULLSCREEN
                    or android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    or android.view.View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    or android.view.View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
        }
    }

    /**
     * What the user said, as it is transcribed.
     *
     * A live region: the words appear one partial at a time, and a blind user
     * needs to hear that the microphone is hearing them at all — which is the
     * whole job this view does for a sighted one.
     */
    fun transcriptView(context: Context): TextView = TextView(context).apply {
        setTextColor(DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.FIELD)
        gravity = Gravity.CENTER
        typeface = BODY_FACE
        liveRegion(this)
    }

    /**
     * What Jarvis said. Also a live region, for the same reason. The display
     * face, light and bright: the one line on the screen to read first.
     */
    fun responseView(context: Context): TextView = TextView(context).apply {
        setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.RESPONSE)
        gravity = Gravity.CENTER
        typeface = DISPLAY_FACE
        letterSpacing = TRACK_SNUG
        setPadding(0, dp(context, Space.ROW), 0, 0)
        liveRegion(this)
    }

    // --- text ---------------------------------------------------------------

    /**
     * The label recipe, bare: the v2 `Label` (`$lib/ds`) — UPPERCASE, the label
     * face, the smallest step, tracked `--jv-track-label`. Every chrome word the
     * overlays and the orb cards draw is this, so they read like the kit's
     * screens (T-0639); it was sentence case and untracked, the Reactor II
     * recipe, which left the overlays' captions as the one place on the phone
     * a label looked like running text. It exists because nine screens typed
     * the same idea by hand in mono, and mono is for DATA — a heading in the
     * data face reads as a readout.
     */
    fun labelText(
        context: Context,
        text: String,
        color: Int = JarvisTokens.Color.TEXT_DIM,
    ): TextView = TextView(context).apply {
        this.text = text
        setTextColor(color)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
        typeface = LABEL_FACE
        letterSpacing = JarvisTokens.Tracking.LABEL
        isAllCaps = true
    }

    /** Small label above a field or a block: the label recipe with room above it. */
    fun label(context: Context, text: String): TextView = labelText(context, text).apply {
        setPadding(0, dp(context, Space.GAP), 0, dp(context, Space.TIGHT))
    }

    /**
     * A readout: data, in the data face. `StatusReadout.svelte`'s word, a
     * count, a duration, a host. Mono, the smallest step, snug tracking,
     * tabular digits so a changing number does not jitter its neighbours.
     * Never a heading — see [labelText].
     */
    fun readout(
        context: Context,
        text: String,
        color: Int = JarvisTokens.Color.TEXT_DIM,
    ): TextView = TextView(context).apply {
        this.text = text
        setTextColor(color)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
        letterSpacing = TRACK_SNUG
        typeface = MONO_FACE
        fontFeatureSettings = TABULAR_DIGITS
    }

    /** Explanatory body copy. */
    fun hint(context: Context, text: String): TextView = TextView(context).apply {
        this.text = text
        setTextColor(JarvisTokens.Color.TEXT_DIM)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.HINT)
        typeface = BODY_FACE
        setLineSpacing(dp(context, Space.TIGHT).toFloat(), 1f)
        setPadding(0, dp(context, Space.SNUG), 0, 0)
    }

    // --- containers ---------------------------------------------------------

    /** Vertical column with the standard screen padding. */
    fun column(context: Context, padDp: Int = Space.SCREEN): LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        val p = dp(context, padDp)
        setPadding(p, p, p, p)
    }

    fun spacer(context: Context, heightDp: Int): View = View(context).apply {
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(context, heightDp)
        )
    }

    // --- controls -----------------------------------------------------------

    fun field(
        context: Context,
        hint: String,
        value: String,
        secret: Boolean = false,
    ): EditText = EditText(context).apply {
        this.hint = hint
        setText(value)
        setTextColor(JarvisTokens.Color.TEXT_BRIGHT)
        setHintTextColor(FAINT)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.FIELD)
        typeface = BODY_FACE
        inputType = if (secret) {
            // VISIBLE_PASSWORD: a token is pasted and eyeballed, not typed from
            // memory, and hiding it just invites paste errors nobody can debug.
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
        } else {
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        setSingleLine(!secret)
        // Again, after the input type: a password variation resets the face to the
        // platform mono, which set the token field apart from every other field.
        typeface = BODY_FACE
        background = GradientDrawable().apply {
            cornerRadius = dp(context, JarvisTokens.Radius.MD).toFloat()
            setColor(JarvisTokens.Color.FIELD)
            setStroke(dp(context, Space.HAIRLINE), JarvisTokens.Color.LINE_SOFT)
        }
        // `Input.svelte`: space-2 above and below, space-3 at the sides. It
        // was GAP all round, a step deeper than the console's on every edge.
        setPadding(dp(context, Space.ROW), dp(context, Space.STEP), dp(context, Space.ROW), dp(context, Space.STEP))
        // That padding plus one line of FIELD type is a 36 dp box, and a finger
        // is not that precise: GLASS_HUD §12 and ATF's TouchTargetSizeCheck both
        // want 48. A minimum, not more padding, so the text stays where the
        // console's Input.svelte puts it and only the tappable box grows.
        minHeight = dp(context, Size.TOUCH)
        // An EditText with a hint is announced by the hint, and every field on
        // every screen has one — so the label above it would be read twice.
        // Stated here so a later screen does not add a second one.
    }

    /** A column's params for a control the width of its label (see [compact]). */
    fun wrapWidth(): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)

    /**
     * The width of its label, as the console's `Control` is: a parent that adds the
     * button with no params of its own keeps this, where a vertical column's default
     * would stretch it edge to edge. Every phone screen's buttons were full-width
     * boxes with the label in the middle, which is what read as a different app. A
     * caller that wants the full width still passes its own params.
     */
    private fun compact(view: View) {
        view.layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    /**
     * A button that looks disabled when it is: `Control.svelte`'s `:disabled` at 0.45.
     * The phone's buttons drew the same enabled or not, so a primary that could not
     * be pressed (Your voice, unpaired: "Tap to speak") still read as the thing to do
     * (the sweep, 2026-10-06). One place, so no screen has to remember an alpha.
     */
    // The platform Button on purpose: every phone screen is drawn with it, and
    // AppCompatButton brings its own style -- all-caps labels and a 33 dp height that
    // failed the 48 dp touch-target check and moved four goldens (2026-10-06).
    @SuppressLint("AppCompatCustomView")
    private class StatefulButton(context: Context) : Button(context) {
        override fun setEnabled(enabled: Boolean) {
            super.setEnabled(enabled)
            alpha = if (enabled) 1f else DISABLED_ALPHA
        }
    }

    /** `Control.svelte`'s disabled opacity. */
    const val DISABLED_ALPHA = 0.45f

    /**
     * The quiet control — Reactor II's `.btn`: a sentence-case label on a hairline,
     * dim until pressed, the one radius. Most buttons on a screen are this.
     */
    fun button(context: Context, label: String, onClick: () -> Unit): Button =
        StatefulButton(context).apply {
            text = label
            isAllCaps = false
            setTextColor(JarvisTokens.Color.TEXT_DIM)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
            typeface = LABEL_FACE
            background = GradientDrawable().apply {
                cornerRadius = dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(Color.TRANSPARENT)
                setStroke(dp(context, Space.HAIRLINE), JarvisTokens.Color.LINE)
            }
            // `Button.svelte`: space-2 by space-4 — 16 across, 10 down. It
            // was 20 by 12, and a control a step bigger than the console's on
            // every screen is what made the phone feel like a bigger, looser
            // copy rather than the same thing.
            setPadding(
                dp(context, Space.SECTION),
                dp(context, Space.ROW),
                dp(context, Space.SECTION),
                dp(context, Space.ROW),
            )
            compact(this)
            setOnClickListener { onClick() }
        }

    /**
     * The one filled control on a screen: the thing the screen is for. The
     * accent as a fill with the ink on it, and nothing else on the screen may
     * be filled — that is what makes it readable as the primary action.
     */
    fun primary(context: Context, label: String, onClick: () -> Unit): Button =
        StatefulButton(context).apply {
            text = label
            isAllCaps = false
            setTextColor(JarvisTokens.Color.ACCENT_INK)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, Type.LABEL)
            typeface = LABEL_FACE
            background = GradientDrawable().apply {
                cornerRadius = dp(context, JarvisTokens.Radius.MD).toFloat()
                setColor(accent)
                setStroke(dp(context, Space.HAIRLINE), accent)
            }
            // The same geometry as [button]: the fill is what makes it the
            // primary, not a wider box. It was 34 by 12.
            setPadding(
                dp(context, Space.SECTION),
                dp(context, Space.ROW),
                dp(context, Space.SECTION),
                dp(context, Space.ROW),
            )
            compact(this)
            setOnClickListener { onClick() }
        }

    /** A colour at an opacity, without writing either of them down twice. */
    fun atAlpha(color: Int, alpha: Int): Int =
        (color and 0x00FFFFFF) or (alpha.coerceIn(0, 255) shl 24)

    /** `font-variant-numeric: tabular-nums`, for a readout whose digits change. */
    private const val TABULAR_DIGITS = "tnum"

}
