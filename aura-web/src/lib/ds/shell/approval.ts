// What the approval card shows for one open request, from whichever link raised it.
//
// `hud/data/approvals.ts` already merges Hermes approvals and questions with the
// house's held calls and the phone's prompts into one list whose ids say which
// link answers. This adds what the v2 card needs on top (Control.dc.html): the
// card's what / why / impact (`approvalCard.ts`), whether the yes must be held
// (an action that cannot be undone), and the "Next time" rule the person can
// pick, in the board's words rather than Hermes's choice names.
//
// Only a Hermes approval's card is the server's: the approval hook appends it
// last, after anything the model wrote. A house or phone request's description
// is the requester's own text, which the model may write, so card lines at its
// end are never read: a forged "Undoable: yes" there made Approve one click and
// a forged "What:" retitled a text send as a weather check. Without a server
// card a request is held, and its parameters are shown at once (the phone reads
// it the same way, 7405ba47).
//
// A settings change's card also says the value now and the value it would become
// (T-1317): three lines the gate writes directly before its card, read only for a
// settings tool, and never drawn for a secret. They add rows; they change neither
// the yes nor who may give it.

import type { HudState } from '$lib/hermes/hud';
import type { ApprovalChoice } from '$lib/hermes/protocol';
import type { PendingApproval } from '$lib/jarvisClient';
import { CHANGE_TOOLS, parseApprovalCard, parseApprovalChange, reasonWithoutCard } from '$lib/hud/approvalCard';
import { secretChange } from '../screens/settings-proposals';
import { approvalRequests, sourceOf, type ApprovalSource } from '$lib/hud/data/approvals';

/** The board's words for Hermes's approval scopes. */
export const RULE_LABEL: Readonly<Record<Exclude<ApprovalChoice, 'deny'>, string>> = {
	once: 'Ask every time',
	session: 'Allow for this task',
	always: 'Allow when I’m home'
};

/**
 * "Ask why": a no, then this line to the model. The sentence, the template and the cap are
 * `tests/contracts/approval-event.json` `ask_why` (T-1054), which `approval.test.ts` reads and the
 * phone's `AskWhy.kt` is held to as well.
 */
export const ASK_WHY = 'Before asking again, say why you need this.';
export const ASK_WHY_WHAT_CAP = 200;

/** The headline is the server's text, so what the model reads of it is bounded. */
export const askWhyLine = (what: string, why: string): string =>
	`You asked: “${what.trim().slice(0, ASK_WHY_WHAT_CAP)}”. I said no for now. ${why}`;

export interface Rule {
	choice: Exclude<ApprovalChoice, 'deny'>;
	label: string;
}

export interface Ask {
	/** The merged list's id (`hermes:`, `core:`, `phone:`): what the answer is routed by. */
	id: string;
	source: ApprovalSource;
	kind: 'approval' | 'clarify';
	/** The question, or what would happen. */
	what: string;
	/** The tool or action that would run, shown beside the headline so a card's words cannot hide it. */
	action?: string;
	/** True only when what / why / impact are a server-written card; otherwise the detail is shown unfolded. */
	carded: boolean;
	why?: string;
	impact?: string;
	/** A settings change: the value now and the value it would become; both '' for a secret, which draws neither. */
	change?: { key: string; secret: boolean; before: string; after: string };
	/** What "Show me first" opens: the command, its arguments, the reason's own words. */
	detail?: string;
	/** False: the yes is a hold (`HoldButton`), because it cannot be taken back. */
	undoable: boolean;
	/** The yes is the phone's fingerprint (a sensitive ask): this screen can only say no. */
	onPhone?: boolean;
	/** The "Next time" choices; empty when the link only takes a yes or a no. */
	rules: Rule[];
	/** A question's offered answers. */
	options: string[];
	at?: string;
}

/**
 * The rules a request may offer, in the board's order.
 *
 * Only the scopes Hermes sent are offered: a request whose choices leave out
 * `always` (a tool the matrix never lets stand) shows no "always" chip. And
 * "Allow when I'm home" needs the card's `Home: yes`: without it the standing
 * yes would not be keyed to anything the person was told about.
 */
export function rulesFor(choices: readonly ApprovalChoice[], home: boolean): Rule[] {
	const out: Rule[] = [];
	for (const choice of ['once', 'session', 'always'] as const) {
		if (!choices.includes(choice)) continue;
		if (choice === 'always' && !home) continue;
		out.push({ choice, label: RULE_LABEL[choice] });
	}
	return out;
}

export type Risk = 'low' | 'irreversible' | 'phone';

/** The chip's level: only a server card's `Undoable: yes` is low (an uncarded ask is held, `undoable: false`). A question has none. */
export function riskOf(ask: Ask): Risk | undefined {
	if (ask.kind === 'clarify') return undefined;
	if (ask.onPhone) return 'phone';
	return ask.undoable ? 'low' : 'irreversible';
}

/**
 * The ids "Approve all" may answer: two or more asks, every one an approval that can be undone
 * and needs no phone signature. Any other ask in the queue empties it, so a batch never carries
 * a hold, a question or a fingerprint along.
 */
export function batchable(asks: readonly Ask[]): string[] {
	const ok = asks.length > 1 && asks.every((a) => a.kind === 'approval' && a.undoable && !a.onPhone);
	return ok ? asks.map((a) => a.id) : [];
}

/** Every open request as the card draws it, Hermes's first. */
export function asksFrom(hud: HudState | null | undefined, pending: readonly PendingApproval[] = []): Ask[] {
	const hermes = new Map((hud?.approvals ?? []).map((a) => [a.requestId, a]));
	// A batch's questions share a request id; each is told apart by its own.
	const qkey = (requestId: string, questionId?: string) => (questionId ? `${requestId}#${questionId}` : requestId);
	const clarify = new Map((hud?.clarifies ?? []).map((c) => [qkey(c.requestId, c.questionId), c]));
	const house = new Map(pending.map((p) => [String(p.request_id ?? p.id ?? ''), p]));
	return approvalRequests(hud, pending).map((row): Ask => {
		const { source, requestId, questionId } = sourceOf(row.id);
		// Held and unfolded unless a server card says otherwise.
		const base = { id: row.id, source, kind: row.kind, what: row.title, action: row.tool, carded: false, at: row.at, undoable: false, rules: [], options: [] };
		if (source === 'hermes' && row.kind === 'approval') {
			const a = hermes.get(requestId);
			if (!a) return { ...base, detail: row.detail };
			const card = parseApprovalCard(a.reason);
			// The phone's yes is for this one call (T-1030): no "Next time" rule could apply to it.
			const onPhone = a.onPhone || undefined;
			// No card: the title is the whole reason already, and no `Home: yes` was said.
			if (!card) return { ...base, onPhone, detail: a.detail || undefined, rules: onPhone ? [] : rulesFor(a.choices, false) };
			const asked = CHANGE_TOOLS.has(a.tool) ? parseApprovalChange(a.reason) : null;
			const secret = !!asked && secretChange(asked.key, asked.before, asked.after, false);
			// The rows replace the three lines; a secret's lines (a mask, or a value the gate failed to mask) go nowhere.
			const said = reasonWithoutCard(a.reason, !!asked);
			return {
				...base,
				carded: true,
				onPhone,
				what: card.what,
				why: card.why,
				impact: card.impact,
				change: asked ? { key: asked.key, secret, before: secret ? '' : asked.before, after: secret ? '' : asked.after } : undefined,
				undoable: card.undoable,
				detail: [a.detail, said].filter(Boolean).join('\n') || undefined,
				rules: onPhone ? [] : rulesFor(a.choices, card.home)
			};
		}
		if (row.kind === 'clarify') {
			const options = source === 'hermes' ? (clarify.get(qkey(requestId, questionId))?.options ?? []) : (house.get(requestId)?.choices ?? []);
			return { ...base, options: [...options] };
		}
		return { ...base, detail: row.detail };
	});
}

/** A standing "always" answer still in force, as the guard's `grants` lists it (times in epoch s). */
export interface Grant {
	tool: string;
	base_key: string;
	scopes: string[];
	granted_at: number;
	expires_at: number;
}

/**
 * The guard's `grants` output as rows. Anything else it answers (the refusal a guest's or a
 * member's session gets) is thrown as the reason, so the screen says it instead of showing an
 * empty list as if nothing stood.
 */
export function grantsFrom(output: string): Grant[] {
	const text = output.trim();
	if (!text.startsWith('[')) throw new Error(text || 'the guard did not answer');
	return (JSON.parse(text) as Grant[]).filter((g) => typeof g?.base_key === 'string' && g.base_key);
}
