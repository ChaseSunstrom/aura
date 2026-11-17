// What JARVIS is doing, step by step, while it does it (T-0732; the owner, 2026-11-08: "theres
// no visual representation of it doing different tasks, like browsing the website").
//
// Pure: one ordered list from the HUD state the console already folds -- the turn's tool
// cards, the background rows, the open approvals and clarifies, and the browser's latest
// `activity.frame` -- so every surface the console serves (desktop, the app, the phone's home)
// draws the same steps. Not covered: a step's detail is what Hermes's events carry; a shell
// command's output arrives with its result (this Hermes sends no `tool.progress` for it), and
// only the newest browse step carries the picture, which is held in memory and never stored.

import type { BackgroundRow, HudState, ToolCard } from '$lib/hermes/hud';
import { summariseToolArgs } from '$lib/hermes/hud';
import { timelineCalls } from '$lib/hud/data/timeline';
import { worded } from '$lib/hud/data/toolWords';

export type StepState = 'thinking' | 'acting' | 'waiting' | 'done' | 'failed';
export type StepKind = 'browse' | 'search' | 'shell' | 'device' | 'other';

export interface Source {
	title: string;
	url: string;
}

export interface LiveStep {
	id: string;
	/** What the step does, in plain words. */
	text: string;
	state: StepState;
	kind: StepKind;
	/** How long it took, once it has. */
	ms: number | null;
	error?: string;
	// browse
	url?: string;
	title?: string;
	/** The page as a data URL: only the newest browse step, only the latest frame. */
	image?: string | null;
	// search
	query?: string;
	sources?: Source[];
	// shell
	command?: string;
	tail?: string[];
	// device
	entity?: string;
	service?: string;
	/** The state the device was left in. */
	left?: string;
	// other
	line?: string;
}

const SOURCES = 4;
const TAIL = 4;
const TAIL_WIDTH = 160;
const LINE = 120;

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const text = (v: unknown) => (typeof v === 'string' ? v : '');
const record = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** The card's result as Hermes parsed it (hud.ts keeps an object result as its JSON). */
function parsed(card: ToolCard): Record<string, unknown> {
	if (!card.result) return {};
	try {
		return record(JSON.parse(card.result));
	} catch {
		return {};
	}
}

/** A result that says it failed, though the call returned: `success:false`, `ok:false`, a non-zero `exit_code`. */
function saysFailed(r: Record<string, unknown>): boolean {
	const code = r.exit_code;
	// Home Assistant's tools wrap theirs one level down: {result: {success, …}}.
	const inner = record(r.result);
	return r.success === false || r.ok === false || inner.success === false || inner.ok === false || (typeof code === 'number' && code !== 0);
}

function kindOf(name: string): StepKind {
	if (name.startsWith('browser_')) return 'browse';
	if (name === 'web_search') return 'search';
	if (name === 'terminal') return 'shell';
	if (name.startsWith('ha_')) return 'device';
	return 'other';
}

function detail(card: ToolCard, kind: StepKind, r: Record<string, unknown>): Partial<LiveStep> {
	const args = card.args ?? {};
	switch (kind) {
		case 'browse':
			return { url: text(r.url) || text(args.url) || undefined, title: text(r.title) || undefined };
		case 'search': {
			const web = record(r.data).web;
			const sources = (Array.isArray(web) ? web : [])
				.map((w) => ({ title: text(record(w).title), url: text(record(w).url) }))
				.filter((s) => s.url)
				.slice(0, SOURCES);
			return { query: text(args.query), sources };
		}
		case 'shell': {
			const out = text(r.output) || (card.state === 'running' ? card.progress : '');
			const tail = out
				.split('\n')
				.filter((l) => l.trim())
				.slice(-TAIL)
				.map((l) => clip(l, TAIL_WIDTH));
			return { command: text(args.command), tail };
		}
		case 'device': {
			const result = record(r.result);
			const entity = text(args.entity_id);
			const affected = Array.isArray(result.affected_entities) ? result.affected_entities.map(record) : [];
			const left = affected.find((a) => a.entity_id === entity) ?? affected[0];
			const service = text(result.service) || (args.domain && args.service ? `${text(args.domain)}.${text(args.service)}` : '');
			return { entity, service: service || undefined, left: left ? text(left.state) || undefined : undefined };
		}
		default:
			// Only for a tool with no words of its own: one that has them already says what it
			// does, and its raw arguments were what the owner called noise (2026-11-08).
			return worded(card.name) ? {} : { line: clip(summariseToolArgs(card.args), LINE) || undefined };
	}
}

function background(row: BackgroundRow): StepState {
	return row.state === 'running' ? 'acting' : row.state === 'failed' || row.state === 'interrupted' ? 'failed' : 'done';
}

/** The turn's steps, oldest first, with a trailing thinking or waiting step when one applies. */
export function liveSteps(hud: HudState | null | undefined): LiveStep[] {
	if (!hud) return [];
	const cards = new Map(hud.turn.tools.map((t) => [`tool:${t.id}`, t]));
	const rows = new Map(hud.background.map((b) => [`${b.kind}:${b.id}`, b]));
	const steps: LiveStep[] = [];
	for (const call of timelineCalls(hud)) {
		if (call.step === '') continue; // a call that only presents (hud_*) is not a step
		const card = cards.get(call.id);
		const plain = call.name.replace(/_/g, ' ');
		const base = { id: call.id, text: call.step ?? plain.charAt(0).toUpperCase() + plain.slice(1), ms: call.ms ?? null };
		if (!card) {
			const row = rows.get(call.id);
			steps.push({ ...base, kind: 'other', state: row ? background(row) : 'done', line: call.args });
			continue;
		}
		const kind = kindOf(card.name);
		const r = parsed(card);
		const failed = card.state === 'failed' || (card.state === 'done' && saysFailed(r));
		const state: StepState = card.state === 'running' ? 'acting' : failed ? 'failed' : 'done';
		const error = card.error ?? (failed ? text(r.error) || undefined : undefined);
		steps.push({ ...base, kind, state, ...(error ? { error } : {}), ...detail(card, kind, r) });
	}

	// The browser's latest frame belongs to the newest browse step; the older ones keep their own
	// url. Only a frame that came in this turn: the activity outlives a turn by its idle wait, and
	// the last turn's page is not the page this one has open.
	const act = hud.activity;
	const browse = steps.findLast((s) => s.kind === 'browse');
	const thisTurn = act?.frameAtMs != null && act.frameAtMs >= (hud.turn.startedAt ?? 0);
	if (act && act.surface === 'browser' && browse && thisTurn) {
		Object.assign(browse, { url: act.url || browse.url, title: act.title || browse.title, image: act.image });
	}

	const asking = hud.approvals.length > 0 || hud.clarifies.length > 0;
	const running = steps.findLast((s) => s.state === 'acting');
	if (asking && running) running.state = 'waiting';
	else if (asking) steps.push({ id: 'waiting', text: 'Waiting on you', state: 'waiting', kind: 'other', ms: null });
	else if (hud.turn.running && !running) {
		// Thinking until the reply streams after the last call: text written before a call
		// ("Let me look.") does not mean the model has stopped working after it.
		const lastDone = Math.max(0, ...hud.turn.tools.map((t) => t.finishedAt ?? 0));
		const streaming = hud.turn.textAt != null && hud.turn.textAt > lastDone;
		if (!streaming) steps.push({ id: 'thinking', text: 'Thinking', state: 'thinking', kind: 'other', ms: null });
	}
	return steps;
}
