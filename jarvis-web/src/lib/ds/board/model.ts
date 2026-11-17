import { wornName } from '$lib/ds/theme.svelte';
// The Board (T-0733): what JARVIS has been doing, from its own records, as six numbered panels
// (the owner, 2026-11-08: "graphs with the differnent models/tasks … inspired from the panel UI
// on the sampo live site").
//
// Pure: the adapter's GET /api/board (the jarvis-board plugin's counts from Hermes's state.db,
// llama-swap's models, the manager's cards) and the bench's capability rows, mapped to bounded
// drawing data. Each panel carries the state of the source it draws: stale with its age, or
// unavailable with the reason, never an old or missing number shown as current.
// Not covered: anything Hermes did not record (no cost for a local model, no per-hour tokens).

export type SourceStatus = 'ok' | 'stale' | 'unavailable';

export interface Source {
	status: SourceStatus;
	age_s: number | null;
	why: string | null;
}

export interface HermesBoard {
	schema: number;
	at: number;
	window_s: number;
	models: { model: string; turns: number; input_tokens: number; output_tokens: number; calls: number }[] | null;
	hours: { at: number; turns: Record<string, number> }[] | null;
	flows: { kind: string; model: string; turns: number }[] | null;
	kinds: { kind: string; turns: number; answered: number; cut_off: number; no_reply: number; running: number }[] | null;
	latency: { turns: number; p50_ms: number | null; p95_ms: number | null } | null;
	tools: { per_minute: number[]; top: { name: string; calls: number }[] } | null;
	cost: { usd: number | null; why: string | null } | null;
	errors: Record<string, string>;
}

export interface Gpu {
	index: number;
	name: string;
	total_mib: number;
	used_mib: number;
	users: { who: string; mib: number }[];
}

export interface BoardApi {
	at: number;
	sources: { hermes: Source; models: Source; gpus: Source };
	hermes: HermesBoard | null;
	models: { model: string; state: string }[] | null;
	gpus: Gpu[] | null;
	/** What the deploy's smoke last proved (T-0580); absent from an adapter older than it. */
	verified?: Verified | null;
}

export interface Verified {
	run: { tag: string; at: number; ok: boolean; failed: string | null; failed_check?: string | null } | null;
	checks: { id: string; words: string; passed_at: number | null; tag: string | null; current: boolean }[];
	/** The golden turn per surface: passed, failed, or (ok null) skipped or not run, with why. */
	surfaces: { id: string; words: string; ok: boolean | null; at: number | null; passed_at: number | null; why: string }[];
}

export interface CapabilityRows {
	rows: readonly { id: string; category: string; last_passed: string }[];
}

export interface Chrome {
	n: string;
	name: string;
	/** What the panel shows, in a few words. */
	what: string;
	/** The accent figure on the right of the name. */
	label: string;
	foot: string;
	stat: string;
	state: SourceStatus;
	why: string;
}

export type Outcome = 'answered' | 'cut_off' | 'no_reply' | 'running';
export type SuiteCell = 'recent' | 'passed' | 'never';

export type Panel =
	| { kind: 'models'; chrome: Chrome; kinds: { id: string; words: string; turns: number }[]; models: { name: string; turns: number }[]; flows: { from: string; to: string; turns: number }[] }
	| { kind: 'turns'; chrome: Chrome; hours: { at: number; stacks: { model: string; n: number }[] }[]; series: string[]; peak: number }
	| { kind: 'tasks'; chrome: Chrome; rows: { id: string; words: string; turns: number; cells: Outcome[] }[]; per: number }
	| { kind: 'tools'; chrome: Chrome; perMinute: number[]; peak: number; rate: number; top: { name: string; calls: number }[] }
	| { kind: 'load'; chrome: Chrome; cards: { name: string; fill: number; usedMib: number; totalMib: number; users: { who: string; share: number }[] }[]; models: { name: string; state: string }[] }
	| { kind: 'suites'; chrome: Chrome; categories: { id: string; words: string; cells: SuiteCell[] }[] }
	| { kind: 'verified'; chrome: Chrome; rows: { id: string; words: string; note: string; cell: VerifiedCell }[] };

/** Passed on the deploy that is running, on an earlier one, the check the last run failed at, or never.
 *  A surface: its turn passed since that deploy's smoke, before it, failed, or was skipped or not run. */
export type VerifiedCell = 'recent' | 'passed' | 'no_reply' | 'never';

/** Hermes's session sources, in words a person uses. */
const KIND_WORDS: Record<string, string> = {
	tui: 'Console & voice',
	api_server: 'Apps (API)',
	cron: 'Scheduled',
	subagent: 'Helpers',
	cli: 'Terminal',
	// A deploy's own smoke and golden turns (jarvis-board tags them): model load, not the person's.
	probe: 'Deploy checks'
};
const words = (kind: string) => KIND_WORDS[kind] ?? kind.replace(/_/g, ' ');

export const CELLS = 40;
const RECENT_DAYS = 14;

const k = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
const secs = (ms: number | null) => (ms === null ? '–' : ms < 10_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 1000)} s`);

function ago(s: number): string {
	if (s < 90) return `${Math.round(s)} s ago`;
	if (s < 3600) return `${Math.round(s / 60)} min ago`;
	return `${Math.round(s / 3600)} h ago`;
}

/** A row of at most `max` cells by outcome; past that each cell stands for `per` turns (largest remainder). */
export function cellsOf(o: Record<Outcome, number>, max = CELLS): { cells: Outcome[]; per: number } {
	const order: Outcome[] = ['answered', 'cut_off', 'no_reply', 'running'];
	const total = order.reduce((s, x) => s + o[x], 0);
	const per = Math.max(1, Math.ceil(total / max));
	const n = Math.ceil(total / per);
	const exact = order.map((x) => (o[x] / total) * n || 0);
	const counts = exact.map(Math.floor);
	const left = n - counts.reduce((s, c) => s + c, 0);
	exact
		.map((e, i) => [e - Math.floor(e), i] as const)
		.sort((a, b) => b[0] - a[0])
		.slice(0, left)
		.forEach(([, i]) => counts[i]++);
	return { cells: order.flatMap((x, i) => Array<Outcome>(counts[i]).fill(x)), per };
}

function chrome(n: number, name: string, what: string, src: Source, sectionError?: string): Chrome {
	const unavailable = src.status === 'unavailable' || Boolean(sectionError);
	return {
		n: String(n).padStart(2, '0'),
		name,
		what,
		label: '',
		foot: src.status === 'stale' && src.age_s !== null ? `as of ${ago(src.age_s)}` : '',
		stat: '',
		state: unavailable ? 'unavailable' : src.status,
		why: sectionError ?? src.why ?? ''
	};
}

/** A series by `by` buckets on: the oldest fall off, empty newest come in. */
const moved = <T>(xs: T[], by: number, empty: () => T): T[] =>
	by <= 0 ? xs : [...xs.slice(by), ...Array.from({ length: Math.min(by, xs.length) }, empty)];

const withFoot = (c: Chrome, foot: string): Chrome => ({ ...c, foot: [c.foot, foot].filter(Boolean).join(' · ') });

export function boardPanels(api: BoardApi, caps: CapabilityRows, now = Date.now() / 1000): Panel[] {
	const h = api.hermes;
	const src = api.sources.hermes;
	const err = h?.errors ?? {};
	// The plugin writes only while JARVIS works, so its file can be hours old: the per-hour and
	// per-minute series move on to the adapter's now, or an idle JARVIS would show its last burst as current.
	const age = h ? Math.max(0, api.at - h.at) : 0;

	// 01 MODELS
	const flows = h?.flows ?? [];
	const kindTurns = new Map<string, number>();
	const modelTurns = new Map<string, number>();
	for (const f of flows) {
		kindTurns.set(f.kind, (kindTurns.get(f.kind) ?? 0) + f.turns);
		modelTurns.set(f.model, (modelTurns.get(f.model) ?? 0) + f.turns);
	}
	const turnsTotal = flows.reduce((s, f) => s + f.turns, 0);
	const lat = h?.latency;
	const models: Panel = {
		kind: 'models',
		chrome: {
			...withFoot(chrome(1, 'MODELS', 'which model took each kind of task', src, h && !h.flows ? err.turns ?? 'not recorded' : undefined), lat ? `${lat.turns} answered turns timed` : ''),
			label: `${turnsTotal} TURNS / 24 H`,
			stat: lat ? `p50 ${secs(lat.p50_ms)} · p95 ${secs(lat.p95_ms)}` : ''
		},
		kinds: [...kindTurns].sort((a, b) => b[1] - a[1]).map(([id, turns]) => ({ id, words: words(id), turns })),
		models: [...modelTurns].sort((a, b) => b[1] - a[1]).map(([name, turns]) => ({ name, turns })),
		flows: flows.map((f) => ({ from: f.kind, to: f.model, turns: f.turns }))
	};

	// 02 TURNS
	// Only models that answered turns: an auxiliary model (production: 3 calls, no turn of its
	// own) counts in the token totals, not as an empty series in the legend.
	const series = (h?.models ?? []).filter((m) => m.turns > 0).map((m) => m.model);
	const hourly = moved<{ at: number; turns: Record<string, number> }>(h?.hours ?? [], Math.floor(age / 3600), () => ({ at: 0, turns: {} }));
	const hours = hourly.map((x, i) => ({
		at: api.at - (hourly.length - 1 - i) * 3600,
		stacks: Object.entries(x.turns)
			.sort((a, b) => series.indexOf(a[0]) - series.indexOf(b[0]))
			.map(([model, n]) => ({ model, n }))
	}));
	const peak = Math.max(0, ...hours.map((x) => x.stacks.reduce((s, y) => s + y.n, 0)));
	const tin = (h?.models ?? []).reduce((s, m) => s + m.input_tokens, 0);
	const tout = (h?.models ?? []).reduce((s, m) => s + m.output_tokens, 0);
	const cost = h?.cost;
	const turns: Panel = {
		kind: 'turns',
		chrome: {
			...withFoot(chrome(2, 'TURNS', 'per hour, by model, and the tokens they took', src, h && !h.hours ? err.turns ?? 'not recorded' : undefined), `tokens ${k(tin)} in · ${k(tout)} out`),
			label: `PEAK ${peak} / H`,
			stat: cost?.usd != null ? `$${cost.usd.toFixed(2)} estimated` : 'no cost recorded'
		},
		hours,
		series,
		peak
	};

	// 03 TASKS
	const kinds = h?.kinds ?? [];
	let per = 1;
	const rows = kinds.map((x) => {
		const c = cellsOf(x);
		per = Math.max(per, c.per);
		return { id: x.kind, words: words(x.kind), turns: x.turns, cells: c.cells };
	});
	const answered = kinds.reduce((s, x) => s + x.answered, 0);
	const asked = kinds.reduce((s, x) => s + x.turns, 0);
	const tasks: Panel = {
		kind: 'tasks',
		chrome: {
			...withFoot(chrome(3, 'TASKS', 'every turn by kind, and how it ended', src, h && !h.kinds ? err.turns ?? 'not recorded' : undefined), per > 1 ? `a cell is ${per} turns` : 'a cell is a turn'),
			label: `${answered} OF ${asked} ANSWERED`,
			stat: `${kinds.reduce((s, x) => s + x.no_reply, 0)} without a reply`
		},
		rows,
		per
	};

	// 04 TOOLS
	const perMinute = moved(h?.tools?.per_minute ?? [], Math.floor(age / 60), () => 0);
	const lastFive = perMinute.slice(-5);
	const rate = lastFive.length ? Math.round((lastFive.reduce((s, x) => s + x, 0) / lastFive.length) * 10) / 10 : 0;
	const top = h?.tools?.top ?? [];
	const tools: Panel = {
		kind: 'tools',
		chrome: {
			...withFoot(chrome(4, 'TOOLS', 'calls a minute over the last hour', src, h && !h.tools ? err.tools ?? 'not recorded' : undefined), `${perMinute.reduce((s, x) => s + x, 0)} calls in the hour`),
			label: `${rate} CALLS / MIN`,
			stat: top[0] ? `most: ${top[0].name.replace(/_/g, ' ')}` : ''
		},
		perMinute,
		peak: Math.max(0, ...perMinute),
		rate,
		top: top.slice(0, 5)
	};

	// 05 LOAD
	// Memory the manager names no holder for (the driver, a process it does not know) is still
	// used: drawn faint under the surface rather than as a gap below it.
	const held = (g: Gpu) => {
		const rest = g.used_mib - g.users.reduce((s, u) => s + u.mib, 0);
		return rest > 0 ? [...g.users, { who: 'unattributed', mib: rest }] : g.users;
	};
	const modelsSrc = api.sources.models;
	const gpusSrc = api.sources.gpus;
	const both: Source =
		modelsSrc.status === 'unavailable' && gpusSrc.status === 'unavailable'
			? { status: 'unavailable', age_s: null, why: [modelsSrc.why, gpusSrc.why].filter(Boolean).join('; ') }
			: { status: 'ok', age_s: 0, why: null };
	const cards = (api.gpus ?? []).map((g) => ({
		name: g.name,
		fill: g.total_mib ? g.used_mib / g.total_mib : 0,
		usedMib: g.used_mib,
		totalMib: g.total_mib,
		users: held(g).map((u) => ({ who: u.who, share: g.total_mib ? u.mib / g.total_mib : 0 }))
	}));
	const served = (api.models ?? []).map((m) => ({ name: m.model, state: m.state }));
	const used = cards.reduce((s, c) => s + c.usedMib, 0);
	const total = cards.reduce((s, c) => s + c.totalMib, 0);
	const load: Panel = {
		kind: 'load',
		chrome: {
			...withFoot(chrome(5, 'LOAD', 'the local models and the cards that hold them', both), cards.length ? `${(used / 1024).toFixed(1)} of ${(total / 1024).toFixed(1)} GiB` : (gpusSrc.why ?? '')),
			label: `${served.filter((m) => m.state === 'ready').length} READY`,
			stat: modelsSrc.status === 'unavailable' ? 'models server not answering' : served.length ? '' : 'no model loaded'
		},
		cards,
		models: served
	};

	// 06 SUITES
	const byCat = new Map<string, SuiteCell[]>();
	const cutoff = (now - RECENT_DAYS * 86400) * 1000;
	for (const r of caps.rows) {
		const cell: SuiteCell = !r.last_passed ? 'never' : Date.parse(r.last_passed) >= cutoff ? 'recent' : 'passed';
		byCat.set(r.category, [...(byCat.get(r.category) ?? []), cell]);
	}
	const passed = caps.rows.filter((r) => r.last_passed).length;
	const suites: Panel = {
		kind: 'suites',
		chrome: {
			...chrome(6, 'SUITES', `what ${wornName()} has shown it can do`, { status: 'ok', age_s: null, why: null }),
			label: `${passed} OF ${caps.rows.length} PASSED`,
			foot: `lit: passed in the last ${RECENT_DAYS} days · from the bench results`,
			stat: `${caps.rows.length - passed} never passed`
		},
		categories: [...byCat].map(([id, cells]) => ({ id, words: id.replace(/-/g, ' '), cells }))
	};

	// 07 VERIFIED: the deploy's smoke, check by check, then the golden turn, surface by surface.
	// The run names the check it failed at; one from an older reporter does not, and there the
	// first check it did not pass stands in (the checks run in order and stop at a failure).
	const v = api.verified;
	const since = (at: number) => {
		const s = Math.max(0, api.at - at);
		return s < 3600 ? `${Math.round(s / 60)} min` : s < 172800 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;
	};
	const failedAt = v?.run && !v.run.ok ? (v.run.failed_check ?? v.checks.find((c) => !c.current)?.id) : undefined;
	const surfaces = v?.surfaces ?? [];
	const skipped = surfaces.filter((s) => s.ok === null);
	const onThis = v?.checks.filter((c) => c.current).length ?? 0;
	const verified: Panel = {
		kind: 'verified',
		chrome: {
			...chrome(
				7,
				'VERIFIED',
				'what was last proved, check by check and surface by surface',
				{ status: 'ok', age_s: null, why: null },
				!v ? 'this adapter does not report it yet' : !v.run && surfaces.every((s) => s.at === null) ? 'no deploy has reported a smoke run yet' : undefined
			),
			label: v?.run ? `${onThis} OF ${v.checks.length} ON THE LAST RUN` : '',
			// Why a surface has no turn is said, so an unlit cell is never read as a pass.
			foot: [v?.run ? `${v.run.tag} · ${since(v.run.at)} ago` : '', ...skipped.map((s) => `${s.words}: ${s.why}`)].filter(Boolean).join(' · '),
			stat: [v?.run?.failed ? `failed: ${v.run.failed.slice(0, 60)}` : '', ...surfaces.filter((s) => s.ok === false).map((s) => `${s.words}: ${s.why.slice(0, 60)}`)].filter(Boolean).join(' · ')
		},
		rows: v
			? [
					...v.checks.map((c) => ({
						id: c.id,
						words: c.words,
						note: c.passed_at === null ? 'never' : since(c.passed_at),
						cell: (c.id === failedAt ? 'no_reply' : c.current ? 'recent' : c.passed_at !== null ? 'passed' : 'never') as VerifiedCell
					})),
					...surfaces.map((s) => ({
						id: s.id,
						words: s.words,
						// The last pass is kept in view through a later skip or failure: a phone that
						// passed last time and is unplugged now is dim, not blank.
						note: s.passed_at !== null ? since(s.passed_at) : s.ok === false ? 'failed' : s.at === null ? 'not run' : 'skipped',
						cell: (s.ok === false ? 'no_reply' : s.passed_at === null ? 'never' : s.ok === null || (v.run && s.passed_at < v.run.at) ? 'passed' : 'recent') as VerifiedCell
					}))
				]
			: []
	};

	return [models, turns, tasks, tools, load, suites, verified];
}
