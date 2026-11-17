/**
 * The adapter's per-device keys (T-0569) as the Devices screen shows them, read and revoked
 * through the console's own routes (`routes/api/devices/keys`). No key or hash reaches the
 * page: a key is shown once, to the device that was paired.
 */
import { agoOf } from './time';
import { wornName } from '$lib/ds/theme.svelte';

export interface DeviceKeyRow {
	id: string;
	name: string;
	kind: string;
	/** Unix seconds. */
	created: number;
	last_seen: number | null;
	house_token: string | null;
}

export type KeysLoad =
	| { status: 'loading' }
	| { status: 'ready'; keys: DeviceKeyRow[] }
	| { status: 'error'; detail: string }
	| { status: 'offline' };

const errorOf = async (res: Response): Promise<string> =>
	((await res.json().catch(() => null)) as { error?: string } | null)?.error ?? `the console answered ${res.status}`;

export async function loadDeviceKeys(fetchImpl: typeof fetch = fetch): Promise<KeysLoad> {
	let res: Response;
	try {
		res = await fetchImpl('/api/devices/keys', { headers: { Accept: 'application/json' } });
	} catch {
		return { status: 'offline' };
	}
	// Only the reason: the screen's State says "Couldn't load this" itself.
	if (!res.ok) return { status: 'error', detail: await errorOf(res) };
	const body = (await res.json().catch(() => null)) as { keys?: unknown } | null;
	return Array.isArray(body?.keys)
		? { status: 'ready', keys: body.keys as DeviceKeyRow[] }
		: { status: 'error', detail: 'the answer had no list of keys' };
}

/**
 * Revoke one; resolves to a sentence when only half of it took (the house kept its token),
 * null when all of it did. Throws what went wrong otherwise.
 */
export async function revokeDeviceKey(id: string, fetchImpl: typeof fetch = fetch): Promise<string | null> {
	const res = await fetchImpl(`/api/devices/keys/${encodeURIComponent(id)}`, { method: 'DELETE' });
	if (!res.ok) throw new Error(`Couldn't revoke it: ${await errorOf(res)}.`);
	const body = (await res.json().catch(() => null)) as { house?: string } | null;
	return body?.house === 'failed'
		? `Revoked from ${wornName()}, but the house kept the device's token: revoke it there, or try again.`
		: null;
}

/** "phone · paired 8 Nov · seen 3 minutes ago", or "never seen" before its first call. */
export function keyMeta(k: DeviceKeyRow, now = Date.now()): string {
	const day = new Date(k.created * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' });
	const seen = k.last_seen ? `seen ${agoOf(k.last_seen * 1000, now)}` : 'never seen';
	return [k.kind, `paired ${day}`, seen].join(' · ');
}
