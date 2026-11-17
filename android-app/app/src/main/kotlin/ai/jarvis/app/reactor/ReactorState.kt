package ai.jarvis.app.reactor

import ai.jarvis.app.ui.theme.JarvisTokens

/**
 * The eleven things the arc reactor can be doing (GLASS_HUD §10), and the rule
 * that picks one when several apply.
 *
 * The phone, the console and the web derive this from the same inputs with the
 * same precedence — `tests/contracts/reactor.json` `derivation` is the table,
 * and `ReactorStateTest` runs its cases against this file — so an approval waiting on the phone cannot show as "thinking"
 * while the console shows "needs you". [word] is the tray / notification word
 * (§10: the same state word on every surface); [color] is C, the state's light,
 * an alias of a base colour so no hue is added.
 *
 * Pure Kotlin on purpose: the JVM unit test and the Python mirror both read it,
 * and an `android.*` import here would drag the framework into a table.
 */
enum class ReactorState(val key: String, val word: String, val color: Int) {
    IDLE("idle", "Ready", JarvisTokens.Color.REACTOR_IDLE),
    LISTENING("listening", "Listening", JarvisTokens.Color.REACTOR_LISTENING),
    THINKING("thinking", "Thinking", JarvisTokens.Color.REACTOR_THINKING),
    SPEAKING("speaking", "Speaking", JarvisTokens.Color.REACTOR_SPEAKING),
    TOOL("tool", "Working", JarvisTokens.Color.REACTOR_TOOL),
    GOAL("goal", "On a goal", JarvisTokens.Color.REACTOR_GOAL),
    ATTENTION("attention", "Needs you", JarvisTokens.Color.REACTOR_ATTENTION),
    WARMING("warming", "Warming up", JarvisTokens.Color.REACTOR_WARMING),
    ALERT("alert", "Alert", JarvisTokens.Color.REACTOR_ALERT),
    KILL("kill", "Kill switch", JarvisTokens.Color.REACTOR_KILL),
    OFFLINE("offline", "Offline", JarvisTokens.Color.REACTOR_OFFLINE);

    /** Nothing moves in these two: the room has gone dark (§10 rows 10 and 11). */
    val still: Boolean get() = this == KILL || this == OFFLINE

    companion object {
        /**
         * Highest first. GOAL is not here: it is the overlay drawn on top of
         * whatever state won, never a winner itself.
         */
        val PRECEDENCE: List<ReactorState> = listOf(
            KILL, OFFLINE, ALERT, ATTENTION, WARMING, SPEAKING, LISTENING, TOOL, THINKING, IDLE,
        )

        /**
         * The states in which the reactor wears the primary persona's hue
         * instead of its own (`tests/contracts/persona.json` `calm_states`;
         * the console's `CALM_STATES`, the terminal's `_LOUD_STATES` is the
         * complement). Every other state keeps its colour because that colour
         * is information: an approval waiting in Ada's pink is an approval
         * nobody noticed.
         */
        val CALM: Set<ReactorState> = setOf(IDLE, SPEAKING, TOOL, GOAL)
    }
}

/**
 * The hue the primary persona paints the reactor with (`--jv-persona-*`), or
 * null for no tint. JARVIS is null on purpose — its token is an alias of the
 * reactor's own light, and tinting with it would still turn the deep idle
 * colour into the bright accent — and so is any name that is not one of the
 * two, because a mishearing or an empty status must fall to the reactor's own
 * colour rather than to somebody else's. `tests/contracts/persona.json`
 * `hues` and `untinted` are the table; `android-app/tools/persona_hue_test.py`
 * reads this against it.
 */
fun personaHue(name: String): Int? = when (name.lowercase()) {
    "aura" -> JarvisTokens.Color.PERSONA_AURA
    else -> null
}

/**
 * Everything a surface knows that bears on the reactor, with the contract's
 * resting values as defaults so a caller names only what is true.
 */
data class ReactorInputs(
    /** The kill switch (Phase 7). False until it exists. */
    val kill: Boolean = false,
    /** The HUD socket is up and the adapter has not said the backend is down. */
    val linkUp: Boolean = true,
    /** An `adapter.error` arrived and nothing has cleared it. */
    val adapterError: Boolean = false,
    /** From `prompt.submit` to `message.complete`. */
    val turnRunning: Boolean = false,
    /** The turn's own error, or an `error` event, not yet cleared by a new turn. */
    val errorEvent: Boolean = false,
    /** An approval or a clarifying question is waiting, or the goal is parked. */
    val attention: Boolean = false,
    /** `GET /api/status` says the model is starting. */
    val warming: Boolean = false,
    /** TTS is playing. */
    val speaking: Boolean = false,
    /** The microphone is open for a turn. */
    val listening: Boolean = false,
    /** `tool.start` events without their `tool.complete`, this turn. */
    val toolsRunning: Int = 0,
    /** A goal is set, continuing or parked. */
    val goalActive: Boolean = false,
)

/** What the reactor wears, and whether the goal satellite orbits it. */
data class Derived(val state: ReactorState, val goal: Boolean) {
    /**
     * What a screen reader says for the core (T-0853): the goal is drawn as a satellite, which
     * TalkBack cannot see, so it is said after the state's word.
     */
    val spoken: String get() = if (goal && state != ReactorState.GOAL) "${state.word}, on a goal" else state.word
}

/**
 * The precedence rule, one branch per row of the contract's `derivation.rules`,
 * in the contract's order. An `adapter.error` is an alert while a turn runs
 * (something was lost) and offline at rest (nothing was); the goal overlay is
 * dropped on the two still states because nothing may move there.
 */
fun derive(i: ReactorInputs): Derived {
    val state = when {
        i.kill -> ReactorState.KILL
        !i.linkUp || (i.adapterError && !i.turnRunning) -> ReactorState.OFFLINE
        i.errorEvent || (i.adapterError && i.turnRunning) -> ReactorState.ALERT
        i.attention -> ReactorState.ATTENTION
        i.warming -> ReactorState.WARMING
        i.speaking -> ReactorState.SPEAKING
        i.listening -> ReactorState.LISTENING
        i.toolsRunning > 0 -> ReactorState.TOOL
        i.turnRunning -> ReactorState.THINKING
        else -> ReactorState.IDLE
    }
    return Derived(state, goal = i.goalActive && !state.still)
}
