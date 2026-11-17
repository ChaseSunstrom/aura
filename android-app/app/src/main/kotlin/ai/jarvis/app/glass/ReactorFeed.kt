package ai.jarvis.app.glass

import ai.jarvis.app.assist.Phase
import ai.jarvis.app.assist.ToolRun
import ai.jarvis.app.assist.hermes.GoalStatus
import ai.jarvis.app.assist.hermes.HudState
import ai.jarvis.app.reactor.ReactorInputs

/**
 * What the phone knows, in the shape the reactor asks for (GLASS_HUD §10).
 *
 * `ReactorInputs` has eleven fields and `ReactorState.derive` turns them into
 * one of eleven states. Everything they need is already on this surface — in
 * [HudState], [ToolRun], [GoalStatus] and the conversation's own flags — but
 * scattered, because it was gathered for captions and lists rather than for an
 * instrument. This is the one place that gathers it.
 *
 * ### Why this exists rather than a change to the conversation
 *
 * [Phase] has five values and can express almost none of the eleven, so the
 * obvious move looks like widening it. It is the wrong move: the phase is the
 * conversation's own flag for barge-in and TTS gating, and what the reactor
 * shows is not the same thing — an approval waiting is attention whatever
 * the microphone is doing. Widening it would drag a redesign through an
 * `onMode` contract that seven classes implement and through ten branching
 * comparisons that gate whether a clip is allowed to play.
 *
 * So this reads the HUD's state a second way and hands the result to the
 * surface as `Ui.onReactor`, beside `onMode` rather than instead of it. The
 * conversation's control flow is not touched at all.
 *
 * Pure and Compose-free, so every rule below is pinned by a JVM test rather
 * than inferred from a running phone.
 */
object ReactorFeed {

    /**
     * Gather [hud] and the conversation's own flags into the reactor's inputs.
     *
     * The four parameters are the state that is not in [HudState] because it
     * is not a HUD event: whether the socket is up, what `GET /api/status` last
     * said about the model, whether the TTS player is playing, and whether the
     * microphone is open. The conversation owns all four already.
     */
    fun inputsFrom(
        hud: HudState,
        linkUp: Boolean = true,
        warming: Boolean = false,
        speaking: Boolean = false,
        listening: Boolean = false,
        kill: Boolean = false,
    ): ReactorInputs = ReactorInputs(
        // Phase 7. False everywhere until the kill switch exists, on this
        // surface and on the web; a parameter rather than a literal so the
        // day it arrives is a call-site change and not a change here.
        kill = kill,
        // Deliberately NOT `hud.offline`. `linkUp` is the socket; `offline` is
        // the adapter reporting that it cannot reach Hermes through a socket
        // that is perfectly up. Folding the two together is what made §10's
        // alert row unreachable on the web — an adapter error mid-turn could
        // only ever read as "offline", never as the alert it is.
        linkUp = linkUp,
        adapterError = hud.offline,
        turnRunning = hud.turnPending,
        // The string is the message; its presence is the fact. An empty
        // `lastError` is "no error", which is how a new turn clears it.
        errorEvent = hud.lastError.isNotEmpty(),
        attention = attentionFrom(hud),
        warming = warming,
        speaking = speaking,
        listening = listening,
        toolsRunning = hud.tools.rows.count { it.state == ToolRun.State.RUNNING },
        goalActive = hud.goal?.state in GOAL_ACTIVE,
    )

    /**
     * The inputs a surface with no HUD state can offer: the phase alone.
     *
     * The pipeline engine (jarvis-core) streams no tool, approval or goal
     * events, and the enrolment screen, the companion's question card and
     * the car have no conversation state at all beyond the phase they are
     * shown. So the phase maps onto the four inputs it can speak for and the
     * rest keep their resting values: LISTENING opens the microphone,
     * THINKING is a running turn with no token yet, SPEAKING is the reply
     * playing, ERROR is the turn's own error, IDLE is nothing.
     *
     * [linkUp] and [warming] are separate because they are not phases: a
     * surface that knows its socket is down, or that the model is loading,
     * says so here and the precedence rule ranks them (offline above every
     * phase, warming above speaking).
     */
    fun inputsFrom(phase: Phase, linkUp: Boolean = true, warming: Boolean = false): ReactorInputs = ReactorInputs(
        linkUp = linkUp,
        turnRunning = phase == Phase.THINKING,
        errorEvent = phase == Phase.ERROR,
        warming = warming,
        speaking = phase == Phase.SPEAKING,
        listening = phase == Phase.LISTENING,
    )

    /**
     * Something is waiting on the person: an approval, a clarifying question,
     * or a goal parked until they answer.
     *
     * A parked goal counts here *and* in [ReactorInputs.goalActive], which is
     * not a mistake: it is still the goal, and it is also the thing asking. The
     * eleven-state table resolves the overlap by ranking attention above goal,
     * so a parked goal reads as needing the person rather than as running.
     */
    private fun attentionFrom(hud: HudState): Boolean =
        hud.approvals.isNotEmpty() ||
            hud.clarifies.isNotEmpty() ||
            hud.goal?.state == GoalStatus.State.PARKED

    /**
     * The goal states that mean a goal is under way.
     *
     * PAUSED, ACHIEVED, CLEARED and UNKNOWN are not: a goal the person paused
     * is not running, and one that is finished or absent is not a goal. Naming
     * the three that count rather than excluding the four that do not is what
     * makes a state added to [GoalStatus] default to "not running" instead of
     * silently lighting the reactor.
     */
    private val GOAL_ACTIVE = setOf(
        GoalStatus.State.SET,
        GoalStatus.State.CONTINUING,
        GoalStatus.State.PARKED,
    )
}
