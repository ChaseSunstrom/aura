import { describe, expect, it, vi } from 'vitest';
import type { SettingsProposal } from '$lib/hermes/settings';
import { changeRows, proposalCards, proposalsQueue, undoSaid, type ProposalsView } from './settings-proposals';

const proposal = (over: Partial<SettingsProposal> = {}): SettingsProposal => ({
	id: 'a1b2c3',
	key: 'approvals.mode',
	before: 'smart',
	after: 'manual',
	source: 'config',
	applies: 'now',
	ts: '2026-11-13T10:00:00Z',
	...over
});
const card = (over: Partial<SettingsProposal> = {}) => proposalCards([proposal(over)])[0];

describe('a waiting settings change, as its card', () => {
	it('says the key, what it is, both values and when it applies', () => {
		const c = card();
		expect(c).toMatchObject({ id: 'a1b2c3', key: 'approvals.mode', secret: false, before: 'smart', after: 'manual', applies: 'applies now' });
		expect(c.what).toMatch(/approval door/);
		expect(card({ applies: 'restart' }).applies).toBe('after a restart');
	});

	it('puts a value in words a person reads', () => {
		expect(card({ key: 'hud.car_speak_directions', before: true, after: false })).toMatchObject({ before: 'on', after: 'off' });
		expect(card({ key: 'approvals.always_ttl_days', before: 30, after: 7 })).toMatchObject({ before: '30', after: '7' });
		expect(card({ before: '', after: null })).toMatchObject({ before: 'not set', after: 'not set' });
		expect(card({ before: [], after: ['web', 'browser'] })).toMatchObject({ before: 'none', after: 'web, browser' });
		expect(card({ before: { a: 1 }, after: {} }).before).toBe('{"a":1}');
	});

	it('never carries a secret’s value, whatever the plugin sent', () => {
		// The plugin masks a secret; a clear one here is the plugin failing, and the card still must not draw it.
		const c = card({ key: 'house.token', before: 'old-token-value', after: 'new-token-value' });
		expect(c.secret).toBe(true);
		expect(JSON.stringify(c)).not.toMatch(/token-value/);
		expect(c).toMatchObject({ before: '', after: '' });
	});

	it('treats a key the catalogue does not know, and a masked value, as a secret', () => {
		expect(card({ key: 'made.up_key', before: 'x', after: 'y' })).toMatchObject({ secret: true, before: '', after: '', what: '' });
		expect(card({ after: '(secret)' }).secret).toBe(true);
		expect(card({ before: '[redacted]' }).secret).toBe(true);
	});

	it('drops a row that is not a proposal, and an answer that is not a list', () => {
		expect(proposalCards([proposal(), null, { key: 'approvals.mode' }, { id: 7, key: 'approvals.mode' }, 'x'])).toHaveLength(1);
		expect(proposalCards({ error: 'nope' })).toEqual([]);
		// An `applies` the console has no words for prints nothing rather than the raw word.
		expect(card({ applies: 'whenever' as never }).applies).toBe('');
	});
});

function harness(over: Partial<{ load: () => Promise<unknown>; apply: (id: string) => Promise<unknown>; discard: (id: string) => Promise<unknown> }> = {}) {
	const io = {
		load: vi.fn(over.load ?? (async () => [proposal(), proposal({ id: 'd4e5f6', key: 'approvals.always_ttl_days', before: 30, after: 7 })])),
		apply: vi.fn(over.apply ?? (async () => ({}))),
		discard: vi.fn(over.discard ?? (async () => ({})))
	};
	const seen: ProposalsView[] = [];
	const queue = proposalsQueue(io, (v) => seen.push(v));
	const now = () => seen[seen.length - 1];
	const ids = () => now().cards?.map((c) => c.id);
	return { io, queue, seen, now, ids };
}

describe('the waiting settings changes', () => {
	it('is loading until the plugin answers, then lists every proposal', async () => {
		const h = harness();
		const reading = h.queue.read();
		expect(h.now()).toMatchObject({ cards: null, error: '' });
		await reading;
		expect(h.ids()).toEqual(['a1b2c3', 'd4e5f6']);
	});

	it('says why when the list cannot be read', async () => {
		const h = harness({ load: async () => Promise.reject(new Error('not connected')) });
		await h.queue.read();
		expect(h.now()).toMatchObject({ cards: null, error: 'not connected' });
	});

	it('Apply calls apply with the id, and the card leaves only once the plugin has answered', async () => {
		let land: () => void = () => {};
		const h = harness({ apply: () => new Promise<void>((resolve) => (land = resolve)) });
		await h.queue.read();
		const applying = h.queue.act('a1b2c3', 'apply');
		expect(h.io.apply).toHaveBeenCalledWith('a1b2c3');
		expect(h.now().acting).toBe('a1b2c3');
		expect(h.ids()).toEqual(['a1b2c3', 'd4e5f6']);
		land();
		await applying;
		expect(h.ids()).toEqual(['d4e5f6']);
		expect(h.now().acting).toBe('');
		expect(h.io.discard).not.toHaveBeenCalled();
	});

	it('Discard calls discard, and never apply', async () => {
		const h = harness();
		await h.queue.read();
		await h.queue.act('d4e5f6', 'discard');
		expect(h.io.discard).toHaveBeenCalledWith('d4e5f6');
		expect(h.io.apply).not.toHaveBeenCalled();
		expect(h.ids()).toEqual(['a1b2c3']);
	});

	it('sends nothing for a second press while the first is on the wire', async () => {
		let land: () => void = () => {};
		const h = harness({ apply: () => new Promise<void>((resolve) => (land = resolve)) });
		await h.queue.read();
		const first = h.queue.act('a1b2c3', 'apply');
		await h.queue.act('a1b2c3', 'apply');
		await h.queue.act('d4e5f6', 'discard');
		expect(h.io.apply).toHaveBeenCalledTimes(1);
		expect(h.io.discard).not.toHaveBeenCalled();
		land();
		await first;
	});

	it('a refused Apply keeps the card and says the plugin’s reason', async () => {
		const h = harness({ apply: async () => Promise.reject(new Error('refused: the kill switch is on')) });
		await h.queue.read();
		await h.queue.act('a1b2c3', 'apply');
		expect(h.ids()).toEqual(['a1b2c3', 'd4e5f6']);
		expect(h.now().failed).toBe('Couldn’t apply approvals.mode: refused: the kill switch is on');
		expect(h.now().acting).toBe('');
		// The next press clears the old reason.
		h.io.apply.mockImplementationOnce(async () => ({}));
		await h.queue.act('a1b2c3', 'apply');
		expect(h.now().failed).toBe('');
	});

	it('a proposal that was answered elsewhere leaves after the failed press', async () => {
		const lists = [[proposal()], []];
		const h = harness({ load: async () => lists.shift() ?? [], discard: async () => Promise.reject(new Error("no proposal 'a1b2c3'")) });
		await h.queue.read();
		await h.queue.act('a1b2c3', 'discard');
		expect(h.ids()).toEqual([]);
		expect(h.now().failed).toMatch(/no proposal/);
	});

	it('a read that lands after an answer does not draw the answered card again', async () => {
		let land: (rows: unknown) => void = () => {};
		const h = harness();
		await h.queue.read();
		h.io.load.mockImplementationOnce(() => new Promise((resolve) => (land = resolve)));
		// A refusal starts a quiet re-read; the person applies the other card before it lands.
		h.io.discard.mockImplementationOnce(async () => Promise.reject(new Error('busy')));
		const refused = h.queue.act('a1b2c3', 'discard');
		await vi.waitFor(() => expect(h.io.load).toHaveBeenCalledTimes(2));
		await h.queue.act('d4e5f6', 'apply');
		land([proposal(), proposal({ id: 'd4e5f6' })]);
		await refused;
		expect(h.ids()).toEqual(['a1b2c3']);
	});

	it('a proposal announced while the list is open is read in without a loading flash (T-1322)', async () => {
		const lists: unknown[][] = [[proposal()], [proposal(), proposal({ id: 'd4e5f6' })]];
		const h = harness({ load: async () => lists.shift() ?? [] });
		await h.queue.read();
		const drawn = h.seen.length;
		await h.queue.changed();
		expect(h.ids()).toEqual(['a1b2c3', 'd4e5f6']);
		// The cards stay drawn while it reads: no view in between is the loading one.
		expect(h.seen.slice(drawn).every((v) => v.cards !== null && !v.error)).toBe(true);
	});

	it('an announced read that lands after an answer does not draw the answered card, and a failed one keeps the list', async () => {
		let land: (rows: unknown) => void = () => {};
		const h = harness();
		await h.queue.read();
		h.io.load.mockImplementationOnce(() => new Promise((resolve) => (land = resolve)));
		const heard = h.queue.changed();
		await h.queue.act('a1b2c3', 'discard');
		land([proposal(), proposal({ id: 'd4e5f6' })]);
		await heard;
		expect(h.ids()).toEqual(['d4e5f6']);
		h.io.load.mockImplementationOnce(async () => Promise.reject(new Error('not connected')));
		await h.queue.changed();
		expect(h.ids()).toEqual(['d4e5f6']);
		expect(h.now().error).toBe('');
	});
});

describe('a change that was made, as its row under Recent changes', () => {
	const NOW = Date.parse('2026-11-13T10:05:00Z');
	const change = (over: Record<string, unknown> = {}) => ({ ts: '2026-11-13T10:00:00Z', actor: 'tool', key: 'goals.max_turns', before: 20, after: 40, undoable: true, ...over });
	const row = (over: Record<string, unknown> = {}) => changeRows([change(over)], 'Vesper', NOW)[0];

	it('says when, who, the key and both values', () => {
		expect(row()).toMatchObject({ key: 'goals.max_turns', when: '5 minutes ago', who: 'Vesper', secret: false, before: '20', after: '40', undoable: true });
	});

	it('names the actor: the assistant for a tool, an app for the command, else the actor’s own word', () => {
		expect(row({ actor: 'command' }).who).toBe('An app');
		expect(row({ actor: 'hud_layout' }).who).toBe('hud_layout');
		expect(row({ actor: '' }).who).toBe('Unknown');
	});

	it('never carries a secret’s value and never offers to put one back, whatever the plugin sent', () => {
		const secret = row({ key: 'house.token', before: 'old-value', after: 'new-value', undoable: true });
		expect(secret).toMatchObject({ secret: true, before: '', after: '', undoable: false });
		expect(JSON.stringify(secret)).not.toMatch(/old-value|new-value/);
		expect(row({ before: '(secret)', after: '(secret)' })).toMatchObject({ secret: true, undoable: false });
		// A key the console's schema does not name: it cannot tell what it would be drawing.
		expect(row({ key: 'made.up', before: 'a', after: 'b' })).toMatchObject({ secret: true, before: '', after: '', undoable: false });
	});

	it('offers Put back only where the plugin said so', () => {
		const rows = changeRows([change(), change({ ts: '2026-11-13T09:00:00Z', before: 10, after: 20, undoable: false }), change({ undoable: 'yes' })], 'Vesper', NOW);
		expect(rows.map((r) => r.undoable)).toEqual([true, false, false]);
	});

	it('drops a row with no key, keeps one with no readable time, and reads a non-list as nothing', () => {
		expect(changeRows([null, { actor: 'tool' }, change({ ts: 'soon' })], 'Vesper', NOW).map((r) => r.when)).toEqual(['']);
		expect(changeRows({ error: 'no' }, 'Vesper', NOW)).toEqual([]);
	});
});

describe('what the console says after Undo', () => {
	it('says the value is back once the plugin wrote it', () => {
		expect(undoSaid('goals.max_turns', { key: 'goals.max_turns', value: 20, previous: 40 })).toBe('Put goals.max_turns back.');
	});

	it('says it waits under Approvals when the plugin answered with a proposal, and never that it was put back', () => {
		const said = undoSaid('approvals.mode', { status: 'proposed', proposal: proposal() });
		expect(said).toMatch(/waiting for your OK under Approvals/);
		expect(said).not.toMatch(/^Put /);
	});
});
