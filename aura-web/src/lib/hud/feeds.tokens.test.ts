import { describe, expect, it } from 'vitest';
import { Feeds } from './feeds.svelte';

// T-1386: the Devices section reads its own feed, so a token list that fails is an error on that
// section (never "no devices" over live credentials), and its two actions go to the house and
// then read the list again. Made-up devices.

const phone = { id: 'a1b2c3d4e5f6', name: 'Pixel (example)', paired: true, approver: true, connected: false, last_used_at: null };

function house(over: Record<string, unknown> = {}) {
	const calls: unknown[][] = [];
	let rows: unknown = [phone];
	const client = {
		subscribeEvents: async () => ({ unsubscribe: async () => {} }),
		listTokens: async () => rows,
		updateToken: async (id: string, approver: boolean) => {
			calls.push(['update', id, approver]);
			rows = [{ ...phone, approver }];
			return { id, approver };
		},
		revokeToken: async (id: string) => {
			calls.push(['revoke', id]);
			rows = [];
			return { id, revoked: true, sockets_closed: 0 };
		},
		...over
	};
	return { calls, sources: { conn: { client }, hermes: null } as never, answer: (next: unknown) => (rows = next) };
}

const DOWN = { conn: null, hermes: null };

describe('tokens feed: the four states', () => {
	it('is loading until the house answers, then ready with its rows', async () => {
		const feeds = new Feeds();
		expect(feeds.tokens.status).toBe('loading');
		await feeds.tokens.load(house().sources);
		expect(feeds.tokens.status).toBe('ready');
		expect(feeds.tokens.data.map((r) => [r.name, r.word, r.approver])).toEqual([['Pixel (example)', 'Paired', true]]);
	});

	it('is empty when the house holds no stored token', async () => {
		const feeds = new Feeds();
		const h = house();
		h.answer([]);
		await feeds.tokens.load(h.sources);
		expect(feeds.tokens.status).toBe('empty');
	});

	it('is an error when the house refuses, and when its answer is not a list', async () => {
		const feeds = new Feeds();
		await feeds.tokens.load(house({ listTokens: async () => Promise.reject(new Error('unauthorized')) }).sources);
		expect([feeds.tokens.status, feeds.tokens.error]).toEqual(['error', 'unauthorized']);

		const h = house();
		h.answer({ tokens: [phone] });
		await feeds.tokens.load(h.sources);
		expect(feeds.tokens.status).toBe('error');
		expect(feeds.tokens.data).toEqual([]);
	});

	it('is offline once the house link drops, and stays loading before the first answer', async () => {
		const feeds = new Feeds();
		await feeds.tokens.load(DOWN);
		expect(feeds.tokens.status).toBe('loading');
		await feeds.tokens.load(house().sources);
		await feeds.tokens.load(DOWN);
		expect(feeds.tokens.status).toBe('offline');
	});

	it('loads with the Integrations screen', () => {
		const feeds = new Feeds();
		expect(feeds.feedsFor('integrations')).toContain(feeds.tokens);
	});
});

describe('tokens feed: the switch and the revoke', () => {
	it('the approver switch sends the token update and shows what the house then holds', async () => {
		const feeds = new Feeds();
		const h = house();
		feeds.setSources(h.sources);
		await feeds.setTokenApprover('a1b2c3d4e5f6', false);
		expect(h.calls).toEqual([['update', 'a1b2c3d4e5f6', false]]);
		expect(feeds.tokens.data[0].approver).toBe(false);
	});

	it('revoke sends the token revoke and the row is gone', async () => {
		const feeds = new Feeds();
		const h = house();
		feeds.setSources(h.sources);
		await feeds.tokens.load(h.sources);
		await feeds.revokeToken('a1b2c3d4e5f6');
		expect(h.calls).toEqual([['revoke', 'a1b2c3d4e5f6']]);
		expect([feeds.tokens.status, feeds.tokens.data]).toEqual(['empty', []]);
	});

	it('a refused change rejects, and the list is read again so the switch shows the house, not the ask', async () => {
		const feeds = new Feeds();
		const h = house({ updateToken: async () => Promise.reject(new Error('unknown token a1b2c3d4e5f6')) });
		feeds.setSources(h.sources);
		await feeds.tokens.load(h.sources);
		h.answer([{ ...phone, name: 'Pixel (renamed example)' }]);
		await expect(feeds.setTokenApprover('a1b2c3d4e5f6', false)).rejects.toThrow('unknown token');
		expect(feeds.tokens.data[0]).toMatchObject({ name: 'Pixel (renamed example)', approver: true });
	});

	it('with no house link nothing is sent and the call rejects', async () => {
		const feeds = new Feeds();
		await expect(feeds.revokeToken('a1b2c3d4e5f6')).rejects.toThrow(/not connected/);
		await expect(feeds.setTokenApprover('a1b2c3d4e5f6', true)).rejects.toThrow(/not connected/);
	});
});
