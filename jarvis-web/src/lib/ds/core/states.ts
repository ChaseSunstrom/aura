// The core's nine states, and how the console's eleven reactor states land on them.
//
// The reactor (v1) was drawn per pipeline state; the v2 core is drawn per what
// JARVIS is doing. Seven of the eleven have a v2 row of their own; four do not,
// and the mapping below says where each goes and why, as data the shell reads
// rather than a switch it re-types.

import type { AccentState } from '$lib/tokens';

export const CORE_STATES = [
	'idle',
	'listening',
	'speaking',
	'thinking',
	'embedding',
	'acting',
	'music',
	'alert',
	'working'
] as const;

export type CoreState = (typeof CORE_STATES)[number];

export function isCoreState(value: unknown): value is CoreState {
	return typeof value === 'string' && (CORE_STATES as readonly string[]).includes(value);
}

/**
 * Reactor state → core state.
 *
 * - `tool` → acting: a tool call inside a turn is the v2 "acting" (rings tilt
 *   and quicken, cyan).
 * - `goal` → working: a /goal is the long multi-step job the presence strip
 *   exists for; the core rises into the strip while it runs.
 * - `attention` → alert: something waits on the user (an approval). The amber
 *   leader line says which projection; the core only has to say "look".
 * - `warming` → embedding: the model is loading. Violet is the colour of
 *   memory, embeddings and models, and the embedding motion (points fading in
 *   and out of the shell, slow spin) reads as "coming up" without claiming a
 *   turn is being thought about, which `thinking` would.
 * - `kill` → alert: the kill switch has stopped everything; alert is the
 *   nearly-still pink core. The subtitle says why.
 * - `offline` → alert: JARVIS cannot hear the user. An idle core would claim
 *   it is ready, which is the one thing it must not say while offline.
 */
export const REACTOR_TO_CORE: Readonly<Record<AccentState, CoreState>> = {
	idle: 'idle',
	listening: 'listening',
	thinking: 'thinking',
	speaking: 'speaking',
	tool: 'acting',
	goal: 'working',
	attention: 'alert',
	warming: 'embedding',
	alert: 'alert',
	kill: 'alert',
	offline: 'alert'
};

/**
 * The core state for a reactor state name; anything unknown is idle. Own keys
 * only: `constructor` or `__proto__` from a payload must not come back as a
 * function where a state name was promised.
 */
export function coreStateFor(reactor: string): CoreState {
	return Object.hasOwn(REACTOR_TO_CORE, reactor) ? REACTOR_TO_CORE[reactor as AccentState] : 'idle';
}
