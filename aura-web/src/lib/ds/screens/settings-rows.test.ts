import { describe, expect, it } from 'vitest';
import { askFor, matchRows, SETTING_ROWS, SETTING_SECTIONS, settingRow } from './settings-rows';

describe('the settings rows', () => {
	it('gives every row a section, a one-line hint and a short more', () => {
		const ids = SETTING_ROWS.map((r) => r.id);
		expect(new Set(ids).size).toBe(ids.length);
		for (const r of SETTING_ROWS) {
			expect(SETTING_SECTIONS, r.id).toContain(r.section);
			// The screen is a narrow column: 46 characters sit on one line beside More and Ask
			// (seen wrapping to three at 70 in a screenshot); more is two lines.
			expect(r.hint.length, r.id).toBeLessThanOrEqual(46);
			expect(r.more.length, r.id).toBeLessThanOrEqual(100);
			expect(r.hint, r.id).not.toMatch(/\n/);
		}
		for (const s of SETTING_SECTIONS) expect(SETTING_ROWS.some((r) => r.section === s), s).toBe(true);
	});

	it('finds a row by its name, its hint, its section or another word for it', () => {
		expect(matchRows('').size).toBe(SETTING_ROWS.length);
		expect([...matchRows('lock')]).toContain('lock-after');
		expect([...matchRows('WAKE word')]).toEqual(['wake']);
		expect([...matchRows('colour')]).toEqual(['theme']);
		expect([...matchRows('persona')].sort()).toEqual(['persona', 'replies', 'voice', 'wake']);
		expect(matchRows('no such setting').size).toBe(0);
		// Every word must match, in any order.
		expect([...matchRows('unlock voice')]).toEqual(['voice-unlock']);
	});

	it('asks for a change in words, and refuses a row that does not exist', () => {
		expect(askFor(settingRow('lock-after'))).toBe('Help me change the “Lock after” setting.');
		expect(() => settingRow('nope')).toThrow(/no settings row/);
	});
});
