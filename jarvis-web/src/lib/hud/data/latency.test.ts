import { describe, expect, it } from 'vitest';
import { LATENCY_TURNS, latencyRows, median, recordTurn, turnLatency } from './latency';

const turn = { 'ptt-start': 0, 'ptt-end': 1000, 'stt-end': 1400, submit: 1410, 'first-token': 2100, 'first-audio': 2600 };

describe('voice turn latency', () => {
	it('splits a turn into release → transcript → first token → first audio', () => {
		expect(turnLatency(turn)).toEqual({ at: 1000, transcript: 400, firstToken: 700, firstAudio: 500 });
	});

	it('leaves out a typed turn, which has no release', () => {
		expect(turnLatency({ submit: 5, 'first-token': 50 })).toBeNull();
		expect(recordTurn([], { submit: 5, 'first-token': 50 })).toEqual([]);
	});

	it('marks a stage that has not happened yet as null, and fills it in place', () => {
		let history = recordTurn([], { 'ptt-end': 1000, 'stt-end': 1400 });
		expect(history).toEqual([{ at: 1000, transcript: 400, firstToken: null, firstAudio: null }]);
		history = recordTurn(history, turn);
		expect(history).toHaveLength(1);
		expect(history[0].firstAudio).toBe(500);
	});

	it(`keeps only the last ${LATENCY_TURNS} turns`, () => {
		let history = recordTurn([], turn);
		for (let i = 1; i <= LATENCY_TURNS + 5; i++) history = recordTurn(history, { ...turn, 'ptt-end': 1000 + i, 'stt-end': 1400 + i * 2 });
		expect(history).toHaveLength(LATENCY_TURNS);
		expect(history[history.length - 1].at).toBe(1000 + LATENCY_TURNS + 5);
	});

	it('takes the median of what was measured', () => {
		expect(median([])).toBeNull();
		expect(median([300, 100, 200])).toBe(200);
		expect(median([100, 400])).toBe(250);
	});

	it('shows the last and the median per stage', () => {
		const history = [
			{ at: 1, transcript: 300, firstToken: 900, firstAudio: 400 },
			{ at: 2, transcript: 500, firstToken: 700, firstAudio: null },
			{ at: 3, transcript: 400, firstToken: 800, firstAudio: 600 }
		];
		expect(latencyRows(history)).toEqual([
			{ key: 'transcript', label: 'Release → transcript', last: 400, median: 400 },
			{ key: 'firstToken', label: 'Transcript → first token', last: 800, median: 800 },
			{ key: 'firstAudio', label: 'First token → first audio', last: 600, median: 500 }
		]);
		expect(latencyRows([]).every((r) => r.last === null && r.median === null)).toBe(true);
	});
});
