import { describe, it, expect } from 'vitest';
import { keyMeta, loadDeviceKeys, revokeDeviceKey } from './deviceKeys';

const row = { id: 'd1', name: 'Pixel', kind: 'phone', created: 1_700_000_000, last_seen: null, house_token: null };
const answer = (body: unknown, status = 200) => (async () => Response.json(body, { status })) as unknown as typeof fetch;

describe('device keys on the Devices screen (T-0569)', () => {
	it('reads the list, says why it could not, and is offline when the console is unreachable', async () => {
		expect(await loadDeviceKeys(answer({ keys: [row] }))).toEqual({ status: 'ready', keys: [row] });
		expect(await loadDeviceKeys(answer({ error: 'the adapter is unreachable' }, 502))).toEqual({
			status: 'error',
			detail: 'the adapter is unreachable'
		});
		expect((await loadDeviceKeys(answer({}))).status).toBe('error');
		const down = (async () => {
			throw new TypeError('fetch failed');
		}) as unknown as typeof fetch;
		expect(await loadDeviceKeys(down)).toEqual({ status: 'offline' });
	});
	it('revokes, and says so when the house kept its token', async () => {
		expect(await revokeDeviceKey('d1', answer({ ok: true, house: 'revoked' }))).toBeNull();
		expect(await revokeDeviceKey('d1', answer({ ok: true, house: 'failed' }))).toMatch(/house kept/);
		await expect(revokeDeviceKey('d1', answer({ error: 'no such device key' }, 404))).rejects.toThrow(
			"Couldn't revoke it: no such device key."
		);
	});
	it('says when a key was last used, and never before its first call', () => {
		expect(keyMeta(row)).toMatch(/^phone · paired .+ · never seen$/);
		expect(keyMeta({ ...row, last_seen: 1_700_000_000 }, 1_700_000_180_000)).toMatch(/seen 3 minutes ago$/);
	});
});
