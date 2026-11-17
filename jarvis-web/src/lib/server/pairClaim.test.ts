import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { CLAIM_KINDS, NO_ADAPTER_KEY, UNNAMED_KIND, withAdapter } from './pairClaim';

// The claim's request and what the console adds to the answer are one table the console,
// the adapter, the phone and the desktop read (`tests/contracts/pairing-claim.json`, T-0569).
const root = new URL('../../../../', import.meta.url);
const contract = JSON.parse(readFileSync(new URL('tests/contracts/pairing-claim.json', root), 'utf8')) as {
	kinds: string[];
	unnamed_kind: string;
	no_adapter_key: string[];
	adapter_answer: string[];
};

const ENV = { AURA_ADAPTER_URL: 'http://jarvis:8643', AURA_ADAPTER_KEY: 'master-key' };
const HOUSE = JSON.stringify({ url: 'http://192.0.2.10:8080', token: 't', token_id: 'tok_9', name: 'Pixel' });
const ASKED = JSON.stringify({ code: '12345678', name: 'Pixel', kind: 'phone' });
const DEVICE = { id: 'd1', name: 'Pixel', kind: 'phone', created: 1, last_seen: null, house_token: 'tok_9' };

function adapter(answer: unknown, status = 200) {
	const seen: { url: string; method: string; auth: string | null; body: unknown }[] = [];
	const fetcher = (async (url: string, init?: RequestInit) => {
		seen.push({
			url,
			method: init?.method ?? 'GET',
			auth: new Headers(init?.headers).get('authorization'),
			body: init?.body ? JSON.parse(String(init.body)) : null
		});
		return new Response(JSON.stringify(answer), { status });
	}) as unknown as typeof fetch;
	return { fetcher, seen };
}
const minted = { url: 'http://192.0.2.10:8643', key: 'device-key', model: 'm', kind: 'hermes-adapter', device: DEVICE };

describe('a redeemed pairing code also carries an adapter key of its own (T-0569)', () => {
	it('reads the same kinds as the contract', () => {
		expect([...CLAIM_KINDS]).toEqual(contract.kinds);
		expect(UNNAMED_KIND).toBe(contract.unnamed_kind);
		expect([...NO_ADAPTER_KEY]).toEqual(contract.no_adapter_key);
	});

	it('mints the device its own key, named and kinded by the claim, with the house token kept beside it', async () => {
		const { fetcher, seen } = adapter(minted);
		const out = await withAdapter(HOUSE, ASKED, ENV, fetcher);
		expect(out).toEqual({
			url: 'http://192.0.2.10:8080',
			token: 't',
			token_id: 'tok_9',
			name: 'Pixel',
			adapter: { url: 'http://192.0.2.10:8643', key: 'device-key', model: 'm', device: DEVICE }
		});
		expect(Object.keys((out as { adapter: object }).adapter)).toEqual(contract.adapter_answer);
		expect(seen).toEqual([
			{
				url: 'http://jarvis:8643/api/pair',
				method: 'POST',
				auth: 'Bearer master-key',
				body: { name: 'Pixel', kind: 'phone', house_token: 'tok_9' }
			}
		]);
		expect(JSON.stringify(out)).not.toContain('master-key');
	});

	it('calls a claim with no kind, or an unknown one, the unnamed kind, and still mints', async () => {
		for (const asked of [JSON.stringify({ code: '1', name: 'Old phone' }), JSON.stringify({ code: '1', kind: 'toaster' }), 'not json']) {
			const { fetcher, seen } = adapter(minted);
			expect(await withAdapter(HOUSE, asked, ENV, fetcher)).not.toBeNull();
			expect((seen[0].body as { kind: string }).kind).toBe(contract.unnamed_kind);
		}
	});

	it('mints nothing for a desktop, which never talks to the adapter', async () => {
		const { fetcher, seen } = adapter(minted);
		expect(await withAdapter(HOUSE, JSON.stringify({ code: '1', name: 'Study', kind: 'desktop' }), ENV, fetcher)).toBeNull();
		expect(seen).toEqual([]);
	});

	it('leaves the house answer alone when the adapter is unset, down, refuses, or answers with the master key', async () => {
		expect(await withAdapter(HOUSE, ASKED, {}, adapter(minted).fetcher)).toBeNull();
		expect(await withAdapter(HOUSE, ASKED, ENV, adapter({}, 502).fetcher)).toBeNull();
		expect(await withAdapter(HOUSE, ASKED, ENV, adapter({ url: 'x', key: 'k' }).fetcher)).toBeNull();
		expect(await withAdapter(HOUSE, ASKED, ENV, adapter({ ...minted, key: 'master-key' }).fetcher)).toBeNull();
		expect(await withAdapter('not json', ASKED, ENV, adapter(minted).fetcher)).toBeNull();
	});
});
