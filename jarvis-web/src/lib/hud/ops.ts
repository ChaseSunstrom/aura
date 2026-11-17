// The Ops board's data (BUILD_BRIEF §1 "Ops dashboard"; the Build Brief's Ops board):
// one pure mapping from what the console already holds to the eight panes, so
// every figure on the board is one somebody measured. Where the console holds
// no source for a pane's figure (compute by hour, calls per skill) the pane
// draws the nearest real thing and says so in its subtitle; GAPS.md lists them.

import type { TimelineCall, Job } from '$lib/glass/bodies/types';
import type { CrewNode } from './data/crew';
import type { GpuView } from './data/models';
import type { MemoryEntry } from './data/memory';

export type Tone = 'accent' | 'violet' | 'ok' | 'warn' | 'danger' | 'tick';

export interface HouseState {
	entity_id: string;
	state: string;
	attributes?: { friendly_name?: string };
}

export interface OpsData {
	gpus: { name: string; usedPct: number; segments: { label: string; pct: number }[] }[];
	agents: { id: string; name: string; status: string; working: boolean; tone: Tone }[];
	routes: { from: string; to: string; weight: number; tone: Tone }[];
	memory: { total: number; kinds: { k: string; v: number; tone: Tone }[]; hourly: number[] };
	rooms: { name: string; on: boolean }[];
	devices: { name: string; detail: string; away: boolean }[];
	commands: { text: string; state: TimelineCall['state']; ms?: number | null }[];
	queue: { id: string; title: string; when: string }[];
	health: { name: string; state: 'ok' | 'running' | 'slow' | 'failing' | 'idle' }[];
}

const TONES: readonly Tone[] = ['accent', 'violet', 'ok', 'warn'];

export interface OpsSources {
	gpus: readonly GpuView[];
	crew: readonly CrewNode[];
	model: string;
	toolsets: readonly { name: string; enabled?: boolean }[];
	memory: readonly MemoryEntry[];
	states: readonly HouseState[];
	calls: readonly TimelineCall[];
	jobs: readonly Job[];
	/** Each source's status, as the console's feeds report it. */
	feeds: readonly { name: string; status: string }[];
	now?: number;
}

const nameOf = (s: HouseState) => s.attributes?.friendly_name ?? s.entity_id.split('.')[1].replace(/_/g, ' ');

export function opsData(src: OpsSources): OpsData {
	const now = src.now ?? Date.now();
	const hourly = new Array(24).fill(0) as number[];
	for (const m of src.memory) {
		const hoursAgo = Math.floor((now / 1000 - m.created) / 3600);
		if (hoursAgo >= 0 && hoursAgo < 24) hourly[23 - hoursAgo]++;
	}
	const bySource = new Map<string, number>();
	for (const m of src.memory) bySource.set(m.source || 'other', (bySource.get(m.source || 'other') ?? 0) + 1);
	const enabled = src.toolsets.filter((t) => t.enabled !== false);
	return {
		gpus: src.gpus.map((g) => ({ name: `GPU ${g.index}`, usedPct: g.usedPct, segments: g.segments.map((s) => ({ label: s.label, pct: s.pct })) })),
		agents: src.crew.map((c, i) => ({
			id: c.id,
			name: c.type ?? `helper ${i + 1}`,
			status: c.state,
			working: c.state === 'running',
			tone: c.state === 'failed' ? 'danger' : c.state === 'running' ? TONES[i % TONES.length] : 'tick'
		})),
		routes: src.model ? enabled.map((t, i) => ({ from: t.name, to: src.model, weight: 1, tone: TONES[i % TONES.length] })) : [],
		memory: {
			total: src.memory.length,
			kinds: [...bySource].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, v], i) => ({ k, v, tone: TONES[i % TONES.length] })),
			hourly
		},
		rooms: src.states.filter((s) => s.entity_id.startsWith('light.')).slice(0, 8).map((s) => ({ name: nameOf(s), on: s.state === 'on' })),
		devices: src.states
			.filter((s) => s.entity_id.startsWith('device_tracker.'))
			.slice(0, 8)
			.map((s) => ({ name: nameOf(s), detail: s.state.replace(/_/g, ' '), away: s.state !== 'home' })),
		commands: src.calls.slice(-6).map((c) => ({ text: `${c.name}${c.args ? ` ${c.args}` : ''}`, state: c.state, ms: c.ms })),
		queue: src.jobs.slice(0, 4).map((j) => ({ id: j.id, title: j.title, when: j.state === 'running' ? 'running' : (j.next ?? j.state) })),
		health: src.feeds.map((f) => ({
			name: f.name,
			state: f.status === 'ready' ? 'ok' : f.status === 'loading' ? 'running' : f.status === 'error' || f.status === 'offline' ? 'failing' : f.status === 'empty' ? 'idle' : 'slow'
		}))
	};
}

/**
 * The compute pane's isometric bars: one column a GPU, one bar a segment of it,
 * as polygons on a 300×240 view (the board's `iso`). Faces back to front.
 */
export function isoBars(gpus: OpsData['gpus']): { points: string; tone: Tone; shade: number }[] {
	const w = 26;
	// Placed so two GPUs of up to three segments, at full height, stay inside the view.
	const ox = 130;
	const oy = 95;
	const iso = (i: number, j: number) => [ox + (i - j) * w, oy + (i + j) * w * 0.5];
	const pt = (q: number[], h: number) => `${q[0].toFixed(1)},${(q[1] - h).toFixed(1)}`;
	const out: { points: string; tone: Tone; shade: number }[] = [];
	gpus.forEach((g, gi) => {
		const segs = g.segments.length ? g.segments : [{ label: 'used', pct: g.usedPct }];
		segs.forEach((s, si) => {
			const i = gi * 2 + 0.2;
			const j = si * 1.1 + 0.2;
			const h = Math.max(4, (s.pct / 100) * 90);
			const a = iso(i, j), b = iso(i + 1.4, j), c = iso(i + 1.4, j + 0.8), d = iso(i, j + 0.8);
			const tone = TONES[si % TONES.length];
			out.push({ points: [pt(d, h), pt(c, h), pt(c, 0), pt(d, 0)].join(' '), tone, shade: 0.55 });
			out.push({ points: [pt(b, h), pt(c, h), pt(c, 0), pt(b, 0)].join(' '), tone, shade: 0.3 });
			out.push({ points: [pt(a, h), pt(b, h), pt(c, h), pt(d, h)].join(' '), tone, shade: 1 });
		});
	});
	return out;
}
