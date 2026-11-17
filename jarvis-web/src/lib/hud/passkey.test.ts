import { describe, expect, it } from 'vitest';

import { passkeyRefusal, passkeyState } from './passkey';

describe('whether the screen offers a passkey', () => {
	it('needs both the console and the browser', () => {
		expect(passkeyState({ available: true, registered: true }, true)).toMatchObject({ usable: true, registered: true, why: null });
		expect(passkeyState({ available: true, registered: true }, false).usable).toBe(false);
		expect(passkeyState({ available: false, problem: 'set the address' }, true)).toMatchObject({ usable: false, why: 'set the address' });
	});

	it('always has a sentence for why not, and no list from a bad answer', () => {
		expect(passkeyState(null, true).why).toBeTruthy();
		expect(passkeyState({ available: true }, false).why).toContain('https');
		expect(passkeyState({ available: true, passkeys: 'x' as never }, true).passkeys).toEqual([]);
	});
});

describe('a refusal in words', () => {
	it("is the console's own sentence, else one for the status", () => {
		expect(passkeyRefusal(401, 'that passkey did not open the console', 'x')).toContain('did not open');
		expect(passkeyRefusal(429, undefined, 'x')).toContain('Too many');
		expect(passkeyRefusal(500, '', 'fallback')).toBe('fallback');
	});
});
