// Reading frame times out of a Chromium trace, shared by the two frame-time
// gates: the room's eight-panel scene (`hud-frametime.spec.ts`) and the map's
// twenty-glyph scene (`hud-map-frametime.spec.ts`).
//
// A frame is one `AnimationFrame` event on the renderer main thread — the whole
// rendering update, script through paint — which is the frame DevTools' timeline
// draws. It is independent of vsync, so an idle page that produces no frame
// counts nothing.
//
// The gated number is the frame's **CPU** time: the thread time (`tdur`) of the
// main-thread tasks that ran inside its span. Wall time is reported beside it
// and never gated, because on a 4-core host under a full suite a frame's wall
// time doubles while the UI did nothing different — the thread was descheduled,
// not busier — and a gate on wall time fails on the runner's neighbours.

import type { Page } from '@playwright/test';

export type TraceEvent = { name?: string; ph?: string; ts: number; dur?: number; tdur?: number; pid?: number; tid?: number; id?: string; id2?: { local?: string; global?: string }; args?: { data?: Record<string, unknown> } };

export const pct = (sorted: number[], p: number) => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * p) - 1))] : 0);
export const stats = (d: number[]) => {
	const s = [...d].sort((a, b) => a - b);
	return { n: s.length, p50: +pct(s, 0.5).toFixed(2), p95: +pct(s, 0.95).toFixed(2), max: +(s[s.length - 1] ?? 0).toFixed(2) };
};

export type Span = { start: number; end: number; ms: number; cpu: number };

/** Pair the b/e halves of an async event; `cpu` is filled in by `withCpu`. */
export function spans(events: TraceEvent[], name: string): Span[] {
	const open = new Map<string, number>();
	const out: Span[] = [];
	for (const e of events) {
		if (e.name !== name) continue;
		const key = `${e.pid}/${e.tid}/${e.id ?? e.id2?.local ?? e.id2?.global ?? ''}`;
		if (e.ph === 'b') open.set(key, e.ts);
		else if (e.ph === 'e' && open.has(key)) {
			const start = open.get(key)!;
			open.delete(key);
			out.push({ start, end: e.ts, ms: (e.ts - start) / 1000, cpu: 0 });
		}
	}
	return out;
}

/**
 * The thread time each span's main-thread tasks spent inside it: `RunTask`
 * complete events carry `dur` (wall) and `tdur` (CPU); a task that straddles
 * a span edge contributes the overlapping fraction of its CPU time.
 */
export function withCpu(events: TraceEvent[], frames: Span[]): Span[] {
	const tasks = events
		.filter((e) => e.name === 'RunTask' && e.ph === 'X' && e.dur !== undefined && e.tdur !== undefined)
		.map((e) => ({ pid: e.pid, tid: e.tid, start: e.ts, end: e.ts + e.dur!, dur: e.dur!, tdur: e.tdur! }))
		.sort((a, b) => a.start - b.start);
	const thread = frames.length ? events.find((e) => e.name === 'AnimationFrame' && e.ph === 'b') : undefined;
	for (const f of frames) {
		let cpu = 0;
		for (const t of tasks) {
			if (t.start >= f.end) break;
			if (t.end <= f.start || t.pid !== thread?.pid || t.tid !== thread?.tid) continue;
			const overlap = Math.min(t.end, f.end) - Math.max(t.start, f.start);
			cpu += t.dur > 0 ? (t.tdur * overlap) / t.dur : 0;
		}
		f.cpu = cpu / 1000;
	}
	return frames;
}

/** The renderer's GL renderer string: which profile the numbers belong to. */
export const glRenderer = (page: Page) =>
	page.evaluate(() => {
		const gl = document.createElement('canvas').getContext('webgl');
		const ext = gl?.getExtension('WEBGL_debug_renderer_info');
		return gl && ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : 'no-webgl';
	});

