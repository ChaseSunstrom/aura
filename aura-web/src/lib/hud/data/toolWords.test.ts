import { describe, expect, it } from 'vitest';

import { stepOf } from './toolWords';

describe('stepOf', () => {
	it('says what a call does in plain words, with what it is about', () => {
		expect(stepOf('web_search', { query: 'weather tomorrow' }, '', 'running')).toBe('Searching the web · weather tomorrow');
		expect(stepOf('web_search', { query: 'weather tomorrow' }, '', 'ok')).toBe('Searched the web · weather tomorrow');
		expect(stepOf('ha_list_entities', { domain: 'sensor' }, '', 'ok')).toBe("Checked the house's devices");
		expect(stepOf('ha_get_state', { entity_id: 'sensor.outside_temperature' }, '', 'ok')).toBe('Checked sensor.outside_temperature');
		expect(stepOf('weather_forecast', { place: 'Oslo', days: 7 }, '', 'ok')).toBe('Checked the weather · Oslo');
		expect(stepOf('weather_forecast', {}, '', 'running')).toBe('Checking the weather');
	});

	it('a page it reads is named by its site, never by the raw arguments', () => {
		const args = { urls: ['https://www.bbc.co.uk/weather/2643743', 'https://weather.com/x'], char_limit: 4000 };
		const step = stepOf('web_extract', args, '', 'ok');
		expect(step).toBe('Read bbc.co.uk, weather.com');
		expect(step).not.toMatch(/char_limit|\[|"/);
		expect(stepOf('web_extract', {}, '', 'running')).toBe('Reading a page');
	});

	it("falls back to Hermes's own context, then to the tool's name in sentence case", () => {
		expect(stepOf('web_search', {}, 'restaurants near me', 'running')).toBe('Searching the web · restaurants near me');
		expect(stepOf('gpu_status', {}, '', 'ok')).toBe('Gpu status');
	});

	it('a failed call says what it was trying and why it stopped', () => {
		expect(stepOf('web_search', { query: 'weather' }, '', 'failed', 'timed out')).toBe('Searching the web · weather — timed out');
	});

	it('a call that only presents (hud_*) is not a step', () => {
		expect(stepOf('hud_say', { text: 'hi' }, '', 'ok')).toBe('');
	});

	it('a long subject is cut, not wrapped across the stage', () => {
		expect(stepOf('web_search', { query: 'x'.repeat(200) }, '', 'ok').length).toBeLessThanOrEqual(80);
	});
});
