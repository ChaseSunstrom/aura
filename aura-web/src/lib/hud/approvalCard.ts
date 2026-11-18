// The approval card's fields, read back out of the approval reason (T-0606).
//
// Hermes' approval event has no slot but the reason string, so the Hermes side
// (`hermes/plugins/_shared/_messages.py` `card`) writes the card as the LAST FIVE
// LINES of the reason and this reads them. The cases are
// `tests/contracts/approval-event.json` `card`, which the Python parse reads too.
// No match is `null`: show the plain reason, as before. A settings change adds three
// lines directly before those five (T-1317), read by `parseApprovalChange`.

export interface ApprovalCard {
	what: string;
	why: string;
	impact: string;
	/** `false` is hold-to-confirm. */
	undoable: boolean;
	/** The owner is home now, so the "allow when I'm home" chip may be offered. */
	home: boolean;
}

const CARD =
	/\nWhat: ([^\n]+)\nWhy: ([^\n]+)\nImpact: ([^\n]+)\nUndoable: (yes|no)\nHome: (yes|no)\s*$/;

export function parseApprovalCard(reason: string): ApprovalCard | null {
	const m = CARD.exec(reason || '');
	if (!m) return null;
	return { what: m[1], why: m[2], impact: m[3], undoable: m[4] === 'yes', home: m[5] === 'yes' };
}

/**
 * The tools whose approval may carry a change, the value now and the value it becomes (T-1317,
 * T-1329). Any other tool's text that happens to end in the three lines is prose: its arguments
 * are outside text.
 */
export const CHANGE_TOOLS: ReadonlySet<string> = new Set([
	'settings_set',
	'settings_undo',
	'chat_model_set',
	'device_name',
	'model_settings',
	'goal_pause',
	'goal_resume',
	'house_ha_confirm'
]);

// The three lines the gate writes directly before the card (`_messages.change`); the cases are
// the contract's `change`. Anywhere else in the reason they do not match.
const CHANGE = new RegExp('\\nSetting: ([^\\n]+)\\nNow: ([^\\n]+)\\nBecomes: ([^\\n]+)(?=' + CARD.source + ')');

/** The value now and the value asked for, as the gate worded them; a secret's are both its mask. */
export function parseApprovalChange(reason: string): { key: string; before: string; after: string } | null {
	const m = CHANGE.exec(reason || '');
	return m ? { key: m[1], before: m[2], after: m[3] } : null;
}

/**
 * The reason without the card lines, for the "Show me first" detail. `change` also drops the
 * three change lines: only for a card that draws them as rows, or they would be hidden.
 */
export function reasonWithoutCard(reason: string, change = false): string {
	return (change ? (reason || '').replace(CHANGE, '') : reason || '').replace(CARD, '').trim();
}
