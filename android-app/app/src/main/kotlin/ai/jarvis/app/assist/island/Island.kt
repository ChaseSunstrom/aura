package ai.jarvis.app.assist.island

import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The island (T-0695): Jarvis over someone else's screen, a pill at the top that grows into a
 * card and back, from what is going on. The popup was a dark box across the lower half that
 * the launcher read through, and approvals a full-screen sheet (the owner, 2026-11-09:
 * "something like a dynamic island"). The table is `tests/contracts/island.json`, which the
 * desktop popup (`jarvis-web` `ds/shell/island.ts`) reads too.
 *
 * Pure Kotlin: the JVM test and the console's suite bind the same rules. Does not cover how a
 * state is drawn or moves ([IslandView]).
 */
object Island {
    /**
     * [spoken] is what a screen reader says for the state (T-0699), the table's `spoken` of the
     * same key; a state with none cannot be written, and the test holds it to the table's words.
     * Not covered: the card's own text, which its views carry.
     */
    enum class State(val key: String, val spoken: String) {
        HIDDEN("hidden", ""),
        PILL("pill", "{name} is working. Tap to open."),
        ACTIVITY("activity", "{name} has a live update. Tap to open."),
        CARD("card", "{name} conversation"),
        APPROVAL("approval", "{name} needs your answer"),
        REST("rest", "{name} is listening for its name");

        /** [spoken] with the worn pack's said name in its `{name}` slot (AURA is not called Jarvis). */
        fun said(name: String): String = spoken.replace("{name}", name)
    }

    /** What is going on; each is the table's `inputs` line of the same name. */
    data class Inputs(
        val turn: Boolean = false,
        val ask: Boolean = false,
        val notice: Boolean = false,
        val activity: Boolean = false,
        val expanded: Boolean = false,
        val collapsed: Boolean = false,
        val working: Boolean = false,
        val touched: Boolean = false,
        /** The assistant is on and its island kept showing: the wake service with the setting on (T-0890). */
        val on: Boolean = false,
    )

    /** Where the phone puts the island: the table's `places` (T-0885). */
    enum class Place(val key: String) { BOTTOM("bottom"), TOP("top") }

    /** A state's size in dp ([CONTENT]: as tall as what it shows; null: not drawn), and whether it takes the focus. */
    data class Size(val widthDp: Int?, val heightDp: Int?, val focus: Boolean)

    const val CONTENT = -1
    const val RADIUS_DP = JarvisTokens.Material.Floating.RADIUS_DP

    val SIZES: Map<State, Size> = with(JarvisTokens.Material.Floating) {
        mapOf(
            State.HIDDEN to Size(null, null, focus = false),
            State.PILL to Size(PILL_WIDTH_DP, PILL_HEIGHT_DP, focus = false),
            State.ACTIVITY to Size(ACTIVITY_WIDTH_DP, PILL_HEIGHT_DP, focus = false),
            State.CARD to Size(CARD_WIDTH_DP, CONTENT, focus = true),
            State.APPROVAL to Size(CARD_WIDTH_DP, CONTENT, focus = true),
            State.REST to Size(REST_WIDTH_DP, REST_HEIGHT_DP, focus = false),
        )
    }

    /**
     * The state for what is going on. An approval is never flicked away unanswered; a flick
     * puts a live turn in its pill and anything else out of the way until something new arrives.
     */
    fun stateFor(i: Inputs): State = when {
        i.ask -> State.APPROVAL
        // At work or with the phone in use, a turn steps out of the way to its pill until opened (T-0885).
        i.turn -> if (i.collapsed || ((i.working || i.touched) && !i.expanded)) State.PILL else State.CARD
        // With the assistant on, nothing going on is the island resting on the camera (T-0890).
        i.collapsed -> if (i.on) State.REST else State.HIDDEN
        i.notice || i.expanded -> State.CARD
        i.activity -> State.ACTIVITY
        else -> if (i.on) State.REST else State.HIDDEN
    }

    /**
     * Where [state] sits: a live turn's card at the bottom, as the reactor that talks, and
     * everything else at the top, as the island (the owner, 2026-11-09: "at the bottom of the
     * screen as the nice reactor, and then if it goes to tasks or the user tries to use the
     * phone, move to the dynamic island look at the top"). A card opened from the pill stays up
     * where it was opened. [bottom] is the phone's setting that puts every state at the bottom
     * instead (the owner, 2026-11-10, T-0968: "also configurable to be at the bottom instead").
     */
    fun placeFor(state: State, i: Inputs, bottom: Boolean = false): Place =
        if (bottom || bare(state, i)) Place.BOTTOM else Place.TOP

    /** The state drawn as the core alone, no box and no words (T-0912): a live turn's card nobody opened, wherever it sits. */
    fun bare(state: State, i: Inputs): Boolean = state == State.CARD && i.turn && !i.expanded && !i.activity

    enum class Gesture { TAP, FLICK_UP, PULL_DOWN }

    /** What a gesture asks of the island: open it, put it out of the way, or end the conversation. */
    enum class Action { NONE, EXPAND, COLLAPSE, DISMISS }

    /**
     * A gesture on a state. An approval takes only its answers. A flick on the card leaves the
     * pill; a flick on the pill ends the turn; a flick on an activity hides it, still held.
     */
    fun act(state: State, gesture: Gesture): Action = when (state) {
        // The resting island is never touched: the status bar's pull goes through it (T-0890).
        State.HIDDEN, State.APPROVAL, State.REST -> Action.NONE
        State.CARD -> if (gesture == Gesture.PULL_DOWN) Action.NONE else Action.COLLAPSE
        State.PILL -> if (gesture == Gesture.FLICK_UP) Action.DISMISS else Action.EXPAND
        State.ACTIVITY -> if (gesture == Gesture.FLICK_UP) Action.COLLAPSE else Action.EXPAND
    }

    /**
     * The window's size for a morph from [from] to [to] (w, h): the larger of each, set once as
     * the morph starts, and [to] once it has settled. A window resized every frame of a spring
     * shows a seam and costs a relayout per frame; grown first and shrunk last, it holds still.
     */
    fun frame(from: Pair<Int, Int>, to: Pair<Int, Int>): Pair<Int, Int> =
        maxOf(from.first, to.first) to maxOf(from.second, to.second)

    /**
     * A damped spring on one number (`spring.*`: stiffness, damping, mass), interruptible: a new
     * target keeps the value and velocity it has. [rest] is how close counts as there, in the
     * value's own units. Semi-implicit Euler in [SUBSTEP_S] steps, stable at the tokens' stiffness.
     */
    class Spring(
        private val stiffness: Float,
        private val damping: Float,
        private val mass: Float,
        private val rest: Float,
    ) {
        var value = 0f
        var velocity = 0f
        var target = 0f

        val moving: Boolean get() = value != target || velocity != 0f

        fun snap(to: Float) {
            value = to
            target = to
            velocity = 0f
        }

        /** [dt] seconds on; false once it is at rest on its target. */
        fun step(dt: Float): Boolean {
            var left = dt
            while (left > 0f) {
                val h = minOf(left, SUBSTEP_S)
                velocity += (-stiffness * (value - target) - damping * velocity) / mass * h
                value += velocity * h
                left -= h
            }
            if (kotlin.math.abs(value - target) < rest && kotlin.math.abs(velocity) < rest * REST_PER_S) {
                snap(target)
                return false
            }
            return true
        }

        companion object {
            const val SUBSTEP_S = 0.004f
            /** At rest also means slow: under [rest] per this fraction of a second. */
            private const val REST_PER_S = 10f

            fun glass(rest: Float) = with(JarvisTokens.Spring.Glass) { Spring(STIFFNESS, DAMPING, MASS, rest) }
            fun snap(rest: Float) = with(JarvisTokens.Spring.Snap) { Spring(STIFFNESS, DAMPING, MASS, rest) }
        }
    }
}
