// The core's parameter model: what each state looks like, how a change travels,
// and the inputs that move it. Both renderers draw from this, so a state that
// is wrong here is wrong on the GPU and on the fallback alike.
import { describe, expect, it } from 'vitest';

import { TOKENS } from '$lib/tokens';
import { MS } from '../motion';
import {
	FRAME,
	LOOKS,
	RINGS,
	segmentOf,
	createCore,
	fibonacci,
	frozenAt,
	kick,
	rgbOf,
	ringPose,
	segmentLit,
	setState,
	shellInto,
	shocks,
	step
} from './engine';
import { CORE_STATES, type CoreState } from './states';

/** Step a core from `from` to `to` (ms) at 60fps. */
function run(core: ReturnType<typeof createCore>, from: number, to: number, inputs = {}) {
	for (let t = from + FRAME; t <= to; t += FRAME) step(core, t, inputs);
}

const PARAMS = ['spin', 'gyro', 'align', 'shell', 'swirl', 'flow', 'glow', 'work'] as const;

describe('the nine looks', () => {
	it('every pair of states differs in at least one parameter or colour', () => {
		for (const a of CORE_STATES)
			for (const b of CORE_STATES) {
				if (a === b) continue;
				const same =
					PARAMS.every((p) => LOOKS[a][p] === LOOKS[b][p]) &&
					LOOKS[a].color.every((c, i) => c === LOOKS[b].color[i]);
				expect(same, `${a} and ${b} look the same`).toBe(false);
			}
	});

	it('takes its colours from the tokens: cyan at rest, violet for thought, pink for alarm', () => {
		expect(LOOKS.idle.color).toEqual(rgbOf(TOKENS['--jv-accent']));
		expect(LOOKS.thinking.color).toEqual(rgbOf(TOKENS['--jv-violet']));
		expect(LOOKS.embedding.color).toEqual(rgbOf(TOKENS['--jv-violet']));
		expect(LOOKS.alert.color).toEqual(rgbOf(TOKENS['--jv-danger']));
		expect(LOOKS.speaking.color).toEqual(rgbOf(TOKENS['--jv-core-speak']));
		expect(LOOKS.working.color).toEqual(rgbOf(TOKENS['--jv-core-work']));
	});

	it('reads a hex colour, and refuses one it cannot read', () => {
		expect(rgbOf('#3fd0e8')).toEqual([63, 208, 232]);
		expect(() => rgbOf('cyan')).toThrow();
	});
});

describe('a state change', () => {
	it('starts on its look, with nothing to announce', () => {
		const core = createCore('thinking', 0);
		expect(core.look.spin).toBe(LOOKS.thinking.spin);
		expect(shocks(core, 0)).toEqual([]);
		expect(kick(core, 0)).toBe(0);
	});

	it('fires one shock ring and a kick; the same state again fires nothing', () => {
		const core = createCore('idle', 0);
		expect(setState(core, 'thinking', 100)).toBe(true);
		expect(shocks(core, 100)).toEqual([0]);
		expect(kick(core, 100)).toBe(1);
		expect(setState(core, 'thinking', 200)).toBe(false);
		expect(shocks(core, 200)).toHaveLength(1);
	});

	it('the kick decays over the kick time and the ring is gone after the shock time', () => {
		const core = createCore('idle', 0);
		setState(core, 'alert', 0);
		run(core, 0, MS.kick / 2);
		expect(kick(core, MS.kick / 2)).toBeCloseTo(0.5, 1);
		run(core, MS.kick / 2, MS.shock + FRAME);
		expect(kick(core, MS.shock)).toBe(0);
		expect(shocks(core, MS.shock + FRAME)).toEqual([]);
	});

	it('the ring grows outward: its progress runs 0 → 1 over its life', () => {
		const core = createCore('idle', 0);
		setState(core, 'listening', 0);
		run(core, 0, MS.shock / 2);
		const [f] = shocks(core, MS.shock / 2);
		expect(f).toBeCloseTo(0.5, 1);
	});

	it('keeps at most four rings however fast the states change', () => {
		const core = createCore('idle', 0);
		for (let i = 0; i < 20; i++) setState(core, i % 2 ? 'idle' : 'acting', i);
		expect(shocks(core, 20).length).toBeLessThanOrEqual(4);
	});

	it('tweens toward the new look rather than jumping', () => {
		const core = createCore('idle', 0);
		setState(core, 'thinking', 0);
		step(core, FRAME);
		const moved = (core.look.spin - LOOKS.idle.spin) / (LOOKS.thinking.spin - LOOKS.idle.spin);
		expect(moved).toBeCloseTo(0.035, 3);
		run(core, FRAME, 4000);
		expect(core.look.spin).toBeCloseTo(LOOKS.thinking.spin, 2);
		expect(core.look.color[0]).toBeCloseTo(LOOKS.thinking.color[0], 0);
	});

	it('the tween is the same at 120Hz as at 60Hz', () => {
		const a = createCore('idle', 0);
		const b = createCore('idle', 0);
		setState(a, 'working', 0);
		setState(b, 'working', 0);
		// Integer counters: `t += FRAME` drifts, and one loop would skip its last frame.
		for (let i = 1; i <= 60; i++) step(a, i * FRAME);
		for (let i = 1; i <= 120; i++) step(b, (i * FRAME) / 2);
		expect(b.look.flow).toBeCloseTo(a.look.flow, 3);
	});

	it('a persona tint replaces the state colour, tweened, and lets go of it again', () => {
		const aura = rgbOf(TOKENS['--jv-persona-aura']);
		const core = createCore('idle', 0);
		run(core, 0, 4000, { tint: aura });
		core.look.color.forEach((c, i) => expect(c).toBeCloseTo(aura[i], 0));
		run(core, 4000, 8000);
		core.look.color.forEach((c, i) => expect(c).toBeCloseTo(LOOKS.idle.color[i], 0));
	});

	it('under reduced motion the tint lands in the one frame drawn', () => {
		const aura = rgbOf(TOKENS['--jv-persona-aura']);
		const core = createCore('idle', 0, true);
		step(core, FRAME, { tint: aura });
		expect(core.look.color).toEqual(aura);
	});

	it('snaps, with no ring, when motion is reduced', () => {
		const core = createCore('idle', 0);
		expect(setState(core, 'music', 0, { snap: true })).toBe(true);
		expect(core.look).toEqual({ ...LOOKS.music, color: [...LOOKS.music.color] });
		expect(shocks(core, 0)).toEqual([]);
	});

	it('a page that comes back after a minute does not spin a minute in one frame', () => {
		const core = createCore('thinking', 0);
		step(core, FRAME);
		const before = core.spinA;
		step(core, 60_000);
		expect(core.spinA - before).toBeLessThan(0.001 * LOOKS.thinking.spin * 101);
	});
});

describe('the working beat', () => {
	it('beats once per beat time while working, each with a ring', () => {
		const core = createCore('idle', 0);
		setState(core, 'working', 0);
		run(core, 0, MS.beat * 3 + FRAME);
		expect(core.beats).toBe(3);
	});

	it('never beats in any other state', () => {
		for (const s of CORE_STATES.filter((x) => x !== 'working')) {
			const core = createCore(s, 0);
			run(core, 0, MS.beat * 3);
			expect(core.beats, s).toBe(0);
		}
	});
});

describe('amplitude', () => {
	const ampOf = (state: CoreState, inputs = {}, at = 1000) => {
		const core = createCore(state, 0);
		run(core, 0, at, inputs);
		return core.amp;
	};

	it('follows the mic while listening, the voice while speaking and the beat in music', () => {
		expect(ampOf('listening', { level: 0.7 })).toBeCloseTo(0.7);
		expect(ampOf('speaking', { envelope: 0.4 })).toBeCloseTo(0.4);
		expect(ampOf('music', { music: 0.9 })).toBeCloseTo(0.9);
	});

	it('is synthesized when the input is missing, and stays in 0..1', () => {
		for (const s of ['listening', 'speaking', 'music'] as const)
			for (let at = 100; at < 4000; at += 300) {
				const a = ampOf(s, {}, at);
				expect(a, `${s} at ${at}`).toBeGreaterThanOrEqual(0);
				expect(a, `${s} at ${at}`).toBeLessThanOrEqual(1);
			}
	});

	it('clamps a level out of range and reads a broken one as silence', () => {
		expect(ampOf('listening', { level: 5 })).toBe(1);
		expect(ampOf('listening', { level: -1 })).toBe(0);
		expect(ampOf('listening', { level: Number.NaN })).toBe(0);
	});

	it('is zero in the states that do not listen, speak or play', () => {
		for (const s of ['idle', 'thinking', 'embedding', 'acting', 'alert', 'working'] as const)
			expect(ampOf(s, { level: 1, envelope: 1, music: 1 }), s).toBe(0);
	});
});

describe('geometry', () => {
	it('spreads the points evenly over the unit sphere, about 3% of them hot', () => {
		const { pos, hot } = fibonacci(1500);
		expect(pos).toHaveLength(4500);
		for (let i = 0; i < 1500; i++)
			expect(Math.hypot(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2])).toBeCloseTo(1, 5);
		const share = hot.reduce((n, h) => n + h, 0) / 1500;
		expect(share).toBeGreaterThan(0.015);
		expect(share).toBeLessThan(0.05);
		expect(fibonacci(1500).hot).toEqual(hot);
	});

	it('draws the shell at its look size, smaller while thinking', () => {
		const pts = fibonacci(300);
		const radius = (state: CoreState) => {
			const core = createCore(state, 0);
			run(core, 0, 500);
			const out = new Float32Array(300 * 4);
			shellInto(core, pts, out);
			let sum = 0;
			for (let i = 0; i < 300; i++) sum += Math.hypot(out[i * 4], out[i * 4 + 1], out[i * 4 + 2]);
			return sum / 300;
		};
		expect(radius('idle')).toBeCloseTo(1, 1);
		expect(radius('thinking')).toBeCloseTo(0.84, 1);
	});

	it('keeps the even spacing as the shell turns, so it never clumps into a speckle (T-0749)', () => {
		const pts = fibonacci(300);
		const closest = (shellA: number) => {
			const core = createCore('idle', 0);
			core.shellA = shellA;
			const out = new Float32Array(300 * 4);
			shellInto(core, pts, out);
			let min = Infinity;
			for (let i = 0; i < 300; i++)
				for (let j = i + 1; j < 300; j++)
					min = Math.min(min, Math.hypot(out[i * 4] - out[j * 4], out[i * 4 + 1] - out[j * 4 + 1], out[i * 4 + 2] - out[j * 4 + 2]));
			return min;
		};
		// Each point at its own speed drifted the golden spiral into clumps and holes in a minute.
		expect(closest(40)).toBeGreaterThan(closest(0) * 0.9);
	});

	it('fades points in and out of the shell while embedding, and only then', () => {
		const pts = fibonacci(300);
		const alphas = (state: CoreState) => {
			const core = createCore(state, 0);
			run(core, 0, 1500);
			const out = new Float32Array(300 * 4);
			shellInto(core, pts, out);
			return Array.from({ length: 300 }, (_, i) => out[i * 4 + 3]);
		};
		const embed = alphas('embedding');
		expect(Math.min(...embed)).toBeGreaterThanOrEqual(0.25);
		expect(Math.max(...embed)).toBeLessThanOrEqual(1);
		expect(Math.max(...embed) - Math.min(...embed)).toBeGreaterThan(0.5);
		expect(new Set(alphas('idle'))).toEqual(new Set([1]));
	});

	it('lines the rings up facing the viewer while listening', () => {
		const core = createCore('listening', 0);
		run(core, 0, 500);
		RINGS.forEach((_, ri) => {
			const { a, b } = ringPose(core, ri);
			expect(Math.abs(a), `ring ${ri}`).toBeLessThan(0.01);
			expect(Math.abs(b), `ring ${ri}`).toBeLessThan(0.1);
		});
	});

	it('turns the rings apart at rest', () => {
		const core = createCore('idle', 0);
		const poses = RINGS.map((_, ri) => ringPose(core, ri));
		expect(poses[0].a).toBeGreaterThan(1);
		expect(Math.sign(poses[1].b)).not.toBe(Math.sign(poses[2].b));
	});

	it('pulses the rings with the voice and the beat, and nowhere else', () => {
		const at = (state: CoreState) => {
			const core = createCore(state, 0);
			run(core, 0, 1000, { envelope: 1, music: 1 });
			return ringPose(core, 0).pulse;
		};
		expect(at('speaking')).toBeCloseTo(1.05);
		expect(at('music')).toBeCloseTo(1.05);
		expect(at('idle')).toBe(1);
	});

	it('lights the segments in a running wave while thinking or playing, steady otherwise', () => {
		const lit = (state: CoreState) => {
			const core = createCore(state, 0);
			run(core, 0, 700);
			return Array.from({ length: 12 }, (_, k) => segmentLit(core, k));
		};
		expect(new Set(lit('idle'))).toEqual(new Set([1]));
		for (const s of ['thinking', 'music'] as const) {
			const l = lit(s);
			expect(Math.max(...l) - Math.min(...l), s).toBeGreaterThan(0.2);
		}
	});
});

describe('a frozen frame', () => {
	it('is the same frame every time', () => {
		const a = frozenAt('thinking', 'idle', 600);
		const b = frozenAt('thinking', 'idle', 600);
		expect(a).toEqual(b);
	});

	it('is the frame `at` ms after a change, ring and all', () => {
		const changed = frozenAt('alert', 'idle', 450);
		expect(shocks(changed, changed.t)).toHaveLength(1);
		expect(frozenAt('alert', 'alert', 450).changes).toBe(0);
		expect(shocks(frozenAt('alert', 'idle', 1000), 1000)).toEqual([]);
	});

	it('bounds a time it was handed: a huge or broken one is not a hang', () => {
		expect(frozenAt('idle', 'idle', 1e12).t).toBeLessThanOrEqual(60_000);
		expect(frozenAt('idle', 'idle', Number.NaN).t).toBe(0);
	});
});

// The owner, 2026-11-08: the three rings were one ring at three sizes. Each has its own weight,
// gap and step now (T-0748).
describe('the rings are three different rings', () => {
	const segs = RINGS.map((_, ri) => segmentOf(ri));

	it('gets lighter and more open toward the core', () => {
		for (let i = 1; i < segs.length; i++) {
			expect(segs[i].inner + segs[i].outer).toBeLessThan(segs[i - 1].inner + segs[i - 1].outer);
			expect(segs[i].gap).toBeGreaterThan(segs[i - 1].gap);
		}
	});

	it('is not evenly spaced', () => {
		const steps = [RINGS[0].r - RINGS[1].r, RINGS[1].r - RINGS[2].r];
		expect(Math.abs(steps[0] - steps[1])).toBeGreaterThan(0.01);
	});

	it("keeps each ring's band clear of the next one's, and a gap narrower than its segment", () => {
		for (let i = 1; i < segs.length; i++) expect(RINGS[i - 1].r - segs[i - 1].inner).toBeGreaterThan(RINGS[i].r + segs[i].outer);
		RINGS.forEach((R, ri) => expect(segs[ri].gap).toBeLessThan((Math.PI * 2) / R.seg / 2));
	});
});
