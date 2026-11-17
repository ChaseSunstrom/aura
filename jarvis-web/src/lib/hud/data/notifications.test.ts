import { describe, expect, it } from 'vitest';
import { dndOf } from './notifications';

// T-0565: the dock's do not disturb was the console's alone; the house's switch is the one
// the narrator, schedules and finished work honour.
describe('dndOf', () => {
	it('reads the house-wide switch from a presence or set_dnd answer', () => {
		expect(dndOf({ context: {}, response: { dnd: true, until: null, devices: [] } })).toBe(true);
		expect(dndOf({ response: { ok: true, dnd: false, until: null } })).toBe(false);
	});
	it('says nothing when an older house does not answer it', () => {
		expect(dndOf({ response: { devices: [] } })).toBeNull();
		expect(dndOf(null)).toBeNull();
		expect(dndOf({})).toBeNull();
	});
});
