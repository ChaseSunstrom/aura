// The settings changes that wait on a yes (the aura-settings plugin's proposals), as the
// Approvals screen draws them: one card per proposal with the value now and the value it
// would become, and the queue that sends Apply or Discard and drops the card once the
// plugin has answered.
//
// A secret's value never reaches a card. The plugin masks one before it leaves the store,
// and this does not rely on that: secret-ness is read from the console's own copy of the
// schema, and a key that copy does not name is treated as a secret too, because the
// console cannot tell what it would be drawing.
//
// A proposal made, applied or discarded elsewhere reaches an open screen as the plugin's
// `settings.proposals {waiting}` (a count, never a value): the screen then reads the list again
// (T-1322). Does not cover: an event missed while the link was down (the reconnect read has
// it); a change asked for in words, which goes
// through the approval card and is never a proposal (that card draws the same two rows
// from its own text, and asks `secretChange` here what it may show: T-1317). The changes
// already made are here too, as the rows of Recent changes on the Settings screen (T-0700):
// they share the secret rule and the wording of a value. Putting one back is one level deep
// (the plugin keeps the last value of a key, not a stack).

import { APPLIES_WORDS, settingsCatalogue } from '../../hud/data/settings';
import { agoOf, stampOf } from '../../hud/data/time';

const SPECS = new Map(settingsCatalogue().specs.map((s) => [s.key, s]));
/** What a secret reads as once masked: the plugin's word, and the mock gateway's. */
const MASKS = new Set(['(secret)', '[redacted]']);

export interface ProposalCard {
	id: string;
	key: string;
	/** The schema's sentence for the setting; '' for a key the console does not know. */
	what: string;
	/** Draw neither value: say only that it changes. */
	secret: boolean;
	before: string;
	after: string;
	/** "applies now", "after a restart"; '' when the plugin said something else. */
	applies: string;
}

/**
 * Must a change to `key` draw neither value? The console's schema says so, or either value is a
 * mask. `unknown` is the answer for a key that schema does not name: a proposal's values come
 * straight from the store, so it is a secret; an approval's were written by the gate, which has
 * masked a secret itself and also asks about house keys this schema never lists.
 */
export function secretChange(key: string, before: unknown, after: unknown, unknown: boolean): boolean {
	const spec = SPECS.get(key);
	return (spec ? spec.secret === true : unknown) || MASKS.has(before as string) || MASKS.has(after as string);
}

/** A value as a person reads it. */
function words(value: unknown): string {
	if (value === null || value === undefined || value === '') return 'not set';
	if (typeof value === 'boolean') return value ? 'on' : 'off';
	if (Array.isArray(value)) return value.length ? value.map(String).join(', ') : 'none';
	return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

/** The plugin's answer to `proposals` as cards. A row with no id or key is dropped: nothing could be sent for it. */
export function proposalCards(answer: unknown): ProposalCard[] {
	if (!Array.isArray(answer)) return [];
	const cards: ProposalCard[] = [];
	for (const row of answer as Record<string, unknown>[]) {
		if (!row || typeof row.id !== 'string' || typeof row.key !== 'string' || !row.id || !row.key) continue;
		const spec = SPECS.get(row.key);
		const secret = secretChange(row.key, row.before, row.after, true);
		cards.push({
			id: row.id,
			key: row.key,
			what: spec?.description ?? '',
			secret,
			before: secret ? '' : words(row.before),
			after: secret ? '' : words(row.after),
			applies: APPLIES_WORDS[row.applies as keyof typeof APPLIES_WORDS] ?? ''
		});
	}
	return cards;
}

/** One row of Recent changes on the Settings screen (T-0700). */
export interface ChangeRow {
	key: string;
	/** "5 minutes ago"; '' when the plugin's time cannot be read. */
	when: string;
	/** The date and time, for the row's title; '' as `when`. */
	stamp: string;
	/** Who made it: the assistant's name, "An app", or the plugin's own word for anything else. */
	who: string;
	secret: boolean;
	before: string;
	after: string;
	/** Draw Put back: the newest change of a key the plugin can put back, and never a secret. */
	undoable: boolean;
}

/**
 * The plugin's answer to `history` as rows, newest first as it sent them. A row with no key is
 * dropped. The plugin marks what `undo` can put back; this only ever narrows that: a secret
 * (by `secretChange`, so by the console's own schema too) is never offered, whatever was sent.
 */
export function changeRows(answer: unknown, assistant: string, now = Date.now()): ChangeRow[] {
	if (!Array.isArray(answer)) return [];
	const rows: ChangeRow[] = [];
	for (const row of answer as Record<string, unknown>[]) {
		if (!row || typeof row.key !== 'string' || !row.key) continue;
		const secret = secretChange(row.key, row.before, row.after, true);
		const at = Date.parse(String(row.ts ?? ''));
		rows.push({
			key: row.key,
			when: Number.isFinite(at) ? agoOf(at, now) : '',
			stamp: stampOf(at),
			who: row.actor === 'tool' ? assistant : row.actor === 'command' ? 'An app' : String(row.actor || 'Unknown'),
			secret,
			before: secret ? '' : words(row.before),
			after: secret ? '' : words(row.after),
			undoable: row.undoable === true && !secret
		});
	}
	return rows;
}

/** What to say once `undo` has answered: a security setting is not put back, it waits on a yes. */
export function undoSaid(key: string, answer: unknown): string {
	const proposed = (answer as { status?: unknown } | null)?.status === 'proposed';
	return proposed ? `Putting ${key} back is waiting for your OK under Approvals.` : `Put ${key} back.`;
}

export interface ProposalsIo {
	load: () => Promise<unknown>;
	apply: (id: string) => Promise<unknown>;
	discard: (id: string) => Promise<unknown>;
}

export interface ProposalsView {
	/** Null until the plugin has answered. */
	cards: ProposalCard[] | null;
	/** Why the list could not be read. */
	error: string;
	/** Why the last Apply or Discard did not land. */
	failed: string;
	/** The proposal whose answer is on the wire; nothing else is sent meanwhile. */
	acting: string;
}

export const NO_PROPOSALS: ProposalsView = { cards: null, error: '', failed: '', acting: '' };

const reason = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** The list and its two verbs. `show` is handed every new view. */
export function proposalsQueue(io: ProposalsIo, show: (view: ProposalsView) => void) {
	let view = NO_PROPOSALS;
	// Each read and each answer takes a turn; a read that lands out of turn is dropped, or it
	// would draw a card the person has just answered.
	let turn = 0;
	const set = (patch: Partial<ProposalsView>): void => show((view = { ...view, ...patch }));

	/** Read the list. Quiet keeps what is drawn while it reads, and through a failed read. */
	async function read(quiet = false): Promise<void> {
		const mine = ++turn;
		if (!quiet) set({ cards: null, error: '', failed: '' });
		try {
			const cards = proposalCards(await io.load());
			if (mine === turn) set({ cards, error: '' });
		} catch (e) {
			if (mine === turn && !quiet) set({ error: reason(e) });
		}
	}

	/** Send the person's answer. The card leaves on the plugin's answer, never before it. */
	async function act(id: string, verb: 'apply' | 'discard'): Promise<void> {
		if (view.acting) return;
		const key = view.cards?.find((c) => c.id === id)?.key ?? 'that change';
		set({ acting: id, failed: '' });
		try {
			await io[verb](id);
			turn++;
			set({ acting: '', cards: (view.cards ?? []).filter((c) => c.id !== id) });
		} catch (e) {
			set({ acting: '', failed: `Couldn’t ${verb} ${key}: ${reason(e)}` });
			// It may be gone: answered from another app, or its setting can no longer be written.
			await read(true);
		}
	}

	/** The plugin said the list changed (`settings.proposals`): read it again, quietly, by the same turn rule. */
	const changed = (): Promise<void> => read(true);

	return { read, act, changed };
}
