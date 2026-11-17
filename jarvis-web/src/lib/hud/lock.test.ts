import { describe, expect, it } from 'vitest';

import { lockEndsSession, lockHint, parseLockAfter, shouldLock, voiceVerdict } from './lock';

describe('the lock', () => {
	it('locks after the allowance of quiet, and never when the allowance is off', () => {
		const t0 = 1_000_000;
		expect(shouldLock({ activeAt: t0, afterMin: 5 }, t0 + 4 * 60_000)).toBe(false);
		expect(shouldLock({ activeAt: t0, afterMin: 5 }, t0 + 5 * 60_000)).toBe(true);
		expect(shouldLock({ activeAt: t0, afterMin: 0 }, t0 + 60 * 60_000)).toBe(false);
	});

	it('reads a stored choice and refuses anything else', () => {
		expect(parseLockAfter('15')).toBe(15);
		// Any whole number of minutes up to twelve hours, not only the presets.
		expect(parseLockAfter('7')).toBe(7);
		expect(parseLockAfter(720)).toBe(720);
		expect(parseLockAfter('721')).toBe(0);
		expect(parseLockAfter('2.5')).toBe(0);
		expect(parseLockAfter('-3')).toBe(0);
		expect(parseLockAfter(null)).toBe(0);
		expect(parseLockAfter('nonsense')).toBe(0);
	});

	it('says what opens it, and says so when nothing can', () => {
		expect(lockHint(false, true)).toMatch(/No console password/);
		expect(lockHint(true, false)).toBe('Type the console password.');
		expect(lockHint(true, true)).toMatch(/your voice/);
		expect(lockHint(true, false, true)).toBe('Use your passkey, or type the console password.');
		expect(lockHint(true, true, true)).toMatch(/passkey.*password.*your voice/);
	});

	it('reads the verifier: accepted opens, a stranger is refused, a short sample is a retry', () => {
		expect(voiceVerdict({ verdict: { accepted: true, label: 'chase' } })).toBe('open');
		expect(voiceVerdict({ verdict: { accepted: true, label: 'chase', level: 'member' } })).toBe('open');
		// Recognised and still kept out: a guest's voice opens nothing guarded.
		expect(voiceVerdict({ verdict: { accepted: true, label: 'Ted', level: 'guest' } })).toBe('guest');
		expect(voiceVerdict({ verdict: { accepted: false, reason: 'below-threshold' } })).toBe('refused');
		expect(voiceVerdict({ verdict: { accepted: false, reason: 'no-speech' } })).toBe('unclear');
		expect(voiceVerdict({ verdict: { accepted: false, reason: 'unverifiable-no-pitch' } })).toBe('unclear');
		expect(voiceVerdict(null)).toBe('unclear');
	});
});

describe('what locking does to the session', () => {
	it('ends it, unless a voice has to be able to open the lock', () => {
		expect(lockEndsSession(false)).toBe(true);
		expect(lockEndsSession(true)).toBe(false);
		expect(lockEndsSession(false, true)).toBe(false);
	});
});
