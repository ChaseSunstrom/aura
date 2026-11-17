import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { listDeviceKeys } from '$lib/server/deviceKeys';
import type { RequestHandler } from './$types';

// The adapter's device keys (T-0569) for the Devices screen: name, kind, when last seen.
// Behind the console gate like every /api path; the adapter itself takes only the master
// key, which stays on this server. No key and no hash is in the answer.
export const GET: RequestHandler = async () => {
	try {
		return json({ keys: await listDeviceKeys(env) });
	} catch (e) {
		return json({ error: e instanceof Error ? e.message : String(e) }, { status: 502 });
	}
};
