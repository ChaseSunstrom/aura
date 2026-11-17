import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { ACTIVITY_LINGER_MS, activityLive, MAX_STEPS, reduceActivity, stopActivity, type ActivityState } from './activity';
import { initialHud, reduce, stopActivityLocally, type HudState } from './hud';
import type { HermesEvent } from './protocol';

const CONTRACT = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../tests/contracts/activity-frames.json', import.meta.url)), 'utf8'));

const SID = 'live-1';
const ev = (type: string, payload: Record<string, unknown> = {}, seq = 0): HermesEvent => ({ type, session_id: SID, seq, payload });
const START = CONTRACT.examples['activity.start'] as Record<string, unknown>;
const STEP = CONTRACT.examples['activity.step'] as Record<string, unknown>;
const FRAME = CONTRACT.examples['activity.frame'] as Record<string, unknown>;
const END = CONTRACT.examples['activity.end'] as Record<string, unknown>;

describe('the activity reducer', () => {
	it('folds the four contract events into one running activity and its end', () => {
		let state: ActivityState | null = null;
		state = reduceActivity(state, ev('activity.start', START), 1000);
		expect(state).toMatchObject({ id: 'a_3f9a1c2b', surface: 'browser', intent: 'open https://example.com/login', status: 'running', steps: [] });
		state = reduceActivity(state, ev('activity.step', STEP), 1001);
		expect(state!.steps).toEqual([{ seq: 2, tool: 'browser_type', action: 'type into Password: [redacted]', ok: true, at: STEP.at }]);
		state = reduceActivity(state, ev('activity.frame', FRAME), 1002);
		expect(state).toMatchObject({ url: 'https://example.com/login', title: 'Sign in', image: null, frameSeq: 3 });
		expect(state!.text).toContain('[redacted]');
		state = reduceActivity(state, ev('activity.end', END), 5000);
		expect(state).toMatchObject({ status: 'done', endedAt: END.ended_at, endedAtMs: 5000 });
	});

	it('every contract event names the fields the reducer reads', () => {
		expect(CONTRACT.events).toEqual(['activity.start', 'activity.frame', 'activity.step', 'activity.end']);
		for (const event of CONTRACT.events) {
			const example = CONTRACT.examples[event] as Record<string, unknown>;
			expect(Object.keys(example).sort()).toEqual([...CONTRACT.fields[event]].sort());
		}
	});

	it('a late frame never paints over a newer one, and an older step slots in by seq', () => {
		let state = reduceActivity(null, ev('activity.start', START), 0);
		state = reduceActivity(state, ev('activity.frame', { ...FRAME, seq: 5, title: 'Newer' }), 1);
		state = reduceActivity(state, ev('activity.frame', { ...FRAME, seq: 4, title: 'Older' }), 2);
		expect(state!.title).toBe('Newer');
		state = reduceActivity(state, ev('activity.step', { ...STEP, seq: 3, action: 'three' }), 3);
		state = reduceActivity(state, ev('activity.step', { ...STEP, seq: 1, action: 'one' }), 4);
		expect(state!.steps.map((s) => s.action)).toEqual(['one', 'three']);
	});

	it('a step or a frame for a run it never saw start opens one (a late joiner)', () => {
		const state = reduceActivity(null, ev('activity.frame', FRAME), 0);
		expect(state).toMatchObject({ id: 'a_3f9a1c2b', status: 'running', url: 'https://example.com/login' });
	});

	it('a start replaces the previous run; an end for another id is ignored', () => {
		let state = reduceActivity(null, ev('activity.start', START), 0);
		state = reduceActivity(state, ev('activity.end', { ...END, id: 'a_other' }), 1);
		expect(state!.status).toBe('running');
		state = reduceActivity(state, ev('activity.start', { ...START, id: 'a_next', surface: 'desktop' }), 2);
		expect(state).toMatchObject({ id: 'a_next', surface: 'desktop', steps: [] });
	});

	it('keeps the newest MAX_STEPS steps of a runaway loop', () => {
		let state = reduceActivity(null, ev('activity.start', START), 0);
		for (let seq = 1; seq <= MAX_STEPS + 10; seq++) state = reduceActivity(state, ev('activity.step', { ...STEP, seq }), seq);
		expect(state!.steps).toHaveLength(MAX_STEPS);
		expect(state!.steps[0].seq).toBe(11);
	});

	it('stopped by the person stays stopped through the emitter’s later end', () => {
		let state = reduceActivity(null, ev('activity.start', START), 0);
		state = stopActivity(state, 100);
		expect(state).toMatchObject({ status: 'stopped', endedAtMs: 100 });
		state = reduceActivity(state, ev('activity.end', END), 200);
		expect(state).toMatchObject({ status: 'stopped', endedAt: END.ended_at, endedAtMs: 100 });
		expect(stopActivity(state, 300)).toBe(state);
	});

	it('the tile lingers for a few seconds after the end, then goes', () => {
		let state = reduceActivity(null, ev('activity.start', START), 0);
		expect(activityLive(state, 10_000)).toBe(true);
		state = reduceActivity(state, ev('activity.end', END), 1000);
		expect(activityLive(state, 1000 + ACTIVITY_LINGER_MS - 1)).toBe(true);
		expect(activityLive(state, 1000 + ACTIVITY_LINGER_MS)).toBe(false);
		expect(activityLive(null, 0)).toBe(false);
	});
});

describe('the HUD state', () => {
	it('carries the activity off the session’s events, and stops it locally', () => {
		let hud: HudState = { ...initialHud(), sessionId: SID };
		hud = reduce(hud, ev('activity.start', START), 0);
		hud = reduce(hud, ev('activity.step', STEP), 1);
		expect(hud.activity).toMatchObject({ id: 'a_3f9a1c2b', steps: [{ seq: 2 }] });
		// Another session's activity is not this screen's.
		const other = reduce(hud, { ...ev('activity.step', { ...STEP, seq: 9 }), session_id: 'elsewhere' }, 2);
		expect(other.activity!.steps).toHaveLength(1);
		const stopped = stopActivityLocally(hud, 50);
		expect(stopped.activity!.status).toBe('stopped');
		expect(stopActivityLocally(stopped, 60)).toBe(stopped);
	});
});
