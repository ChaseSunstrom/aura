// What the reactor is doing, folded from everything the console knows.
//
// Two layers, deliberately separate. `derive` is the contract's truth table
// (`tests/contracts/reactor.json` `derivation`) over eleven plain booleans and
// one count — the same branch order the phone's `ReactorState.kt` and the
// console's `reactor.py` run, so an approval waiting in the browser cannot read
// as "thinking" while the phone reads "needs you". `inputsFrom` is the console's
// half: which of this app's many objects answers each of those booleans. Keeping
// them apart is what lets the truth table be tested without a Hermes link, and
// what stops a new event source quietly changing the precedence.

import { goalIsLive, type GoalState } from '$lib/hermes/goal';
import type { HudState } from '$lib/hermes/hud';
import { isWarmingUp } from '$lib/hermes/status';
import type { HermesStatus } from '$lib/hermes/client';
import type { StatusReport } from '$lib/hermes/protocol';
import { PRECEDENCE, WORDS, type ReactorStateName } from './spec';

/** Everything a surface knows that bears on the reactor, at the contract's rest. */
export interface ReactorInputs {
	/** The kill switch is engaged: `GET /api/status` says so. */
	kill: boolean;
	/** The HUD socket is up and the adapter has not said the backend is down. */
	linkUp: boolean;
	/** An `adapter.error` arrived and nothing has cleared it. */
	adapterError: boolean;
	/** From `prompt.submit` to `message.complete`. */
	turnRunning: boolean;
	/** The turn's own error, or an `error` event, not cleared by a new turn. */
	errorEvent: boolean;
	/** An approval or a clarifying question is waiting, or the goal is parked. */
	attention: boolean;
	/** `GET /api/status` says the model is starting. */
	warming: boolean;
	/** TTS is playing. */
	speaking: boolean;
	/** The microphone is open for a turn. */
	listening: boolean;
	/** `tool.start` events without their `tool.complete`, this turn. */
	toolsRunning: number;
	/** A goal is set, continuing or parked. */
	goalActive: boolean;
}

/** The contract's `derivation.rest`: a caller names only what is true. */
export const REST: ReactorInputs = {
	kill: false,
	linkUp: true,
	adapterError: false,
	turnRunning: false,
	errorEvent: false,
	attention: false,
	warming: false,
	speaking: false,
	listening: false,
	toolsRunning: 0,
	goalActive: false
};

/** What the reactor wears, and whether the goal satellite orbits it. */
export interface Derived {
	state: ReactorStateName;
	goal: boolean;
}

/**
 * The precedence rule, one branch per row of the contract's `derivation.rules`,
 * in the contract's order.
 *
 * Two of the rows carry a judgement worth stating. An `adapter.error` is an
 * *alert* while a turn is running and *offline* at rest: mid-turn something was
 * lost and the person needs to know, at rest nothing was and the honest reading
 * is that the link is down. And the goal overlay is dropped on the two still
 * states, because a dot orbiting a dead reactor would say the goal is still
 * being worked by a link that is gone.
 */
export function derive(inputs: Partial<ReactorInputs> = {}): Derived {
	const i: ReactorInputs = { ...REST, ...inputs };
	const state: ReactorStateName = i.kill
		? 'kill'
		: !i.linkUp || (i.adapterError && !i.turnRunning)
			? 'offline'
			: i.errorEvent || (i.adapterError && i.turnRunning)
				? 'alert'
				: i.attention
					? 'attention'
					: i.warming
						? 'warming'
						: i.speaking
							? 'speaking'
							: i.listening
								? 'listening'
								: i.toolsRunning > 0
									? 'tool'
									: i.turnRunning
										? 'thinking'
										: 'idle';
	return { state, goal: i.goalActive && state !== 'kill' && state !== 'offline' };
}

/** Which of two states the precedence table puts first. Lower is higher. */
export const rankOf = (state: ReactorStateName): number => {
	const at = PRECEDENCE.indexOf(state);
	// `goal` is not in the table: it is the overlay, and ranking it would let it
	// win a comparison it can never win in `derive`.
	return at < 0 ? PRECEDENCE.length : at;
};

/** The state word, for the tray, the notification and the console strip. */
export const wordOf = (state: ReactorStateName): string => WORDS[state];

/** The live sources the console folds into the inputs. */
export interface Sources {
	/** The HUD reducer's state: the turn, its tools, the approvals, the goal. */
	hud?: HudState | null;
	/** The socket's own status, from `HermesSnapshot`. */
	link?: HermesStatus | null;
	/** The adapter's last `GET /api/status`, for the cold start. */
	report?: StatusReport | null;
	/** TTS is playing: the speech queue's `onActive`. */
	speaking?: boolean;
	/** Push-to-talk is held, or the wake word opened the microphone. */
	recording?: boolean;
	/**
	 * The microphone was released and the words are on their way to a prompt:
	 * thinking from the release itself, so the reactor acknowledges within a
	 * frame rather than resting through the transcriber's round trip (voi-08).
	 */
	transcribing?: boolean;
	/**
	 * The kill switch, forced. Normally it comes from `report.kill_switch`
	 * below; a caller passes it only to render the state deliberately (the
	 * styleguide, a screenshot golden).
	 */
	kill?: boolean;
}

/**
 * The console's answer to each of the contract's inputs.
 *
 * `warming` is the one that is not simply read: `/api/status` reports the model
 * as starting whenever llama-swap has it unloaded, which at rest is most of the
 * time — a reactor that sat in "warming up" all evening would say the assistant
 * was busy when it was idle. §10 row 8 is a *cold start*, so warming counts only
 * while a turn is waiting on it.
 */
export function inputsFrom(sources: Sources): ReactorInputs {
	const hud = sources.hud ?? null;
	const turnRunning = Boolean(hud?.turn.running);
	const tools = hud?.turn.tools ?? [];
	const goal: GoalState | undefined = hud?.goal;
	const starting = sources.link === 'idle' || sources.link === 'connecting';
	return {
		// `??`, not `||`: an explicitly-passed `false` is a caller saying "render
		// this without the switch", and must not be overridden by the report.
		kill: sources.kill ?? Boolean(sources.report?.kill_switch?.engaged),
		// The socket, and only the socket. The adapter's own "I cannot reach
		// Hermes" is `adapterError` beside it, because the two have to stay
		// separable: folding backendDown in here would make `!linkUp` fire first
		// and an adapter error mid-turn could never reach the alert branch.
		// The first connect counts as up, and reads as warming below: the client is
		// `idle` then `connecting` only until its first answer (a lost link is
		// `reconnecting`, a refused one `offline`), and every page load opened on the
		// offline core for that moment (T-0671). Not idle: it is not ready yet.
		linkUp: sources.link === 'connected' || starting,
		adapterError: Boolean(hud?.backendDown),
		// Not `warming` below: that waits on the model, and a transcription does not.
		turnRunning: turnRunning || Boolean(sources.transcribing),
		errorEvent: Boolean(hud?.turn.error) || (Boolean(hud?.error) && !hud?.backendDown),
		attention:
			(hud?.approvals.length ?? 0) > 0 ||
			(hud?.clarifies.length ?? 0) > 0 ||
			goal?.phase === 'parked',
		warming: (isWarmingUp(sources.report) && turnRunning) || starting,
		speaking: Boolean(sources.speaking),
		listening: Boolean(sources.recording),
		toolsRunning: tools.filter((t) => t.state === 'running').length,
		goalActive: goal ? goalIsLive(goal) : false
	};
}

/** The reactor's state straight from the live sources: the two halves, joined. */
export const stateFrom = (sources: Sources): Derived => derive(inputsFrom(sources));

/**
 * How many segments the tool ring shows and how many are lit: every tool call
 * this turn, in call order, the running ones lit. A segment holds until its
 * `tool.complete` (§10 row 5), which is why `done` still counts toward the
 * total — the ring is a record of the turn, not a gauge of what is left.
 */
export function segmentsOf(hud: HudState | null | undefined): { segments: number; lit: number } {
	const tools = hud?.turn.tools ?? [];
	return { segments: tools.length, lit: tools.filter((t) => t.state === 'running').length };
}

/**
 * The cold start's progress, 0–1, when the adapter gives one; null when it does
 * not, and the renderer ramps on the clock instead. The adapter reports no
 * figure today, so this is the seam rather than a guess dressed as a reading.
 */
export function warmingProgress(report: StatusReport | null | undefined): number | null {
	// `StatusReport['model']` has no `progress`, because the adapter does not
	// send one yet. Read it off the object rather than widening the protocol for
	// a field nothing produces: when the adapter grows one, the type grows and
	// this cast comes out.
	const raw = (report?.model as { progress?: unknown } | undefined)?.progress;
	if (typeof raw !== 'number' || !Number.isFinite(raw)) return null;
	return Math.min(1, Math.max(0, raw > 1 ? raw / 100 : raw));
}
