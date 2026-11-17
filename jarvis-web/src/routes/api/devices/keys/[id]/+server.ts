import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { closeDeviceSessions } from '$lib/server/consoleAuth';
import { revokeDevice } from '$lib/server/deviceKeys';
import type { RequestHandler } from './$types';

// Revoke one device (T-0569): its adapter key (the adapter hangs up its sockets), its house
// token (so does jarvis-core), and every console session its key opened here, at once. The
// sockets those sessions opened close at their next sign-in check (`watchSignIn`).
export const DELETE: RequestHandler = async ({ params }) => {
	try {
		const done = await revokeDevice(env, params.id);
		if (!done.revoked) return json({ error: 'no such device key' }, { status: 404 });
		closeDeviceSessions(params.id);
		return json({ ok: true, id: params.id, house: done.house });
	} catch (e) {
		return json({ error: e instanceof Error ? e.message : String(e) }, { status: 502 });
	}
};
