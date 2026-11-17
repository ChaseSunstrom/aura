import { describe, expect, it } from 'vitest';

import { MS } from './motion';
import { POSES, SCENES, createStage, go, regrowFrom, view, type Scene, type Stage } from './stage';

const EXIT = MS.exit;
const APPEAR = MS.appear;

const pairs: [Scene, Scene][] = SCENES.flatMap((a) =>
	SCENES.filter((b) => b !== a).map((b) => [a, b] as [Scene, Scene])
);

function settledAt(scene: Scene): Stage {
	return createStage(scene, 0);
}

describe('the stage machine', () => {
	it('knows seven scenes, each with a pose', () => {
		expect(SCENES).toEqual(['idle', 'listening', 'answer', 'thinking', 'music', 'alert', 'working']);
		for (const s of SCENES) expect(POSES[s].scale).toBeGreaterThan(0);
	});

	it('a settled stage is still, whole and posed', () => {
		const v = view(settledAt('answer'), 1000);
		expect(v).toMatchObject({ scene: 'answer', target: 'answer', phase: 'still', presence: 1 });
		expect(v.leaving).toBe(false);
		expect(v.settled).toBe(true);
		expect(v.core).toMatchObject(POSES.answer);
	});

	it('going to the scene already on stage changes nothing', () => {
		const s = settledAt('idle');
		expect(go(s, 'idle', 50)).toBe(s);
		const moving = go(s, 'music', 0);
		expect(go(moving, 'music', 100)).toBe(moving);
	});

	it('working lifts the core to the top and shrinks it into the strip', () => {
		expect(POSES.working.y).toBeLessThan(POSES.idle.y);
		expect(POSES.working.scale).toBeLessThan(0.5);
		expect(POSES.working.state).toBe('working');
	});

	describe.each(pairs)('%s → %s', (a, b) => {
		const moving = go(settledAt(a), b, 0);

		it('the outgoing scene leaves first, fully, and only then the incoming one comes out', () => {
			for (let t = 0; t <= EXIT + APPEAR; t += 5) {
				const v = view(moving, t);
				if (t < EXIT) {
					expect(v.scene).toBe(a);
					expect(v.leaving).toBe(true);
					expect(v.presence).toBeCloseTo(1 - t / EXIT, 6);
				} else if (t < EXIT + APPEAR) {
					expect(v.scene).toBe(b);
					expect(v.leaving).toBe(false);
					expect(v.phase).toBe('entering');
					expect(v.presence).toBeCloseTo((t - EXIT) / APPEAR, 6);
				}
			}
			const end = view(moving, EXIT + APPEAR);
			expect(end).toMatchObject({ scene: b, phase: 'still', presence: 1, settled: true });
		});

		it('the core tweens the whole way, without a jump, and lands on the new pose', () => {
			let prev = view(moving, 0).core;
			expect(prev.y).toBeCloseTo(POSES[a].y, 9);
			expect(prev.scale).toBeCloseTo(POSES[a].scale, 9);
			const dy = Math.abs(POSES[b].y - POSES[a].y);
			const ds = Math.abs(POSES[b].scale - POSES[a].scale);
			for (let t = 5; t <= EXIT + APPEAR; t += 5) {
				const c = view(moving, t).core;
				// A 5ms step of a sub-second ease-in-out moves at most a few percent.
				expect(Math.abs(c.y - prev.y)).toBeLessThanOrEqual(dy * 0.05 + 1e-9);
				expect(Math.abs(c.scale - prev.scale)).toBeLessThanOrEqual(ds * 0.05 + 1e-9);
				prev = c;
			}
			// Still travelling halfway through the exit: the core does not wait for the stage.
			const mid = view(moving, EXIT / 2).core;
			if (ds > 0) expect(Math.abs(mid.scale - POSES[a].scale)).toBeGreaterThan(0);
			expect(view(moving, 0).core.state).toBe(POSES[b].state);
			expect(view(moving, EXIT + APPEAR).core).toMatchObject(POSES[b]);
		});

		it('interrupted halfway out, it reverses: the outgoing scene comes back from where it was', () => {
			const t = EXIT / 2;
			const before = view(moving, t);
			const back = go(moving, a, t);
			const after = view(back, t);
			expect(after).toMatchObject({ scene: a, target: a, phase: 'entering', leaving: false });
			expect(after.presence).toBeCloseTo(before.presence, 9);
			expect(after.from).toBeCloseTo(0.5, 9);
			expect(after.core.y).toBeCloseTo(before.core.y, 9);
			expect(after.core.scale).toBeCloseTo(before.core.scale, 9);
			// Half of it had gone, so half an appearance brings it back.
			expect(view(back, t + APPEAR / 2 - 1).phase).toBe('entering');
			expect(view(back, t + APPEAR / 2)).toMatchObject({ scene: a, phase: 'still', settled: true });
			expect(view(back, t + APPEAR / 2).core).toMatchObject(POSES[a]);
		});

		it('interrupted halfway in, the incoming scene retracts from where it was before the old one returns', () => {
			const t = EXIT + APPEAR / 2;
			const before = view(moving, t);
			const back = go(moving, a, t);
			const after = view(back, t);
			expect(after).toMatchObject({ scene: b, target: a, phase: 'leaving', leaving: true });
			expect(after.presence).toBeCloseTo(before.presence, 9);
			expect(after.core.y).toBeCloseTo(before.core.y, 9);
			expect(after.core.scale).toBeCloseTo(before.core.scale, 9);
			const gone = t + EXIT / 2;
			expect(view(back, gone - 1).scene).toBe(b);
			expect(view(back, gone)).toMatchObject({ scene: a, phase: 'entering' });
			expect(view(back, gone + APPEAR)).toMatchObject({ scene: a, phase: 'still' });
			expect(view(back, gone + APPEAR).core).toMatchObject(POSES[a]);
		});
	});

	it('redirected mid-exit to a third scene, the exit carries on and the third comes out', () => {
		const moving = go(settledAt('idle'), 'music', 0);
		const redirected = go(moving, 'alert', EXIT / 2);
		const v = view(redirected, EXIT / 2);
		expect(v).toMatchObject({ scene: 'idle', target: 'alert', leaving: true });
		expect(v.presence).toBeCloseTo(0.5, 9);
		expect(view(redirected, EXIT)).toMatchObject({ scene: 'alert', phase: 'entering' });
		expect(view(redirected, EXIT + APPEAR).core).toMatchObject(POSES.alert);
	});

	it('reduced motion: every change lands at once', () => {
		const s = createStage('idle', 0, { exit: 0, appear: 0 });
		const v = view(go(s, 'working', 10), 10);
		expect(v).toMatchObject({ scene: 'working', phase: 'still', settled: true });
		expect(v.core).toMatchObject(POSES.working);
	});

	it('a clock that runs backwards does not unwind a transition', () => {
		const moving = go(settledAt('idle'), 'alert', 100);
		expect(view(moving, 0)).toMatchObject({ scene: 'idle', presence: 1 });
	});
});

// T-0974: a screen opening on a phone shrank the core to its new box at once and only slid it
// (the owner, 2026-11-10: "make the core above the settings/opened menus transition/animate
// smoother"). Its first frame is its old size where it stood; the box is drawn centred on its
// anchor (translate(-50%, -50%)), so the scale about the box's centre must not move it.
describe('regrowFrom', () => {
	/** Where the core's centre is drawn, as an offset from its box's centre, and how wide. */
	const drawn = (k: number, w: number, h: number) => {
		const f = regrowFrom(k, w, h);
		const [tx, ty] = String(f.translate).split(' ').map(parseFloat);
		const s = Number(f.scale);
		return { x: s * (-w / 2) + tx, y: s * (-h / 2) + ty, width: s * w };
	};

	it('starts at the old size, centred where the core stood', () => {
		for (const [k, w, h] of [[2, 100, 100], [0.5, 240, 240], [1.6, 108, 130]] as const) {
			const at = drawn(k, w, h);
			expect(at.x).toBeCloseTo(-w / 2);
			expect(at.y).toBeCloseTo(-h / 2);
			expect(at.width).toBeCloseTo(k * w);
		}
	});

	it('is still at a ratio of one', () => {
		expect(regrowFrom(1, 200, 200)).toEqual({ translate: '0px 0px', scale: '1' });
	});
});
