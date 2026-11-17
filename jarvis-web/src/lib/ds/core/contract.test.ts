// The core's numbers as the phone must reproduce them (T-0712).
//
// The phone draws this core natively (android-app reactor/core/CoreEngine.kt, a port of
// engine.ts and states.ts), so the two are bound by one table both suites read:
// tests/contracts/core_frames.json. This test computes the table from engine.ts and fails
// when the file differs; `UPDATE_CONTRACT=1 npx vitest run src/lib/ds/core/contract.test.ts`
// rewrites it. CoreEngineTest checks the Kotlin against the same file.
//
// Does not cover the drawing: canvas2d.ts and its Kotlin port are held to the pictures
// (docs/screens/v2/core and the phone's goldens), not to numbers.

import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { MS } from '../motion';
import {
	BOARD_WIDTH,
	CAMERA,
	FLOW,
	LOOKS,
	PALETTE,
	RINGS,
	SEGMENT,
	SPREAD,
	STRUT_IN,
	TILT,
	cometAngle,
	coreGlow,
	fibonacci,
	flowAngle,
	frozenAt,
	kick,
	ringPose,
	segmentOf,
	segmentLit,
	shellInto,
	shockRing,
	shocks,
	type Inputs
} from './engine';
import { CORE_STATES, REACTOR_TO_CORE, type CoreState } from './states';

const PATH = fileURLToPath(new URL('../../../../../tests/contracts/core_frames.json', import.meta.url));

/** Six places: both sides compare to 1e-4, so rounding here never decides a test. */
const round = (x: number) => Math.round(x * 1e6) / 1e6;
const all = (xs: ArrayLike<number>) => Array.from(xs, round);

const SHELL_POINTS = 64;
/** Inputs given, and none (each state then synthesizes its own amplitude from the clock). */
const HEARD: Inputs = { level: 0.4, envelope: 0.6, music: 0.3 };
const TIMES = [0, 120, 600, 2000];

function frame(state: CoreState, from: CoreState, at: number, inputs: Inputs) {
	const core = frozenAt(state, from, at, inputs);
	const shell = fibonacci(SHELL_POINTS);
	const out = new Float32Array(SHELL_POINTS * 4);
	shellInto(core, shell, out);
	const glow = coreGlow(core);
	return {
		state,
		from,
		at,
		inputs: inputs === HEARD ? 'heard' : 'none',
		look: { ...Object.fromEntries(Object.entries(core.look).filter(([k]) => k !== 'color').map(([k, v]) => [k, round(v as number)])), color: all(core.look.color) },
		t: round(core.t),
		spinA: round(core.spinA),
		gyroA: round(core.gyroA),
		shellA: round(core.shellA),
		flowA: round(core.flowA),
		amp: round(core.amp),
		beat: round(core.beat),
		beats: core.beats,
		changes: core.changes,
		kick: round(kick(core, core.t)),
		shocks: all(shocks(core, core.t)),
		glow: { glow: round(glow.glow), radius: round(glow.radius) },
		rings: RINGS.map((_, ri) => {
			const p = ringPose(core, ri);
			return { a: round(p.a), b: round(p.b), pulse: round(p.pulse), flow: [0, 5, 25].map((i) => round(flowAngle(core, ri, i))), comet: round(cometAngle(core, ri)) };
		}),
		lit: [0, 1, 2, 3].map((k) => round(segmentLit(core, k))),
		// Every fourth point: x, y, z before the tilt, and alpha.
		shell: all(out.filter((_, i) => Math.floor(i / 4) % 8 === 0))
	};
}

function table() {
	const shell = fibonacci(SHELL_POINTS);
	const frames = [];
	for (const state of CORE_STATES) {
		const from: CoreState = state === 'idle' ? 'listening' : 'idle';
		for (const at of TIMES) frames.push(frame(state, from, at, HEARD));
		frames.push(frame(state, from, 600, {}));
	}
	return {
		$comment:
			'The v2 core (jarvis-web/src/lib/ds/core/engine.ts) as numbers: written by src/lib/ds/core/contract.test.ts (UPDATE_CONTRACT=1), read by android-app CoreEngineTest. Do not edit by hand.',
		view: { TILT, CAMERA, SPREAD, BOARD_WIDTH, STRUT_IN },
		ms: { beat: MS.beat, shock: MS.shock, kick: MS.kick },
		RINGS,
		SEGMENT,
		segments: RINGS.map((_, ri) => segmentOf(ri)),
		FLOW,
		LOOKS: Object.fromEntries(CORE_STATES.map((s) => [s, LOOKS[s]])),
		PALETTE,
		REACTOR_TO_CORE,
		shockRing: [0, 0.5, 0.9].map((f) => {
			const r = shockRing(f);
			return { f, radius: round(r.radius), width: round(r.width), alpha: round(r.alpha) };
		}),
		fibonacci: {
			n: SHELL_POINTS,
			pos: all(shell.pos.slice(0, 24)),
			hot: Array.from(shell.hot).flatMap((h, i) => (h ? [i] : [])),
			speed: all(shell.speed.slice(0, 8)),
			phase: all(shell.phase.slice(0, 8))
		},
		heard: HEARD,
		frames
	};
}

describe('the core contract (tests/contracts/core_frames.json)', () => {
	it('is what engine.ts computes', () => {
		const now = table();
		const text = `${JSON.stringify(now, null, '\t')}\n`;
		if (process.env.UPDATE_CONTRACT) writeFileSync(PATH, text);
		expect(existsSync(PATH), 'run with UPDATE_CONTRACT=1 to write it').toBe(true);
		expect(JSON.parse(readFileSync(PATH, 'utf8'))).toEqual(JSON.parse(text));
	});

	it('moves when a state changes, so a stale port cannot pass on constants alone', () => {
		const { frames } = table();
		const thinking = frames.find((f) => f.state === 'thinking' && f.at === 2000)!;
		const idle = frames.find((f) => f.state === 'idle' && f.at === 2000)!;
		expect(thinking.spinA).not.toBe(idle.spinA);
		expect(thinking.look.color).not.toEqual(idle.look.color);
	});
});
