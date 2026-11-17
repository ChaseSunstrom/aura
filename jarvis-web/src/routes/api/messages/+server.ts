import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem } from '$lib/server/backend';
import type { RequestHandler } from './$types';

// The owner's mirrored texts for the Texts screen: jarvis-core's
// `GET /api/messages`, read with the admin token, which stays on the server.
//
// Behind the console gate like every route not exempted in hooks.server.ts:
// these are other people's words. Read-only and one path; only `limit` (a
// number, clamped) and `conversation` (a name, checked, then URL-encoded) reach
// the upstream URL, so nothing the browser sends becomes a path or a second
// query. The body is relayed as jarvis-core wrote it, `trust: "untrusted"`
// included, and never cached.
const MAX_LIMIT = 100;
const MAX_CONVERSATION = 128;

export const GET: RequestHandler = async ({ url }) => {
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	const limit = Math.min(MAX_LIMIT, Math.max(1, Math.floor(Number(url.searchParams.get('limit')) || 20)));
	const conversation = url.searchParams.get('conversation') ?? '';
	// eslint-disable-next-line no-control-regex -- refusing control characters in a name is the intent here.
	if (conversation.length > MAX_CONVERSATION || /[\u0000-\u001f\u007f]/.test(conversation)) throw error(400, 'not a conversation name');

	const query = new URLSearchParams({ limit: String(limit) });
	if (conversation) query.set('conversation', conversation);
	const upstream = await globalThis
		.fetch(`${backend.url.replace(/\/+$/, '')}/api/messages?${query}`, {
			headers: { Authorization: `Bearer ${backend.token}` },
			redirect: 'error'
		})
		.catch(() => null);
	if (!upstream) throw error(502, 'the house did not answer');
	// Always 502: the house's own 401 (a rotated core token) relayed as-is read as "The
	// console answered 401", which sent the owner to sign in again for nothing (T-0673).
	if (!upstream.ok) throw error(502, 'the house would not read the texts');
	const body: unknown = await upstream.json().catch(() => undefined);
	if (body === undefined) throw error(502, 'the house answered something that is not the texts');
	return json(body, { headers: { 'cache-control': 'no-store' } });
};
