import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Feeds } from './feeds.svelte';

// T-1343: the integrations feed carries the sign-in accounts, read from the house's own
// `oauth.status` through `call_service`; a house that does not answer that costs the Accounts
// section, not the screen.

const providers = {
	google: { label: 'Google', configured: true, status: 'connected', last_test: null },
	spotify: { label: 'Spotify', configured: false, status: 'not_configured', last_test: null }
};

function houseWith(status: (msg: Record<string, unknown>) => Promise<unknown>) {
	const client = {
		listToolRows: async () => [{ name: 'lock', description: 'Lock a door', tier: 3 }],
		listSkills: async () => ({ skills: [] }),
		listMcpServers: async () => ({ servers: [] }),
		subscribeEvents: async () => ({ unsubscribe: async () => {} }),
		callService: (domain: string, service: string, data: Record<string, unknown>, extra: Record<string, unknown>) => status({ domain, service, data, ...extra }),
		command: () => Promise.resolve({ extensions: [], entries: [], errors: [] })
	};
	return { conn: { client }, hermes: null } as never;
}

beforeEach(() => vi.stubGlobal('fetch', async () => new Response('[]', { status: 200 })));
afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

describe('integrations feed: accounts', () => {
	it('reads oauth.status for its response and carries one row per provider', async () => {
		const feeds = new Feeds();
		await feeds.integrations.load(houseWith(async () => ({ response: { providers } })));
		expect(feeds.integrations.data.accounts?.map((a) => [a.id, a.state])).toEqual([
			['google', 'connected'],
			['spotify', 'not-set-up']
		]);
	});

	it('a house that errors on it leaves the screen ready and the accounts null', async () => {
		const feeds = new Feeds();
		await feeds.integrations.load(houseWith(async () => Promise.reject(new Error('unknown service'))));
		expect(feeds.integrations.status).toBe('ready');
		expect(feeds.integrations.data.accounts).toBeNull();
		expect(feeds.integrations.data.tools.map((t) => t.name)).toEqual(['lock']);
	});

	it('a house that never answers it is null after the wait, with the rest of the screen loaded', async () => {
		vi.useFakeTimers();
		const feeds = new Feeds();
		const loading = feeds.integrations.load(houseWith(() => new Promise(() => {})));
		await vi.advanceTimersByTimeAsync(5000);
		await loading;
		expect(feeds.integrations.status).toBe('ready');
		expect(feeds.integrations.data.accounts).toBeNull();
	});
});
