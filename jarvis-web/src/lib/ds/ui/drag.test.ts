import { describe, expect, it } from 'vitest';
import { dismisses } from './drag';

describe('dragging a menu down', () => {
	it('closes past a quarter of its height, however slowly', () => {
		expect(dismisses(140, 0, 557)).toBe(true);
		expect(dismisses(139, 0, 557)).toBe(false);
	});

	it('closes on a flick that has moved, and not on a twitch', () => {
		expect(dismisses(40, 1, 557)).toBe(true);
		expect(dismisses(40, 0.3, 557)).toBe(false);
		expect(dismisses(10, 3, 557)).toBe(false);
	});

	it('never closes on an upward drag or a menu with no height', () => {
		expect(dismisses(0, 5, 557)).toBe(false);
		expect(dismisses(-30, 5, 557)).toBe(false);
		expect(dismisses(200, 1, 0)).toBe(false);
	});
});
