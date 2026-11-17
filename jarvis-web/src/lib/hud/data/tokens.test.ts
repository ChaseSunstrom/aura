import { describe, expect, it } from 'vitest';
import { tokensView } from './tokens';

// T-1386: the Devices section's rows, from the house's `config/token/list`. Made-up devices.

const NOW = 1_800_000_000_000;
const ago = (seconds: number) => NOW / 1000 - seconds;

const phone = { id: 'a1b2c3d4e5f6', name: 'Pixel (example)', created_at: 1_700_000_000, last_used_at: ago(180), paired: true, approver: true, connected: false };

describe('tokensView', () => {
	it('shows a paired device with its name, when it was last used and its approver setting', () => {
		expect(tokensView([phone], NOW)).toEqual([
			{ id: 'a1b2c3d4e5f6', name: 'Pixel (example)', state: 'paired', word: 'Paired', detail: 'Last used 3 minutes ago', note: '', approver: true, revocable: true, current: false }
		]);
		expect(tokensView([{ ...phone, approver: false }], NOW)?.[0].approver).toBe(false);
	});

	it('reads a paired token with no approver field as an approver: that is what the house does', () => {
		const { approver: _gone, ...older } = phone;
		expect(tokensView([older], NOW)?.[0].approver).toBe(true);
		// Only a real `false` switches it off; a string is not guessed at.
		expect(tokensView([{ ...phone, approver: 'false' }], NOW)?.[0].approver).toBe(true);
	});

	it('labels a token stored before pairing was recorded as an older token to pair again', () => {
		const row = tokensView([{ id: 'feedfacecafe', name: 'Tablet (example)', last_used_at: null, connected: false }], NOW)?.[0];
		expect(row).toMatchObject({ state: 'older', word: 'Older token', detail: 'Never used', revocable: true });
		expect(row?.note).toMatch(/pair that device again/i);
	});

	it('gives a token that is not paired no approver setting, whatever the row says', () => {
		// The house reads the flag only for a paired token: a switch here would change nothing.
		expect(tokensView([{ id: 'feedfacecafe', name: 'Tablet (example)', approver: true }], NOW)?.[0].approver).toBeNull();
		expect(tokensView([{ id: 'feedfacecafe', name: 'Tablet (example)', paired: 'yes', approver: false }], NOW)?.[0].approver).toBeNull();
	});

	it('offers no revoke on the row the house marks as the caller\'s own, and labels it', () => {
		const row = tokensView([{ id: 'c0ffee000001', name: 'console', connected: true, current: true, last_used_at: ago(5) }], NOW)?.[0];
		expect(row).toMatchObject({ state: 'older', word: 'This console', revocable: false, current: true, detail: 'Last used just now · connected now' });
		expect(row?.note).toMatch(/signs? you out|sign you out/i);
		// A paired token can be the caller's own too: the house's word decides, not the paired flag.
		expect(tokensView([{ ...phone, current: true }], NOW)?.[0]).toMatchObject({ state: 'paired', word: 'This console', revocable: false });
	});

	it('offers revoke on every other row, including a connected key that is not paired', () => {
		const rows = tokensView(
			[
				{ id: 'c0ffee000001', name: 'console', connected: true, current: true },
				{ id: 'c0ffee000002', name: 'Kitchen display (example)', connected: true, current: false, paired: false },
				{ id: 'c0ffee000003', name: 'Older tablet (example)', connected: false, current: false },
				{ ...phone, current: false, connected: true }
			],
			NOW
		);
		expect(rows?.map((r) => [r.id, r.revocable, r.current])).toEqual([
			['c0ffee000001', false, true],
			['c0ffee000002', true, false],
			['c0ffee000003', true, false],
			['a1b2c3d4e5f6', true, false]
		]);
		// The connected, unpaired row is no longer treated as the caller's: it reads as an ordinary older token.
		expect(rows?.[1]).toMatchObject({ word: 'Older token', detail: expect.stringContaining('connected now') });
		expect(rows?.[1].note).toMatch(/pair that device again/i);
	});

	it('reads a flag that is not a real true as not the caller\'s own once the house sends the field', () => {
		for (const current of [false, 'true', 1, null]) {
			expect(tokensView([{ id: 'x', name: 'k', connected: true, current }], NOW)?.[0]).toMatchObject({ current: false, revocable: true });
		}
	});

	it('falls back to the old guess only for a house that sends no `current` field at all', () => {
		// An older house cannot say which key is the caller's; keeping revoke off a connected unpaired
		// key is then the safe side. A paired row stays revocable.
		expect(tokensView([{ id: 'x', name: 'k', connected: true }], NOW)?.[0]).toMatchObject({ current: true, revocable: false });
		expect(tokensView([{ ...phone, connected: true }], NOW)?.[0]).toMatchObject({ current: false, revocable: true });
	});

	it('never copies a hash, a token or any field it does not show', () => {
		const view = tokensView([{ ...phone, token_hash: 'HASH-do-not-show', token: 'SECRET-do-not-show', secret: 'ALSO-do-not-show', extra: { token: 'NESTED-do-not-show' } }], NOW);
		expect(JSON.stringify(view)).not.toMatch(/do-not-show|token_hash|secret/);
		expect(Object.keys(view?.[0] ?? {}).sort()).toEqual(['approver', 'current', 'detail', 'id', 'name', 'note', 'revocable', 'state', 'word']);
	});

	it('is null for an answer that is not a list, so the section says error and not "no devices"', () => {
		expect(tokensView(null, NOW)).toBeNull();
		expect(tokensView({ tokens: [phone] }, NOW)).toBeNull();
		expect(tokensView([], NOW)).toEqual([]);
	});

	it('survives rows it cannot trust: no id, a repeated id, a long name, a time that is not a number', () => {
		const view = tokensView([null, 'x', { name: 'no id' }, { id: '' }, phone, { ...phone, name: 'twin' }, { id: 'b', name: 'n'.repeat(200), paired: true, last_used_at: 'soon' }, { id: 'c', paired: true, last_used_at: Infinity }], NOW);
		expect(view?.map((r) => r.id)).toEqual(['a1b2c3d4e5f6', 'b', 'c']);
		expect(view?.[1].name).toHaveLength(60);
		expect(view?.[1].detail).toBe('Never used');
		expect(view?.[2]).toMatchObject({ name: 'Unnamed token', detail: 'Never used' });
	});
});
