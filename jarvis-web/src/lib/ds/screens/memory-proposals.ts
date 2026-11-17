// What the assistant wants to remember and has put in a queue instead of storing (the
// `memory_propose` tool, T-1500), as the Approvals screen draws it: one card per waiting
// proposal with the fact whole, where it would be stored, who proposed it and what that turn
// had read, and the queue that sends Drop and asks for a Keep.
//
// A Drop is answered by the plugin and the card leaves on its answer. A Keep stores nothing
// from here: it sends the assistant one sentence naming the id, so the keep is the
// `memory_proposals` call the approval gate asks about, on a card that shows the fact whole.
// The card on this screen stays until the plugin says the queue changed.
//
// Does not cover: the keep itself or its card (the gate's, `ApprovalCard`); a keep the
// assistant does not make (nothing is kept, and the card here can be asked again once the turn
// is over); a pick of some but not all (Keep all names a cardful, Drop all what is shown; a
// card's own Keep and Drop do one). A proposal made in a routine's own process
// is announced by the gateway's five-second look at the queue (T-1506), and the screen reads
// the list each time it opens and each time the link comes back.

import { agoOf, stampOf } from '../../hud/data/time';

/** Where a kept fact goes, in the words a card uses (the table's `stores`). */
export const STORE_WORDS = { fact: 'the fact store', memory: 'your profile' } as const;

/** An id as the plugin makes one. It is written into a sentence the assistant reads, so nothing else passes. */
const ID = /^p\d{1,9}$/;
/** The plugin's time of a proposal: "2026-11-17 09:00 UTC". */
const AT = /^(\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC$/;
/** The most one keep names, so the gate's card shows each fact: the plugin's KEEP_BATCH (`keep_batch` in the table). */
export const KEEP_BATCH = 8;
/** Tools named on a card at most: what the plugin keeps per proposal. */
const READ_SHOWN = 4;

export interface MemoryCard {
	id: string;
	/** The fact, whole: what would be stored, character for character. */
	text: string;
	/** "the fact store" or "your profile". */
	where: string;
	/** "Proposed by a routine, after reading session_search". */
	source: string;
	/** The plugin's mark: the sentence reads as an instruction, not as a fact. */
	instruction: boolean;
	/** "3 hours ago"; '' when the plugin's time cannot be read. */
	when: string;
	/** The date and time, for the title of `when`; '' as `when`. */
	stamp: string;
}

/** A tool's or a platform's name by its plain characters, as the approval card shows one. */
const plain = (value: unknown): string =>
	String(value ?? '')
		.replace(/[^A-Za-z0-9_.:-]/g, '')
		.slice(0, 60);

function sourceOf(by: unknown, read: unknown): string {
	const name = plain(by);
	const who =
		by === 'conversation' ? 'in conversation' : by === 'cron' || by === 'a routine or message' || !name ? 'by a routine' : `by a routine or message (${name})`;
	const tools = (Array.isArray(read) ? read : []).map(plain).filter(Boolean).slice(0, READ_SHOWN);
	return `Proposed ${who}${tools.length ? `, after reading ${tools.join(', ')}` : ''}`;
}

/** The plugin's answer to `proposals` as cards. A row with no text, or an id that is not the plugin's, is dropped. */
export function memoryCards(answer: unknown, now = Date.now()): MemoryCard[] {
	const rows = answer && typeof answer === 'object' && !Array.isArray(answer) ? (answer as { open?: unknown }).open : null;
	if (!Array.isArray(rows)) return [];
	const cards: MemoryCard[] = [];
	for (const row of rows as (Record<string, unknown> | null)[]) {
		if (!row || typeof row !== 'object' || typeof row.id !== 'string' || !ID.test(row.id)) continue;
		if (typeof row.text !== 'string' || !row.text) continue;
		const at = typeof row.at === 'string' && AT.test(row.at) ? Date.parse(row.at.replace(AT, '$1T$2:00Z')) : NaN;
		cards.push({
			id: row.id,
			text: row.text,
			where: row.store === 'memory' ? STORE_WORDS.memory : STORE_WORDS.fact,
			source: sourceOf(row.by, row.read),
			instruction: row.worded_as_instruction === true,
			when: Number.isFinite(at) ? agoOf(at, now) : '',
			stamp: Number.isFinite(at) ? stampOf(at) : ''
		});
	}
	return cards;
}

/**
 * What a Keep sends the assistant. It names the tool and the ids and nothing a proposal says:
 * the facts reach the person on the approval card, drawn by the gate from the queue itself, and
 * several ids are one call, so one card shows each.
 */
export function keepSentence(ids: string | readonly string[]): string {
	const list = typeof ids === 'string' ? [ids] : [...ids];
	const named = list.length === 1 ? `proposal ${list[0]}` : `proposals ${list.join(', ')}`;
	return `Keep memory ${named}: call memory_proposals with action keep and ids ${JSON.stringify(list)}, then store what it returns exactly as written.`;
}

export interface MemoryIo {
	load: () => Promise<unknown>;
	drop: (id: string) => Promise<unknown>;
	/** Hand the assistant a sentence, as the screen's other asks do. */
	ask: (text: string) => void;
}

export interface MemoryView {
	/** Null until the plugin has answered. */
	cards: MemoryCard[] | null;
	/** Why the list could not be read. */
	error: string;
	/** Why the last Drop did not land. */
	failed: string;
	/** The proposal whose Drop is on the wire ('all' for a Drop all); nothing else is sent meanwhile. */
	acting: string;
	/** The proposals a Keep was asked for in this turn: their yes is on the approval card. */
	asked: readonly string[];
}

export const NO_MEMORY: MemoryView = { cards: null, error: '', failed: '', acting: '', asked: [] };

const reason = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** The list and its verbs. `show` is handed every new view. */
export function memoryQueue(io: MemoryIo, show: (view: MemoryView) => void) {
	let view = NO_MEMORY;
	// Each read and each answer takes a turn; a read that lands out of turn is dropped, or it
	// would draw a card the person has just dropped.
	let turn = 0;
	const set = (patch: Partial<MemoryView>): void => show((view = { ...view, ...patch }));

	/** Read the list. Quiet keeps what is drawn while it reads, and through a failed read. */
	async function read(quiet = false): Promise<void> {
		const mine = ++turn;
		if (!quiet) set({ cards: null, error: '', failed: '' });
		try {
			const cards = memoryCards(await io.load());
			if (mine !== turn) return;
			const here = new Set(cards.map((c) => c.id));
			set({ cards, error: '', asked: view.asked.filter((id) => here.has(id)) });
		} catch (e) {
			if (mine === turn && !quiet) set({ error: reason(e) });
		}
	}

	/** Drop one. The card leaves on the plugin's answer, never before it. */
	async function drop(id: string): Promise<void> {
		if (view.acting) return;
		set({ acting: id, failed: '' });
		try {
			await io.drop(id);
			turn++;
			set({ acting: '', cards: (view.cards ?? []).filter((c) => c.id !== id), asked: view.asked.filter((a) => a !== id) });
		} catch (e) {
			set({ acting: '', failed: `Couldn’t drop it: ${reason(e)}` });
			// It may be gone: kept or dropped from another app, or by asking.
			await read(true);
		}
	}

	/** Ask the assistant to keep one. Nothing is stored and the card stays: the yes is on the approval card. */
	function keep(id: string): void {
		if (view.acting || view.asked.includes(id) || !view.cards?.some((c) => c.id === id)) return;
		io.ask(keepSentence(id));
		set({ asked: [...view.asked, id] });
	}

	/** The ids a Keep all names: the first cardful not yet asked for, in the list's order. */
	const keepable = (): string[] =>
		(view.cards ?? [])
			.map((c) => c.id)
			.filter((id) => !view.asked.includes(id))
			.slice(0, KEEP_BATCH);

	/** Ask the assistant to keep a cardful in one call: one approval card shows each fact. */
	function keepAll(): void {
		const ids = keepable();
		if (view.acting || !ids.length) return;
		io.ask(keepSentence(ids));
		set({ asked: [...view.asked, ...ids] });
	}

	/** Drop every card shown, in one call: a proposal that arrived since is not dropped unseen. */
	async function dropAll(): Promise<void> {
		const ids = (view.cards ?? []).map((c) => c.id);
		if (view.acting || !ids.length) return;
		set({ acting: 'all', failed: '' });
		try {
			await io.drop(ids.join(' '));
			turn++;
			set({ acting: '', cards: (view.cards ?? []).filter((c) => !ids.includes(c.id)), asked: view.asked.filter((a) => !ids.includes(a)) });
		} catch (e) {
			set({ acting: '', failed: `Couldn’t drop them: ${reason(e)}` });
			await read(true);
		}
	}

	/** The plugin said the queue changed (`memory.proposals`): read it again, quietly, by the same turn rule. */
	const changed = (): Promise<void> => read(true);

	/** The turn that was asked is over. What still waits was not kept (denied, or never called): it can be asked again. */
	function settled(): Promise<void> {
		if (view.asked.length) set({ asked: [] });
		return read(true);
	}

	return { read, drop, keep, keepAll, dropAll, changed, settled, keepable };
}
