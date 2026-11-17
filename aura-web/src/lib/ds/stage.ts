// The stage: which scene is on, what is leaving, where the core is.
//
// A pure state machine — no timers, no DOM. The host passes the time in
// (`performance.now()` in a frame loop, a number in a test) and renders what
// `view()` returns. Any scene can go to any scene:
//
//   1. The outgoing scene leaves first, fully (MS.exit): its projections
//      collapse back into the core, lines retract, the strip folds, the
//      subtitles fade. The host renders the outgoing scene with `leaving` set.
//   2. Then the incoming scene comes out (MS.appear).
//   3. The core tweens its position and size the whole way — through the exit
//      and the entrance — and switches its motion state at once, so the
//      engine's own tween runs alongside.
//
// An interruption reverses cleanly: whatever is on stage turns round from the
// presence it had reached (half gone → half an appearance to come back), and
// the core starts its new tween from where it is, so nothing jumps.
//
// Not covered: what each scene contains. That is the shell's composition; the
// stage only says which scene is on and how present it is.

import { MS, easeTravel } from './motion';
import type { CoreState } from './core/states';

export const SCENES = ['idle', 'listening', 'answer', 'thinking', 'music', 'alert', 'working'] as const;

export type Scene = (typeof SCENES)[number];

/** Where the core sits: `y` is its centre as a fraction of the stage's height. */
export interface CorePose {
	y: number;
	scale: number;
}

export interface ScenePose extends CorePose {
	state: CoreState;
}

/**
 * The core's pose per scene (the Core v2 board). Working lifts it to the top
 * and shrinks it to a third so it sits in the presence strip; answering
 * pulls it back to make room for projections.
 */
export const POSES: Readonly<Record<Scene, ScenePose>> = {
	idle: { y: 0.44, scale: 1, state: 'idle' },
	listening: { y: 0.44, scale: 1.06, state: 'listening' },
	answer: { y: 0.44, scale: 0.8, state: 'speaking' },
	thinking: { y: 0.44, scale: 0.9, state: 'thinking' },
	music: { y: 0.44, scale: 0.92, state: 'music' },
	alert: { y: 0.44, scale: 0.82, state: 'alert' },
	working: { y: 0.17, scale: 0.34, state: 'working' }
};

export type Phase = 'still' | 'leaving' | 'entering';

export interface StageTimings {
	exit: number;
	appear: number;
}

export const STAGE_TIMINGS: StageTimings = { exit: MS.exit, appear: MS.appear };

/** The machine's state. Treat as opaque; read it through `view()`. */
export interface Stage {
	readonly shown: Scene;
	readonly target: Scene;
	readonly phase: Phase;
	/** Presence of `shown` when the phase began (1 = whole, 0 = gone). */
	readonly from: number;
	readonly t0: number;
	readonly coreFrom: CorePose;
	readonly coreT0: number;
	readonly coreDur: number;
	readonly timings: StageTimings;
}

export interface StageView {
	/** The scene to render. Only ever one: the incoming waits for the outgoing. */
	scene: Scene;
	/** Where the stage is going. */
	target: Scene;
	phase: Phase;
	/** Render the scene's exit (collapse, retract, fold, fade). */
	leaving: boolean;
	/** How much of the scene is out, 0..1, linear in time. */
	presence: number;
	/**
	 * Presence when the current animation began: 0 for a fresh entrance, 1 for
	 * a fresh exit, between for a reversal. A component offsets its CSS
	 * animation by it (`animationOffset`) so a reversal continues rather than
	 * restarts.
	 */
	from: number;
	/** Nothing is moving: the scene is whole and the core has arrived. */
	settled: boolean;
	core: ScenePose;
}

export function createStage(scene: Scene, now = 0, timings: StageTimings = STAGE_TIMINGS): Stage {
	return {
		shown: scene,
		target: scene,
		phase: 'still',
		from: 1,
		t0: now,
		coreFrom: { y: POSES[scene].y, scale: POSES[scene].scale },
		coreT0: now,
		coreDur: 0,
		timings
	};
}

/** Presence of the shown scene at `now`, unclamped phases rolled over. */
function settle(s: Stage, now: number): Stage {
	let cur = s;
	// At most two roll-overs (leaving → entering → still); the loop bound is a guard.
	for (let i = 0; i < 3; i++) {
		const dt = Math.max(0, now - cur.t0);
		if (cur.phase === 'leaving') {
			const left = cur.from * cur.timings.exit;
			if (dt < left) return cur;
			cur = { ...cur, shown: cur.target, phase: 'entering', from: 0, t0: cur.t0 + left };
		} else if (cur.phase === 'entering') {
			const left = (1 - cur.from) * cur.timings.appear;
			if (dt < left) return cur;
			cur = { ...cur, phase: 'still', from: 1, t0: cur.t0 + left };
		} else {
			return cur;
		}
	}
	return cur;
}

function presenceOf(s: Stage, now: number): number {
	const dt = Math.max(0, now - s.t0);
	if (s.phase === 'leaving') {
		return s.timings.exit > 0 ? Math.max(0, s.from - dt / s.timings.exit) : 0;
	}
	if (s.phase === 'entering') {
		return s.timings.appear > 0 ? Math.min(1, s.from + dt / s.timings.appear) : 1;
	}
	return 1;
}

function coreAt(s: Stage, now: number): CorePose {
	const to = POSES[s.target];
	const k = s.coreDur > 0 ? easeTravel(Math.max(0, now - s.coreT0) / s.coreDur) : 1;
	// Land exactly: `a + (b - a) * 1` is not always `b` in floating point.
	if (k >= 1) return { y: to.y, scale: to.scale };
	return {
		y: s.coreFrom.y + (to.y - s.coreFrom.y) * k,
		scale: s.coreFrom.scale + (to.scale - s.coreFrom.scale) * k
	};
}

/**
 * Head for `target` at `now`. Returns the same object when nothing changes
 * (already heading there), so a host can compare by identity.
 */
export function go(stage: Stage, target: Scene, now: number): Stage {
	if (target === stage.target) return stage;
	const s = settle(stage, now);
	const presence = presenceOf(s, now);
	const core = coreAt(stage, now);
	const { exit, appear } = s.timings;
	let next: Stage;
	if (target === s.shown) {
		// Turn round: what was leaving comes back from where it got to.
		next = { ...s, target, phase: 'entering', from: presence, t0: now };
	} else if (s.phase === 'leaving') {
		// Already on its way out: keep going, just to somewhere else.
		next = { ...s, target, from: presence, t0: now };
	} else {
		// Still or entering: start (or turn into) the exit from here.
		next = { ...s, target, phase: 'leaving', from: presence, t0: now };
	}
	const remaining =
		next.phase === 'entering' ? (1 - next.from) * appear : next.from * exit + appear;
	return settle({ ...next, coreFrom: core, coreT0: now, coreDur: remaining }, now);
}

export function view(stage: Stage, now: number): StageView {
	const s = settle(stage, now);
	const presence = presenceOf(s, now);
	const pose = coreAt(s, now);
	const arrived = s.coreDur <= 0 || now - s.coreT0 >= s.coreDur;
	return {
		scene: s.shown,
		target: s.target,
		phase: s.phase,
		leaving: s.phase === 'leaving',
		presence,
		from: s.from,
		settled: s.phase === 'still' && arrived,
		core: { ...pose, state: POSES[s.target].state }
	};
}

/**
 * The negative `animation-delay` (ms) that starts a component's CSS animation
 * at the stage's `from`, so a reversal picks up where the other direction
 * stopped. `exit` and `appear` are mirror keyframes, which is what makes the
 * offset meaningful.
 */
export function animationOffset(leaving: boolean, from: number, timings = STAGE_TIMINGS): number {
	const f = Math.min(1, Math.max(0, from));
	return leaving ? -(1 - f) * timings.exit : -f * timings.appear;
}

/**
 * The first frame of the core growing or shrinking into a new box instead of jumping to it
 * (T-0974): [k] is its old size over its new one, [w]×[h] the new box. The box is drawn
 * centred on its anchor (translate(-50%, -50%)), so a scale about the box's own centre also
 * moves it; the translate puts it back. The last frame is `{ translate: '0px 0px', scale: '1' }`.
 * Does not cover a core that also moved on the stage between the two boxes.
 */
export function regrowFrom(k: number, w: number, h: number): Keyframe {
	return { translate: `${((k - 1) * w) / 2}px ${((k - 1) * h) / 2}px`, scale: `${k}` };
}
