import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend, backendProblem } from '$lib/server/backend';
import type { RequestHandler } from './$types';

/**
 * Where Google and GitHub send the owner's browser after consent
 * (`<console origin>/api/oauth/callback`, docs/runbooks/connectors.md).
 *
 * A relay and nothing else: it forwards the query to jarvis-core's own
 * `/api/oauth/callback` and returns the page it answers. It attaches NO token
 * and no adapter key; the house route is open, and what protects it is the
 * single-use `state` jarvis-core minted when the owner asked to connect. The
 * console password still gates THIS path like every other `/api/*` one, and a
 * browser top-level navigation carries the session cookie, so only the signed-in
 * owner's browser reaches it. The platform `fetch`, not `event.fetch`, for the
 * reason `api/pair/claim` gives (no Origin stamped on the hop).
 *
 * Does not cover a consent link opened in a browser with no console session:
 * that lands on the sign-in page and the link must be opened again after.
 */
export const GET: RequestHandler = async ({ url }) => {
	const backend = resolveBackend(env);
	const problem = backendProblem(backend);
	if (problem) throw error(500, problem);

	const upstream = await fetch(
		`${backend.url.replace(/\/+$/, '')}/api/oauth/callback${url.search}`,
		{ redirect: 'error' }
	).catch(() => null);
	if (!upstream) throw error(502, 'the house server could not be reached');

	return new Response(await upstream.text(), {
		status: upstream.status,
		headers: {
			'Content-Type': 'text/html; charset=utf-8',
			'Cache-Control': 'no-store',
			'Referrer-Policy': 'no-referrer'
		}
	});
};
