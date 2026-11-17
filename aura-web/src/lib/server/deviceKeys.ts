// Per-device adapter keys (T-0569), as the console sees them.
//
// A phone paired since T-0569 holds its own adapter key, not AURA_ADAPTER_KEY. Its web
// view still loads this console with that key as a bearer, so the gate (`consoleAuth.admit`)
// has to know a live device key from a guess: it asks the adapter, which answers
// `GET /api/pair` with the device a key belongs to. The answers are kept thirty seconds,
// which is how long a revoke can take to reach a request that was not cut off directly;
// the revoke route below drops them at once for the device it revoked.
//
// Does not cover: an adapter that is down. A key it admitted before keeps the last answer
// (nothing can be revoked while it is down either), and a key it never answered for is
// nobody; a console restarted while the adapter is down admits no device key until it is back.
import { createHash } from 'node:crypto';
import { resolveBackend } from './backend';
import { resolveAdapter } from './hermes';

export interface DeviceKey {
	id: string;
	name: string;
	kind: string;
	created: number;
	last_seen: number | null;
	/** jarvis-core's token id for the same device, revoked with it. */
	house_token: string | null;
}

export const DEVICE_CACHE_MS = 30_000;
/** A caller chooses the bearer, so the memory of wrong ones must not grow without bound. */
const MAX_REMEMBERED = 256;

type Seen<T> = { at: number; value: T };
const byKey = new Map<string, Seen<string | null>>();
const byHouseToken = new Map<string, Seen<boolean>>();
let live: Seen<Set<string>> | null = null;

/** Test seam. */
export function resetDeviceKeys(): void {
	byKey.clear();
	byHouseToken.clear();
	live = null;
}

function bearerOf(authorization: string | null | undefined): string {
	const m = /^Bearer\s+(.+)$/i.exec(String(authorization ?? '').trim());
	return m ? m[1].trim() : '';
}

/** The id of the live device whose key [authorization] carries, or null. */
export async function deviceFor(
	env: Record<string, string | undefined>,
	authorization: string | null | undefined,
	fetcher: typeof fetch = fetch,
	now = Date.now()
): Promise<string | null> {
	const adapter = resolveAdapter(env);
	const key = bearerOf(authorization);
	if (!adapter.configured || !key) return null;
	const hash = createHash('sha256').update(key).digest('hex');
	const seen = byKey.get(hash);
	if (seen && now - seen.at < DEVICE_CACHE_MS) return seen.value;
	const answer = await fetcher(`${adapter.url}/api/pair`, {
		headers: { Authorization: `Bearer ${key}` },
		redirect: 'error'
	}).catch(() => null);
	if (!answer || answer.status >= 500) return seen?.value ?? null;
	const body = answer.ok ? ((await answer.json().catch(() => null)) as { device?: { id?: unknown } } | null) : null;
	const id = typeof body?.device?.id === 'string' ? body.device.id : null;
	byKey.delete(hash);
	if (byKey.size >= MAX_REMEMBERED) byKey.delete(byKey.keys().next().value as string);
	byKey.set(hash, { at: now, value: id });
	return id;
}

/**
 * Whether [authorization] carries a token the house itself accepts (`GET /api/`), kept
 * like a device key. A phone paired through the console holds one beside its device key
 * and calls the house relays with it (T-0782). Does not say whose token it is: jarvis-core's
 * tokens carry no scopes, so any live one is the whole house already.
 */
export async function houseTokenLive(
	env: Record<string, string | undefined>,
	authorization: string | null | undefined,
	fetcher: typeof fetch = fetch,
	now = Date.now()
): Promise<boolean> {
	const backend = resolveBackend(env);
	const token = bearerOf(authorization);
	if (!backend.url || !token) return false;
	const hash = createHash('sha256').update(token).digest('hex');
	const seen = byHouseToken.get(hash);
	if (seen && now - seen.at < DEVICE_CACHE_MS) return seen.value;
	const answer = await fetcher(`${backend.url}/api/`, {
		headers: { Authorization: `Bearer ${token}` },
		redirect: 'error'
	}).catch(() => null);
	if (!answer || answer.status >= 500) return seen?.value ?? false;
	byHouseToken.delete(hash);
	if (byHouseToken.size >= MAX_REMEMBERED) byHouseToken.delete(byHouseToken.keys().next().value as string);
	byHouseToken.set(hash, { at: now, value: answer.ok });
	return answer.ok;
}

/** Whether device [id] still has a key; false when the list was never read (fail closed). */
export async function deviceLive(
	env: Record<string, string | undefined>,
	id: string,
	fetcher: typeof fetch = fetch,
	now = Date.now()
): Promise<boolean> {
	if (!live || now - live.at >= DEVICE_CACHE_MS) {
		const keys = await listDeviceKeys(env, fetcher).catch(() => null);
		if (keys) live = { at: now, value: new Set(keys.map((k) => k.id)) };
	}
	return live?.value.has(id) ?? false;
}

function adapterOrThrow(env: Record<string, string | undefined>) {
	const adapter = resolveAdapter(env);
	if (!adapter.configured) throw new Error('the adapter is not configured (AURA_ADAPTER_URL/AURA_ADAPTER_KEY)');
	return adapter;
}

export async function listDeviceKeys(
	env: Record<string, string | undefined>,
	fetcher: typeof fetch = fetch
): Promise<DeviceKey[]> {
	const adapter = adapterOrThrow(env);
	const answer = await fetcher(`${adapter.url}/api/devices/keys`, {
		headers: { Authorization: `Bearer ${adapter.key}` },
		redirect: 'error'
	}).catch(() => null);
	if (!answer) throw new Error('the adapter is unreachable');
	if (!answer.ok) throw new Error(`the adapter answered ${answer.status}`);
	const body = (await answer.json().catch(() => null)) as { keys?: unknown } | null;
	if (!Array.isArray(body?.keys)) throw new Error('the adapter sent no key list');
	return body.keys as DeviceKey[];
}

/**
 * Revoke device [id]: its adapter key, then its house token (jarvis-core hangs up the
 * sockets that token holds). The caller closes the console sessions the device opened.
 * `house` is `failed` when the adapter key went but the house refused: the device is then
 * off Hermes but not off the house, and the screen says so.
 */
export async function revokeDevice(
	env: Record<string, string | undefined>,
	id: string,
	fetcher: typeof fetch = fetch
): Promise<{ revoked: boolean; house: 'revoked' | 'none' | 'failed' }> {
	const adapter = adapterOrThrow(env);
	const record = (await listDeviceKeys(env, fetcher)).find((k) => k.id === id);
	if (!record) return { revoked: false, house: 'none' };
	const answer = await fetcher(`${adapter.url}/api/devices/keys/${encodeURIComponent(id)}`, {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${adapter.key}` },
		redirect: 'error'
	}).catch(() => null);
	if (!answer) throw new Error('the adapter is unreachable');
	if (!answer.ok && answer.status !== 404) throw new Error(`the adapter answered ${answer.status}`);
	live = null;
	for (const [hash, seen] of byKey) if (seen.value === id) byKey.delete(hash);

	const backend = resolveBackend(env);
	// Home Assistant's token ids are not jarvis-core's: only the house that minted it revokes it.
	if (!record.house_token || backend.kind !== 'core' || !backend.configured) return { revoked: true, house: 'none' };
	const house = await fetcher(`${backend.url}/api/auth/tokens/${encodeURIComponent(record.house_token)}`, {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${backend.token}` },
		redirect: 'error'
	}).catch(() => null);
	return { revoked: true, house: house && (house.ok || house.status === 404) ? 'revoked' : 'failed' };
}
