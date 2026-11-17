import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { BargeIn, ECHO_DECAY, FLOOR_RATIO, HOLD_MS, LEARN_MS, MIN_LEVEL, OVER_ECHO } from './bargeIn';

// The shared table: android-app's BargeInTest.kt runs the same scenarios.
const contract = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../../tests/contracts/barge-in.json', import.meta.url)), 'utf8')
) as {
	constants: Record<string, number>;
	step_ms: number;
	floor: number;
	scenarios: { name: string; spans: [number, number, number, boolean][] }[];
};

describe('barge-in', () => {
	it('uses the contract constants', () => {
		expect({ LEARN_MS, HOLD_MS, OVER_ECHO, MIN_LEVEL, FLOOR_RATIO, ECHO_DECAY }).toEqual({
			LEARN_MS: contract.constants.learn_ms,
			HOLD_MS: contract.constants.hold_ms,
			OVER_ECHO: contract.constants.over_echo,
			MIN_LEVEL: contract.constants.min_level,
			FLOOR_RATIO: contract.constants.floor_ratio,
			ECHO_DECAY: contract.constants.echo_decay
		});
	});

	for (const scenario of contract.scenarios) {
		it(scenario.name, () => {
			const b = new BargeIn();
			b.reset(0);
			for (const [from, until, level, cut] of scenario.spans) {
				let heard = false;
				for (let t = from; t < until; t += contract.step_ms) heard = b.hear(t, level, contract.floor) || heard;
				expect(heard, `${from}-${until} at ${level}`).toBe(cut);
			}
		});
	}
});
