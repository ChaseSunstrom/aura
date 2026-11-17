import { describe, expect, it } from 'vitest';
import contract from '../../../../../tests/contracts/first-run-steps.json';
import { STEPS, TEXT, close, current, fresh, mark, parse, serialize, shown, status, type House } from './model';

const NEW: House = { firstRun: true, people: 0, devices: 0 };
const SET_UP: House = { firstRun: false, people: 1, devices: 1 };

describe('the first-run walkthrough model', () => {
	it('orders the six steps', () => {
		// The shared table the first-run skill's sections are checked against too.
		expect([...STEPS]).toEqual(contract.steps);
		expect(current(null, NEW)).toBe('account');
	});

	it('each step asks for its own section of the first-run skill', () => {
		for (const s of STEPS) expect(TEXT[s].ask).toContain(`step "${s}"`);
	});

	it('a step the house decides is done only when the house says so', () => {
		const said = mark(mark(mark(null, 'account', 'done'), 'people', 'done'), 'devices', 'done');
		for (const s of ['account', 'people', 'devices'] as const) expect(status(s, said, NEW)).toBe('open');
		expect(status('account', null, { ...NEW, firstRun: false })).toBe('done');
		expect(status('people', null, { ...NEW, people: 1 })).toBe('done');
		expect(status('devices', null, { ...NEW, devices: 2 })).toBe('done');
	});

	it('a step with valid defaults is done when the owner says so', () => {
		expect(status('persona', null, SET_UP)).toBe('open');
		expect(status('persona', mark(null, 'persona', 'done'), SET_UP)).toBe('done');
	});

	it('skips a step and moves on, and a skipped step the house later shows done is done', () => {
		const house = { ...NEW, firstRun: false };
		const p = mark(null, 'people', 'skipped');
		expect(status('people', p, house)).toBe('skipped');
		expect(current(p, house)).toBe('persona');
		expect(status('people', p, { ...house, people: 1 })).toBe('done');
	});

	it('resumes at the same step from the stored row', () => {
		const house = { ...NEW, firstRun: false, people: 1 };
		const p = mark(mark(null, 'persona', 'done'), 'privacy', 'skipped');
		expect(current(parse(serialize(p)), house)).toBe('devices');
	});

	it('ends when nothing is open', () => {
		let p = fresh();
		for (const s of ['persona', 'privacy', 'integrations'] as const) p = mark(p, s, 'done');
		expect(current(p, SET_UP)).toBeNull();
		expect(shown(p, SET_UP)).toBe(false);
	});

	it('an owner already set up with no progress row is not shown it, unless Settings asks', () => {
		expect(shown(null, SET_UP)).toBe(false);
		expect(shown(null, { firstRun: false, people: 0, devices: 0 })).toBe(false);
		expect(shown(null, SET_UP, true)).toBe(true);
	});

	it('opens on first run, stays open while a row has an open step, and stays shut once closed', () => {
		expect(shown(null, NEW)).toBe(true);
		const p = mark(null, 'persona', 'done');
		expect(shown(p, SET_UP)).toBe(true);
		expect(shown(close(p), SET_UP)).toBe(false);
		expect(shown(close(null), NEW)).toBe(false);
		expect(shown(close(p), SET_UP, true)).toBe(true);
	});

	it('reads a missing, malformed or foreign row as none, and drops marks it does not know', () => {
		for (const raw of [null, undefined, '', '{', '[]', '{"schema":2,"marks":{}}', '{"schema":1,"marks":null}'])
			expect(parse(raw)).toBeNull();
		expect(parse('{"schema":1,"marks":{"persona":"done","account":"yes","other":"done"}}')).toEqual({
			schema: 1,
			marks: { persona: 'done' },
			closed: false
		});
	});
});
