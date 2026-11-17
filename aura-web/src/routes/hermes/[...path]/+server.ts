// The Hermes adapter, over HTTP, from the page's own origin.
//
// `/hermes/api/*` and `/hermes/v1/*` are forwarded to `${AURA_ADAPTER_URL}`
// with the server-held key injected — status, the audio relay, jobs, toolsets,
// skills, the OpenAI-compatible chat. The body streams both ways, so an SSE
// reply from `/v1/chat/completions` reaches the browser as it is produced.
//
// What it refuses: anything outside the two prefixes, `/api/pair` (which
// answers with the key), and a request from another origin — a page anywhere
// on the LAN must not be able to run a cron job through this console's key.
// 401 and 502 from the adapter pass straight through, so a bad key or a
// Hermes that is down is diagnosable from the browser.
import type { RequestHandler } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { isOriginAllowed, parseAllowedOrigins } from '$lib/server/backend';
import {
	FORWARDED_REQUEST_HEADERS,
	FORWARDED_RESPONSE_HEADERS,
	hermesProxyTarget,
	resolveAdapter
} from '$lib/server/hermes';

const relay: RequestHandler = async ({ params, request, url }) => {
	const adapter = resolveAdapter(env);
	if (!adapter.configured) {
		return Response.json(
			{ ok: false, error: 'server missing AURA_ADAPTER_URL/AURA_ADAPTER_KEY' },
			{ status: 503 }
		);
	}
	const origin = request.headers.get('origin');
	if (origin && !isOriginAllowed(origin, url.host, parseAllowedOrigins(env.AURA_ALLOWED_ORIGINS))) {
		return Response.json({ ok: false, error: 'forbidden origin' }, { status: 403 });
	}
	const target = hermesProxyTarget(params.path ?? '', adapter.url, url.search);
	if (!target) return Response.json({ ok: false, error: 'not relayed' }, { status: 404 });

	const headers = new Headers({ authorization: `Bearer ${adapter.key}` });
	for (const name of FORWARDED_REQUEST_HEADERS) {
		const value = request.headers.get(name);
		if (value) headers.set(name, value);
	}
	const method = request.method.toUpperCase();
	const body = method === 'GET' || method === 'HEAD' ? undefined : await request.arrayBuffer();

	let upstream: Response;
	try {
		// The platform fetch, not `event.fetch`: see routes.test.ts — SvelteKit's
		// stamps an Origin on every relayed POST.
		upstream = await fetch(target, { method, headers, body, redirect: 'error' });
	} catch (e) {
		return Response.json(
			{ ok: false, error: `adapter unreachable: ${e instanceof Error ? e.message : String(e)}` },
			{ status: 502 }
		);
	}
	const out = new Headers({ 'cache-control': 'no-store' });
	for (const name of FORWARDED_RESPONSE_HEADERS) {
		const value = upstream.headers.get(name);
		if (value) out.set(name, value);
	}
	return new Response(upstream.body, { status: upstream.status, headers: out });
};

export const GET = relay;
export const POST = relay;
export const PUT = relay;
export const PATCH = relay;
export const DELETE = relay;
