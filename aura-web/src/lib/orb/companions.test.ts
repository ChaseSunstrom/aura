// The companions' promises: distinct without colour, stable, and no new hues.

import { describe, expect, it } from 'vitest';

import { TOKENS } from '$lib/tokens';

import { CHOREOGRAPHY, COMPANIONS, companionFor, phaseAt, ringOf } from './companions';
import { GEOMETRY } from './geometry';

describe('the two', () => {
	it('are told apart without colour at all', () => {
		// V3_PLAN §21: under deuteranopia companion hues can simulate to a worst
		// pairwise contrast of 1.00 — the same colour. If two companions ever
		// share a blade density *and* a coil rate, a deuteranope has no way to
		// tell them apart, and the hue is decoration.
		const cues = COMPANIONS.map((c) => `${c.blades}/${c.coilRate}`);
		expect(new Set(cues).size).toBe(COMPANIONS.length);
	});

	it('carries each persona\'s own ring, JARVIS at the full ring', () => {
		expect(COMPANIONS.map((c) => [c.name, c.blades])).toEqual([['AURA', 48], ['JARVIS', GEOMETRY.blades]]);
	});

	it('types no hue of its own', () => {
		// The design system's rule: a colour is typed in design/tokens.json and
		// nowhere else.
		const palette = new Set(Object.values(TOKENS));
		for (const companion of COMPANIONS) expect(palette).toContain(companion.accent);
	});

	it('is a third of the orb, as §19 assigns the subject plane', () => {
		for (const companion of COMPANIONS) expect(companion.scale).toBeCloseTo(1 / 3, 6);
	});
});

describe('which companion a subagent gets', () => {
	it('is the same one every time, so a person can learn it', () => {
		expect(companionFor('search-42')).toBe(companionFor('search-42'));
	});

	it('is never the primary', () => {
		const seen = new Set(
			Array.from({ length: 60 }, (_, i) => companionFor(`job-${i}`).name)
		);
		// The pool is the two less the primary (ADR-0043): AURA, and never JARVIS.
		expect([...seen]).toEqual(['AURA']);
		// With AURA primary, JARVIS is the worker and AURA leaves the pool.
		const withAura = new Set(Array.from({ length: 60 }, (_, i) => companionFor(`job-${i}`, 'AURA').name));
		expect([...withAura]).toEqual(['JARVIS']);
	});
});

describe('arriving and leaving', () => {
	it('fades in and grows, and settles at full', () => {
		const born = { spawnedAt: 10 };
		expect(phaseAt(born, 9.9).opacity).toBe(0);
		const early = phaseAt(born, 10 + CHOREOGRAPHY.spawn * 0.2);
		const late = phaseAt(born, 10 + CHOREOGRAPHY.spawn * 0.8);
		expect(early.opacity).toBeLessThan(late.opacity);
		expect(early.scale).toBeLessThan(late.scale);
		const settled = phaseAt(born, 10 + CHOREOGRAPHY.spawn + 1);
		expect(settled).toEqual({ opacity: 1, scale: 1, alive: true });
	});

	it('fades out without shrinking, so it reads as finished and not destroyed', () => {
		const life = { spawnedAt: 0, endedAt: 20 };
		const going = phaseAt(life, 20 + CHOREOGRAPHY.despawn / 2);
		expect(going.opacity).toBeLessThan(1);
		expect(going.opacity).toBeGreaterThan(0);
		expect(going.scale).toBe(1);
	});

	it('reports when it is gone, so the caller can drop it', () => {
		const life = { spawnedAt: 0, endedAt: 20 };
		expect(phaseAt(life, 20 + CHOREOGRAPHY.despawn * 0.9).alive).toBe(true);
		// Past the end rather than exactly on it: `20 + 0.4 - 20` is
		// 0.3999999999999986 in binary floating point, so an assertion on the
		// boundary itself tests IEEE 754 and not the choreography.
		expect(phaseAt(life, 20 + CHOREOGRAPHY.despawn * 1.1).alive).toBe(false);
	});

	it('crossfades without scaling under reduced motion', () => {
		// The brief: "companions appear and disappear with a crossfade instead
		// of a boot sequence. Everything still functions and every state is
		// still legible." So the fade stays and only the movement goes.
		const half = phaseAt({ spawnedAt: 0 }, CHOREOGRAPHY.spawn / 2, true);
		expect(half.scale).toBe(1);
		expect(half.opacity).toBeGreaterThan(0);
		expect(half.opacity).toBeLessThan(1);
	});
});

describe('where they sit', () => {
	it('puts the first one in the same place whatever the count', () => {
		// A layout that reshuffled when one finished would make the crowding
		// case unsolvable and the scene unreadable in the meantime.
		const one = ringOf(['a']);
		const three = ringOf(['a', 'b', 'c']);
		expect(three[0].x).toBeCloseTo(one[0].x, 12);
		expect(three[0].y).toBeCloseTo(one[0].y, 12);
	});

	it('keeps them behind the orb, so none occludes the instrument', () => {
		for (const placed of ringOf(['a', 'b', 'c', 'd'])) expect(placed.z).toBeLessThan(0);
	});

	it('spreads them rather than stacking them', () => {
		const placed = ringOf(['a', 'b', 'c']);
		for (let i = 0; i < placed.length; i++) {
			for (let j = i + 1; j < placed.length; j++) {
				expect(Math.hypot(placed[i].x - placed[j].x, placed[i].y - placed[j].y)).toBeGreaterThan(
					0.5
				);
			}
		}
	});

	it('is empty for no subagents', () => {
		expect(ringOf([])).toEqual([]);
	});
});
