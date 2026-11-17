import { describe, expect, it } from 'vitest';
import {
	SettingsError,
	asStringList,
	parseSettingsOutput,
	toolsetOn,
	withToolset,
	type SettingsGetResult,
	type SettingsListItem
} from './settings';

describe('parseSettingsOutput', () => {
	it('reads a get result', () => {
		const r = parseSettingsOutput<SettingsGetResult>('{"key":"goals.max_turns","value":20,"source":"config"}');
		expect(r).toEqual({ key: 'goals.max_turns', value: 20, source: 'config' });
	});

	it('reads a list result', () => {
		const r = parseSettingsOutput<SettingsListItem[]>('[{"key":"a","value":1},{"key":"b","value":2}]');
		expect(r).toHaveLength(2);
		expect(r[0].key).toBe('a');
	});

	it('throws the plugin error, not a made-up one', () => {
		expect(() => parseSettingsOutput('{"error":"unknown or disallowed setting: nope"}')).toThrowError(SettingsError);
		try {
			parseSettingsOutput('{"error":"must be at least 1"}');
		} catch (e) {
			expect((e as Error).message).toBe('must be at least 1');
		}
	});

	it('treats a non-JSON line as its own message', () => {
		expect(() => parseSettingsOutput('jsettings: list | get <key> | set <key> <json>')).toThrowError(
			'jsettings: list | get <key> | set <key> <json>'
		);
	});

	it('treats empty output as an error rather than undefined', () => {
		expect(() => parseSettingsOutput('')).toThrowError(SettingsError);
		expect(() => parseSettingsOutput(undefined)).toThrowError(SettingsError);
	});
});

describe('toolset lists', () => {
	it('reads a config list, and shrugs off a non-list', () => {
		expect(asStringList(['web', 'browser'])).toEqual(['web', 'browser']);
		expect(asStringList(null)).toEqual([]);
		expect(asStringList('web')).toEqual([]);
	});

	it('resolves on/off: explicit wins, else the roster default', () => {
		const lists = { enabled: ['web'], disabled: ['video'] };
		expect(toolsetOn('web', lists, false)).toBe(true); // explicit enabled beats a false default
		expect(toolsetOn('video', lists, true)).toBe(false); // explicit disabled beats a true default
		expect(toolsetOn('terminal', lists, true)).toBe(true); // neither list: the roster's word
		expect(toolsetOn('terminal', lists, false)).toBe(false);
	});

	it('flipping a name lands it in one list only', () => {
		const off = withToolset('video', true, { enabled: [], disabled: ['video'] });
		expect(off.enabled).toContain('video');
		expect(off.disabled).not.toContain('video');

		const on = withToolset('web', false, { enabled: ['web'], disabled: [] });
		expect(on.disabled).toContain('web');
		expect(on.enabled).not.toContain('web');
	});

	it('does not duplicate a name already in the target list', () => {
		const r = withToolset('web', true, { enabled: ['web'], disabled: [] });
		expect(r.enabled.filter((n) => n === 'web')).toHaveLength(1);
	});
});
