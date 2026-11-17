// A `chart` or `table` content panel as telemetry rows (T-0448).
//
// "Chart my power this week" drew one number: chartReadings kept only each
// series' last point. A three-column table lost its middle column: tableReadings
// kept the first as the label and the last as the figure.
import { describe, expect, it } from 'vitest';
import { chartReadings, tableReadings } from './telemetry';

describe('a chart panel', () => {
	it('keeps every point of a series, the last as the figure', () => {
		const [row] = chartReadings({ series: [{ name: 'Power', points: [[0, 410], [60, 'x'], [120, 980.04], [180, 655]] }], unit: 'W' });
		expect(row).toMatchObject({ label: 'Power', value: 655, unit: 'W', points: [410, 980, 655] });
	});

	it('takes bare numbers as points too', () => {
		expect(chartReadings({ series: [{ name: 'load', points: [0.4, 0.9, 1.2] }] })[0].points).toEqual([0.4, 0.9, 1.2]);
	});

	it('samples a long series across its whole span, not just the newest points', () => {
		const points = Array.from({ length: 2016 }, (_, i) => [i, i]);
		const [row] = chartReadings({ series: [{ name: 'n', points }] });
		expect(row.points).toHaveLength(120);
		expect(row.points?.[0]).toBe(0);
		expect(row.points?.at(-1)).toBe(2015);
		expect(row.value).toBe(2015);
	});
});

describe('a table panel', () => {
	it('keeps the middle columns on the row\'s second line', () => {
		const rows = tableReadings({ columns: ['GPU', 'VRAM', 'Temp'], rows: [['0', '93.6 %', '50 °C']] });
		expect(rows[0]).toMatchObject({ label: 'GPU 0', value: 50, unit: '°C', sub: 'VRAM 93.6 %' });
	});

	it('leaves an empty middle cell out rather than printing its header alone', () => {
		const rows = tableReadings({ columns: ['GPU', 'VRAM', 'Fan', 'Temp'], rows: [['0', ' ', '40 %', '50 °C']] });
		expect(rows[0].sub).toBe('Fan 40 %');
	});

	it('reads a grouped figure and shows a word or a price as itself, with no dash', () => {
		const rows = tableReadings({ columns: ['Meter', 'Now'], rows: [['A', '1,234 W'], ['B', 'Online'], ['C', '$5'], ['D', '1,5 kW']] });
		expect(rows[0]).toMatchObject({ value: 1234, unit: 'W' });
		expect(rows[1]).toMatchObject({ value: null, text: 'Online', unit: undefined });
		expect(rows[2]).toMatchObject({ value: null, text: '$5', unit: undefined });
		expect(rows[3]).toMatchObject({ value: null, text: '1,5 kW' });
	});

	it('has no second line for two columns', () => {
		expect(tableReadings({ columns: ['GPU', 'Temp'], rows: [['0', '50 °C']] })[0].sub).toBeUndefined();
	});
});
