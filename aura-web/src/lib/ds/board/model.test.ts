import { describe, expect, it } from 'vitest';
import { boardPanels, cellsOf, type BoardApi, type HermesBoard } from './model';

const NOW = 1_800_000_000;
const ok = { status: 'ok' as const, age_s: 4, why: null };

function hermes(over: Partial<HermesBoard> = {}): HermesBoard {
	return {
		schema: 1,
		at: NOW - 4,
		window_s: 86400,
		models: [
			{ model: 'qwen', turns: 30, input_tokens: 90_000, output_tokens: 4_000, calls: 70 },
			{ model: 'other', turns: 2, input_tokens: 1_000, output_tokens: 100, calls: 2 }
		],
		hours: Array.from({ length: 24 }, (_, i) => ({ at: NOW - (23 - i) * 3600, turns: (i === 23 ? { qwen: 5, other: 2 } : i === 20 ? { qwen: 9 } : {}) as Record<string, number> })),
		flows: [
			{ kind: 'tui', model: 'qwen', turns: 20 },
			{ kind: 'cron', model: 'qwen', turns: 10 },
			{ kind: 'api_server', model: 'other', turns: 2 }
		],
		kinds: [
			{ kind: 'tui', turns: 20, answered: 15, cut_off: 1, no_reply: 3, running: 1 },
			{ kind: 'cron', turns: 10, answered: 10, cut_off: 0, no_reply: 0, running: 0 }
		],
		latency: { turns: 26, p50_ms: 4200, p95_ms: 19000 },
		tools: { per_minute: [...Array(55).fill(0), 1, 0, 2, 3, 4], top: [{ name: 'web_search', calls: 40 }] },
		cost: { usd: null, why: 'Hermes recorded no cost for these models' },
		errors: {},
		...over
	};
}

function api(over: Partial<BoardApi> = {}): BoardApi {
	return {
		at: NOW,
		sources: { hermes: ok, models: { status: 'ok', age_s: 0, why: null }, gpus: { status: 'ok', age_s: 0, why: null } },
		hermes: hermes(),
		models: [{ model: 'qwen', state: 'ready' }],
		gpus: [{ index: 0, name: 'RTX', total_mib: 24576, used_mib: 21650, users: [{ who: 'models', mib: 20890 }, { who: 'voice', mib: 760 }] }],
		verified: { run: { tag: 't1', at: NOW - 600, ok: true, failed: null }, checks: [{ id: 'health', words: 'health', passed_at: NOW - 600, tag: 't1', current: true }], surfaces: [] },
		...over
	};
}

const CAPS = {
	rows: [
		{ id: 'a', category: 'everyday', last_passed: '2027-01-14' },
		{ id: 'b', category: 'everyday', last_passed: '2026-09-01' },
		{ id: 'c', category: 'everyday', last_passed: '' },
		{ id: 'd', category: 'voice', last_passed: '2027-01-13' }
	]
};

describe('boardPanels', () => {
	it('numbers seven panels and fills each from its own source', () => {
		const p = boardPanels(api(), CAPS, NOW);
		expect(p.map((x) => `${x.chrome.n} ${x.chrome.name}`)).toEqual(['01 MODELS', '02 TURNS', '03 TASKS', '04 TOOLS', '05 LOAD', '06 SUITES', '07 VERIFIED']);
		expect(p.every((x) => x.chrome.state === 'ok')).toBe(true);
	});

	it('models: each kind of task flows to the model that answered it, with the latency', () => {
		const m = boardPanels(api(), CAPS, NOW)[0];
		if (m.kind !== 'models') throw new Error('order');
		expect(m.kinds.map((k) => [k.id, k.words, k.turns])).toEqual([
			['tui', 'Console & voice', 20],
			['cron', 'Scheduled', 10],
			['api_server', 'Apps (API)', 2]
		]);
		expect(m.models.map((x) => [x.name, x.turns])).toEqual([['qwen', 30], ['other', 2]]);
		expect(m.chrome.label).toBe('32 TURNS / 24 H');
		expect(m.chrome.stat).toBe('p50 4.2 s · p95 19 s');
	});

	it('names a deploy\'s own check turns in words, in models and in tasks', () => {
		const h = hermes();
		const withProbe = hermes({
			flows: [...h.flows!, { kind: 'probe', model: 'qwen', turns: 3 }],
			kinds: [...h.kinds!, { kind: 'probe', turns: 3, answered: 3, cut_off: 0, no_reply: 0, running: 0 }]
		});
		const [m, , t] = boardPanels(api({ hermes: withProbe }), CAPS, NOW);
		if (m.kind !== 'models' || t.kind !== 'tasks') throw new Error('order');
		expect(m.kinds.find((k) => k.id === 'probe')?.words).toBe('Deploy checks');
		expect(t.rows.find((r) => r.id === 'probe')?.words).toBe('Deploy checks');
	});

	it('turns: 24 hourly stacks by model, the peak, and tokens; no cost is invented', () => {
		const t = boardPanels(api(), CAPS, NOW)[1];
		if (t.kind !== 'turns') throw new Error('order');
		expect(t.hours).toHaveLength(24);
		expect(t.hours[23].stacks).toEqual([{ model: 'qwen', n: 5 }, { model: 'other', n: 2 }]);
		expect(t.peak).toBe(9);
		expect(t.chrome.label).toBe('PEAK 9 / H');
		expect(t.chrome.foot).toContain('91.0k in · 4.1k out');
		expect(t.chrome.stat).toBe('no cost recorded');
	});

	it('turns: an auxiliary model with no turn of its own is in the tokens, not the legend; millions read as M', () => {
		const models = [...hermes().models!, { model: 'aux', turns: 0, input_tokens: 98_000_000, output_tokens: 500, calls: 3 }];
		const t = boardPanels(api({ hermes: hermes({ models }) }), CAPS, NOW)[1];
		if (t.kind !== 'turns') throw new Error('order');
		expect(t.series).toEqual(['qwen', 'other']);
		expect(t.chrome.foot).toContain('98.1M in');
	});

	it('tasks: a row per kind of cells by outcome, scaled when a kind has more turns than cells', () => {
		const k = boardPanels(api(), CAPS, NOW)[2];
		if (k.kind !== 'tasks') throw new Error('order');
		expect(k.rows[0].cells.filter((c) => c === 'answered')).toHaveLength(15);
		expect(k.rows[0].cells.filter((c) => c === 'running')).toHaveLength(1);
		expect(k.chrome.label).toBe('25 OF 30 ANSWERED');
		const many = cellsOf({ answered: 300, cut_off: 0, no_reply: 100, running: 0 }, 40);
		expect(many.cells).toHaveLength(40);
		expect(many.per).toBe(10);
		expect(many.cells.filter((c) => c === 'no_reply')).toHaveLength(10);
	});

	it('tools: the last hour per minute, the rate over the last five minutes', () => {
		const t = boardPanels(api(), CAPS, NOW)[3];
		if (t.kind !== 'tools') throw new Error('order');
		expect(t.perMinute).toHaveLength(60);
		expect(t.rate).toBe(2); // (1 + 0 + 2 + 3 + 4) / 5
		expect(t.chrome.label).toBe('2 CALLS / MIN');
	});

	it('an older file moves on to now: an idle JARVIS reads idle, its last burst hours back', () => {
		const p = boardPanels(api({ hermes: hermes({ at: NOW - 3 * 3600 }) }), CAPS, NOW);
		const [turns, tools] = [p[1], p[3]];
		if (turns.kind !== 'turns' || tools.kind !== 'tools') throw new Error('order');
		expect(tools.perMinute).toHaveLength(60);
		expect(tools.rate).toBe(0);
		expect(turns.hours).toHaveLength(24);
		expect(turns.hours[20].stacks).toEqual([{ model: 'qwen', n: 5 }, { model: 'other', n: 2 }]);
		expect(turns.hours[23].stacks).toEqual([]);
		expect(turns.hours[23].at).toBe(NOW);
	});

	it('load: each card as a tank of who holds its memory, and llama-swap’s models', () => {
		const l = boardPanels(api(), CAPS, NOW)[4];
		if (l.kind !== 'load') throw new Error('order');
		expect(l.cards[0].fill).toBeCloseTo(21650 / 24576);
		const g = { index: 0, name: 'RTX', total_mib: 24576, used_mib: 20702, users: [{ who: 'models', mib: 19406 }] };
		const parts = boardPanels(api({ gpus: [g] }), CAPS, NOW)[4];
		if (parts.kind !== 'load') throw new Error('order');
		// production: what the manager names no holder for fills up to the surface, not a gap below it
		expect(parts.cards[0].users.map((u) => u.who)).toEqual(['models', 'unattributed']);
		expect(parts.cards[0].users.reduce((s, u) => s + u.share, 0)).toBeCloseTo(parts.cards[0].fill);
		expect(l.models).toEqual([{ name: 'qwen', state: 'ready' }]);
		expect(l.chrome.label).toBe('1 READY');
	});

	it('suites: a cell per bench task by category, lit when it passed in the last two weeks', () => {
		const s = boardPanels(api(), CAPS, NOW)[5];
		if (s.kind !== 'suites') throw new Error('order');
		expect(s.categories.map((c) => [c.id, c.cells])).toEqual([
			['everyday', ['recent', 'passed', 'never']],
			['voice', ['recent']]
		]);
		expect(s.chrome.label).toBe('3 OF 4 PASSED');
	});

	it('an old board is stale with its age; a missing one makes its panels unavailable with the reason', () => {
		const stale = boardPanels(api({ sources: { ...api().sources, hermes: { status: 'stale', age_s: 3600, why: null } } }), CAPS, NOW);
		expect(stale[0].chrome.state).toBe('stale');
		expect(stale[0].chrome.foot).toMatch(/as of 1 h ago/);
		const gone = boardPanels(api({ hermes: null, sources: { ...api().sources, hermes: { status: 'unavailable', age_s: null, why: 'not written yet' } } }), CAPS, NOW);
		expect(gone.slice(0, 4).map((p) => [p.chrome.state, p.chrome.why])).toEqual(Array(4).fill(['unavailable', 'not written yet']));
		expect(gone[4].chrome.state).toBe('ok');
		expect(gone[5].chrome.state).toBe('ok');
	});

	it('a section the plugin could not read takes out only its panels', () => {
		const p = boardPanels(api({ hermes: hermes({ kinds: null, flows: null, hours: null, latency: null, errors: { turns: 'no such column: finish_reason' } }) }), CAPS, NOW);
		expect(p.map((x) => x.chrome.state)).toEqual(['unavailable', 'unavailable', 'unavailable', 'ok', 'ok', 'ok', 'ok']);
		expect(p[2].chrome.why).toContain('finish_reason');
	});

	it('the models server down leaves the load panel unavailable, the rest standing', () => {
		const down = { status: 'unavailable' as const, age_s: null, why: '/running: ConnectError' };
		const p = boardPanels(api({ models: null, gpus: null, sources: { hermes: ok, models: down, gpus: down } }), CAPS, NOW);
		expect(p[4].chrome.state).toBe('unavailable');
		expect(p[0].chrome.state).toBe('ok');
	});
});

describe('07 VERIFIED: what the last deploy\'s smoke proved (T-0580)', () => {
	const check = (id: string, passed_at: number | null, tag: string | null, current: boolean) => ({ id, words: id, passed_at, tag, current });
	const surfaces = [
		{ id: 'console', words: 'console', ok: true, at: NOW - 3600, passed_at: NOW - 3600, why: '' },
		{ id: 'car', words: 'car screen', ok: false, at: NOW - 3600, passed_at: NOW - 90_000, why: 'Timeout 30000ms exceeded' },
		{ id: 'phone', words: 'phone app', ok: null, at: NOW - 3600, passed_at: null, why: 'no phone attached (adb)' },
		{ id: 'desktop', words: 'desktop app', ok: null, at: null, passed_at: null, why: 'not run yet' }
	];
	const panel = (verified: BoardApi['verified']) => {
		const p = boardPanels(api({ verified }), { rows: [] }, NOW)[6];
		if (p.kind !== 'verified') throw new Error('order');
		return p;
	};

	it('lights a check passed on this deploy, dims one passed on an earlier one, and marks the first one the run did not reach as failed', () => {
		const p = panel({
			run: { tag: 't2', at: NOW - 7200, ok: false, failed: 'SSE chunks=0' },
			checks: [check('health', NOW - 7200, 't2', true), check('chat', NOW - 90_000, 't1', false), check('tool', NOW - 90_000, 't1', false), check('voice', null, null, false)],
			surfaces
		});
		expect(p.rows.map((r) => [r.id, r.cell, r.note])).toEqual([
			['health', 'recent', '2 h'],
			['chat', 'no_reply', '25 h'],
			['tool', 'passed', '25 h'],
			['voice', 'never', 'never'],
			['console', 'recent', '1 h'],
			['car', 'no_reply', '25 h'],
			['phone', 'never', 'skipped'],
			['desktop', 'never', 'not run']
		]);
		expect(p.chrome.label).toBe('1 OF 4 ON THE LAST RUN');
		expect(p.chrome.stat).toBe('failed: SSE chunks=0 · car screen: Timeout 30000ms exceeded');
		expect(p.chrome.foot).toContain('t2');
		expect(p.chrome.foot).toContain('phone app: no phone attached (adb)');
	});

	it('takes the failed check from the run when it names one, and dims a surface turn older than the deploy', () => {
		const p = panel({
			run: { tag: 't2', at: NOW - 60, ok: false, failed: 'steps 4-5d', failed_check: 'tool' },
			checks: [check('health', NOW - 60, 't2', true), check('chat', NOW - 90_000, 't1', false), check('tool', NOW - 90_000, 't1', false)],
			surfaces: [surfaces[0]]
		});
		expect(p.rows.map((r) => r.cell)).toEqual(['recent', 'passed', 'no_reply', 'passed']);
	});

	it('keeps a surface\'s last pass in view when its turn is skipped this time', () => {
		const p = panel({ run: null, checks: [], surfaces: [{ ...surfaces[2], passed_at: NOW - 7200 }, surfaces[2]] });
		expect(p.rows.map((r) => [r.cell, r.note])).toEqual([['passed', '2 h'], ['never', 'skipped']]);
	});

	it('shows a golden turn reported before any deploy has', () => {
		const p = panel({ run: null, checks: [check('health', null, null, false)], surfaces: [surfaces[0]] });
		expect(p.chrome.state).toBe('ok');
		expect(p.rows.map((r) => r.cell)).toEqual(['never', 'recent']);
	});

	it('a clean run fails nothing', () => {
		const p = panel({ run: { tag: 't3', at: NOW - 60, ok: true, failed: null }, checks: [check('health', NOW - 60, 't3', true)], surfaces: [] });
		expect(p.rows.map((r) => r.cell)).toEqual(['recent']);
		expect(p.chrome.stat).toBe('');
		expect(p.chrome.state).toBe('ok');
	});

	it('says so when no deploy has reported yet, or the adapter is too old to', () => {
		const none = panel({ run: null, checks: [check('health', null, null, false)], surfaces: [] });
		expect(none.chrome.state).toBe('unavailable');
		expect(none.chrome.why).toMatch(/no deploy has reported/);
		const old = panel(undefined);
		expect(old.chrome.state).toBe('unavailable');
		expect(old.rows).toEqual([]);
	});
});
