import { describe, it, expect, beforeEach } from 'vitest';
import { deviceFor, deviceLive, listDeviceKeys, revokeDevice, resetDeviceKeys } from './deviceKeys';

// T-0569: each paired phone holds its own adapter key. The console admits one by asking
// the adapter whose it is, and a revoke must end both the adapter key and the house token.

const ENV = {
	AURA_ADAPTER_URL: 'http://adapter:8643',
	AURA_ADAPTER_KEY: 'master',
	AURA_BACKEND: 'core',
	AURA_URL: 'http://house:8080',
	AURA_TOKEN: 'house-admin' // pragma: allowlist secret
};
const KEYS = [
	{ id: 'd1', name: 'Pixel', kind: 'phone', created: 1, last_seen: null, house_token: 'tok_1' },
	{ id: 'd2', name: 'Tablet', kind: 'phone', created: 2, last_seen: 5, house_token: null }
];

type Call = { url: string; method: string; auth: string | null };
function adapter(opts: { down?: boolean; keys?: typeof KEYS } = {}) {
	const calls: Call[] = [];
	const state = { down: opts.down ?? false, keys: [...(opts.keys ?? KEYS)] };
	const fetcher = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		const url = String(input);
		const auth = new Headers(init.headers).get('authorization');
		const method = init.method ?? 'GET';
		calls.push({ url, method, auth });
		if (state.down) throw new TypeError('fetch failed');
		const json = (body: unknown, status = 200) =>
			new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
		if (url === 'http://adapter:8643/api/pair') {
			if (auth === 'Bearer master') return json({ kind: 'hermes-adapter', url: 'x', key: 'master' });
			if (auth === 'Bearer phone-key') return json({ kind: 'hermes-adapter', url: 'x', device: KEYS[0] });
			return json({ detail: 'Unauthorized' }, 401);
		}
		if (auth !== 'Bearer master' && url.startsWith('http://adapter')) return json({}, 401);
		if (url === 'http://adapter:8643/api/devices/keys') return json({ keys: state.keys });
		const del = /^http:\/\/adapter:8643\/api\/devices\/keys\/([^/]+)$/.exec(url);
		if (del && method === 'DELETE') {
			const before = state.keys.length;
			state.keys = state.keys.filter((k) => k.id !== decodeURIComponent(del[1]));
			return state.keys.length < before ? json({ ok: true, id: del[1] }) : json({}, 404);
		}
		if (url.startsWith('http://house:8080/api/auth/tokens/') && method === 'DELETE') {
			return auth === 'Bearer house-admin' ? json({ ok: true }) : json({}, 401);
		}
		return json({}, 404);
	}) as typeof fetch;
	return { fetcher, calls, state };
}

beforeEach(() => resetDeviceKeys());

describe('which device a bearer is', () => {
	it('asks the adapter once, then answers from memory for thirty seconds', async () => {
		const a = adapter();
		expect(await deviceFor(ENV, 'Bearer phone-key', a.fetcher, 0)).toBe('d1');
		expect(await deviceFor(ENV, 'Bearer phone-key', a.fetcher, 29_999)).toBe('d1');
		expect(a.calls).toHaveLength(1);
		expect(await deviceFor(ENV, 'Bearer phone-key', a.fetcher, 30_000)).toBe('d1');
		expect(a.calls).toHaveLength(2);
	});
	it('is nobody for a wrong key, the master key, or no key, and never asks without a bearer', async () => {
		const a = adapter();
		expect(await deviceFor(ENV, 'Bearer guess', a.fetcher)).toBeNull();
		expect(await deviceFor(ENV, 'Bearer master', a.fetcher)).toBeNull();
		expect(await deviceFor(ENV, null, a.fetcher)).toBeNull();
		expect(await deviceFor(ENV, 'Basic abc', a.fetcher)).toBeNull();
		expect(a.calls).toHaveLength(2);
	});
	it('keeps the last answer while the adapter is unreachable, and is nobody with none', async () => {
		const a = adapter();
		expect(await deviceFor(ENV, 'Bearer phone-key', a.fetcher, 0)).toBe('d1');
		a.state.down = true;
		expect(await deviceFor(ENV, 'Bearer phone-key', a.fetcher, 60_000)).toBe('d1');
		resetDeviceKeys();
		expect(await deviceFor(ENV, 'Bearer phone-key', a.fetcher, 60_000)).toBeNull();
	});
	it('asks nothing when the adapter is not configured', async () => {
		const a = adapter();
		expect(await deviceFor({}, 'Bearer phone-key', a.fetcher)).toBeNull();
		expect(a.calls).toHaveLength(0);
	});
});

describe('whether a device is still live', () => {
	it('reads the list with the master key, cached, and fails closed with no list at all', async () => {
		const a = adapter();
		expect(await deviceLive(ENV, 'd1', a.fetcher, 0)).toBe(true);
		expect(await deviceLive(ENV, 'gone', a.fetcher, 10)).toBe(false);
		expect(a.calls.map((c) => c.auth)).toEqual(['Bearer master']);
		resetDeviceKeys();
		a.state.down = true;
		expect(await deviceLive(ENV, 'd1', a.fetcher, 0)).toBe(false);
	});
});

describe('revoking a device', () => {
	it('revokes the adapter key and the house token, and forgets what it knew', async () => {
		const a = adapter();
		expect(await deviceFor(ENV, 'Bearer phone-key', a.fetcher, 0)).toBe('d1');
		expect(await deviceLive(ENV, 'd1', a.fetcher, 0)).toBe(true);
		expect(await revokeDevice(ENV, 'd1', a.fetcher)).toEqual({ revoked: true, house: 'revoked' });
		const deletes = a.calls.filter((c) => c.method === 'DELETE').map((c) => [c.url, c.auth]);
		expect(deletes).toEqual([
			['http://adapter:8643/api/devices/keys/d1', 'Bearer master'],
			['http://house:8080/api/auth/tokens/tok_1', 'Bearer house-admin']
		]);
		expect(await deviceLive(ENV, 'd1', a.fetcher, 1)).toBe(false);
		expect((await listDeviceKeys(ENV, a.fetcher)).map((k) => k.id)).toEqual(['d2']);
	});
	it('says when there was no house token to revoke, and when there is no such device', async () => {
		const a = adapter();
		expect(await revokeDevice(ENV, 'd2', a.fetcher)).toEqual({ revoked: true, house: 'none' });
		expect(await revokeDevice(ENV, 'nope', a.fetcher)).toEqual({ revoked: false, house: 'none' });
	});
	it('leaves the house token alone when the house is Home Assistant, whose ids are not ours', async () => {
		const a = adapter();
		const ha = { ...ENV, AURA_BACKEND: 'ha', HA_URL: 'http://ha:8123', HA_TOKEN: 't' };
		expect(await revokeDevice(ha, 'd1', a.fetcher)).toEqual({ revoked: true, house: 'none' });
		expect(a.calls.some((c) => c.url.startsWith('http://ha'))).toBe(false);
	});
	it('throws what the adapter said when the list cannot be read', async () => {
		const a = adapter({ down: true });
		await expect(listDeviceKeys(ENV, a.fetcher)).rejects.toThrow(/unreachable/);
		await expect(listDeviceKeys({}, a.fetcher)).rejects.toThrow(/not configured/);
	});
});
