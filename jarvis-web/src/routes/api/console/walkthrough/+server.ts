import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { parse, serialize } from '$lib/ds/walkthrough/model';
import { readWalkthrough, writeWalkthrough } from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// The first-run walkthrough's progress (T-0629): which steps the owner acknowledged or
// skipped, and whether they left it. Behind the console's session like every other /api path
// (`gated`), so only a signed-in console reads or writes it.
// Does not cover: what is done in the house; the screen reads that from the house each time.

export const GET: RequestHandler = () => json({ progress: readWalkthrough(env) });

export const PUT: RequestHandler = async ({ request }) => {
	const body = await request.json().catch(() => ({}));
	// Stored as the model wrote it back, never as sent: a row is six marks and a flag at most.
	const row = parse(typeof body?.progress === 'string' ? body.progress : null);
	if (!row) throw error(400, 'not a walkthrough progress row');
	try {
		writeWalkthrough(env, serialize(row));
	} catch {
		throw error(500, 'could not save the walkthrough progress');
	}
	return json({ ok: true });
};
