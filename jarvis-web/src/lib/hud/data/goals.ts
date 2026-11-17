// `panel.goals` from the reducer's one-line goal state.
//
// Hermes reports the goal as a status line — `⊙ Goal set: keep the study at
// 21°`, `↻ Goal continuing (3/20): the room is 19°` — and the reducer keeps the
// latest one. The panel wants the goal's words and the judge's words apart:
// on a `set` line the text after the colon IS the goal; on every later line it
// is the judge's reason, and the goal itself was on a line we no longer hold.
// So the last `set` line's words are carried in by the host as `title` when it
// has seen one, and the mapping falls back to the line itself.

import { type GoalState } from '$lib/hermes/goal';
import type { Goal, RunState } from '$lib/glass';

function runStateOf(goal: GoalState): RunState {
	switch (goal.phase) {
		case 'set':
		case 'continuing':
			return 'running';
		case 'parked':
		case 'paused':
			return 'held';
		case 'achieved':
			return 'done';
		default:
			return 'queued';
	}
}

/** The line without its glyph, for a title when nothing better is known. */
function words(goal: GoalState): string {
	return goal.text.replace(/^[^\p{L}\p{N}]+/u, '').trim();
}

/**
 * The goal as the panel draws it, or null when there is none: `none` and
 * `cleared` are the panel's empty state, not a goal called "cleared".
 */
export function goalView(goal: GoalState | null | undefined, title = ''): Goal | null {
	if (!goal || goal.phase === 'none' || goal.phase === 'cleared') return null;
	// `⊙ Goal set: keep the study at 21° (20-turn budget)`: the budget is the
	// panel's "of 20", not part of the goal's words.
	const own = (goal.phase === 'set' ? goal.reason || words(goal) : title || words(goal))
		.replace(/\s*\(\d+-turn budget\)\s*$/, '')
		.trim();
	const view: Goal = { id: 'goal', title: own, state: runStateOf(goal) };
	if (goal.turn !== null) view.turn = goal.turn;
	if (goal.budget !== null) view.turns = goal.budget;
	if (goal.phase !== 'set' && goal.reason) view.reason = goal.reason;
	return view;
}
