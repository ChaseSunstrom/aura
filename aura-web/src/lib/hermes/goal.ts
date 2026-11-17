// The goal's state, read off the sentence Hermes sends.
//
// Hermes reports its standing goal as one `status.update` event with
// `kind: "goal"` and a line of text whose first glyph is the state:
//
//   ⊙ Goal set (20-turn budget): <goal>
//   ↻ Continuing toward goal (3/20): <the judge's reason>
//   ⏳ Goal (parked …)
//   ⏸ Goal paused — 3/20 turns used …
//   ✓ Goal achieved: <the judge's reason>
//   Goal cleared
//
// The panel shows the state as a word and the reason VERBATIM — the judge's
// sentence is the one honest account of why the loop went round again, and
// paraphrasing it would be the console making something up.

export type GoalPhase =
	| 'none'
	| 'set'
	| 'continuing'
	| 'parked'
	| 'paused'
	| 'achieved'
	| 'cleared'
	| 'status';

export interface GoalState {
	phase: GoalPhase;
	/** The whole line, as sent. */
	text: string;
	/** What comes after the `: ` — the goal itself when set, the judge's reason otherwise. */
	reason: string;
	/** `n` of `n/N`, when the line carries one. */
	turn: number | null;
	/** `N` of `n/N` or `N-turn budget`, when the line carries one. */
	budget: number | null;
	/** Epoch ms of the event that produced this, for "as of" text. */
	at: number;
}

export const NO_GOAL: GoalState = {
	phase: 'none',
	text: '',
	reason: '',
	turn: null,
	budget: null,
	at: 0
};

const GLYPHS: Array<[string, GoalPhase]> = [
	['⊙', 'set'],
	['↻', 'continuing'],
	['⏳', 'parked'],
	['⏸', 'paused'],
	['✓', 'achieved'],
	['✔', 'achieved']
];

/** `(n/N)` or `n/N` anywhere in the line. */
const TURNS = /(\d+)\s*\/\s*(\d+)/;
/** `(N-turn budget)`. */
const BUDGET = /(\d+)-turn budget/;

/** The text after the first `: ` — the judge's reason, or the goal itself. */
function afterColon(text: string): string {
	const index = text.indexOf(': ');
	return index === -1 ? '' : text.slice(index + 2).trim();
}

export function parseGoalText(raw: string, at = Date.now()): GoalState {
	const text = String(raw ?? '').trim();
	if (!text) return { ...NO_GOAL, at };
	let phase: GoalPhase = 'status';
	for (const [glyph, name] of GLYPHS) {
		if (text.startsWith(glyph)) {
			phase = name;
			break;
		}
	}
	// `Goal cleared` from the loop, `✓ Goal cleared.` from `/goal clear`: both are
	// the goal being gone, whatever glyph the sentence wears.
	if (/^[^a-z]*goal cleared\b/i.test(text)) phase = 'cleared';
	if (phase === 'status' && /^no active goal\b/i.test(text)) phase = 'none';

	const turns = TURNS.exec(text);
	const budget = BUDGET.exec(text);
	let reason = afterColon(text);
	if (phase === 'parked' && !reason) {
		// `⏳ Goal (parked waiting on pid 4242)`: the bracket is the reason.
		const bracket = /\((parked[^)]*)\)/i.exec(text);
		reason = bracket ? bracket[1] : text.replace(/^⏳\s*/, '');
	}
	if (phase === 'paused' && !reason) {
		const dash = text.split(/\s[—–-]\s/);
		reason = dash.length > 1 ? dash.slice(1).join(' — ').trim() : '';
	}
	return {
		phase,
		text,
		reason,
		turn: turns ? Number(turns[1]) : null,
		budget: turns ? Number(turns[2]) : budget ? Number(budget[1]) : null,
		at
	};
}

/** The word the panel puts beside the glyph. */
export function goalPhaseLabel(state: GoalState): string {
	switch (state.phase) {
		case 'set':
			return 'SET';
		case 'continuing':
			return state.turn !== null && state.budget !== null
				? `CONTINUING · TURN ${state.turn}/${state.budget}`
				: 'CONTINUING';
		case 'parked':
			return 'PARKED';
		case 'paused':
			return state.turn !== null && state.budget !== null
				? `PAUSED · ${state.turn}/${state.budget} TURNS USED`
				: 'PAUSED';
		case 'achieved':
			return 'ACHIEVED';
		case 'cleared':
			return 'CLEARED';
		case 'status':
			return 'STATUS';
		default:
			return 'NO GOAL';
	}
}

/** Whether the loop is still going: a goal that is set, continuing or parked. */
export function goalIsLive(state: GoalState): boolean {
	return state.phase === 'set' || state.phase === 'continuing' || state.phase === 'parked';
}

/** The colour tone the panel's tag wears; semantic, never the accent for its own sake. */
export function goalTone(state: GoalState): 'neutral' | 'live' | 'ok' | 'warn' | 'danger' {
	switch (state.phase) {
		case 'set':
		case 'continuing':
			return 'live';
		case 'achieved':
			return 'ok';
		case 'parked':
		case 'paused':
			return 'warn';
		default:
			return 'neutral';
	}
}
