import type { RequestHandler } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { FORWARDED_REQUEST_HEADERS, FORWARDED_RESPONSE_HEADERS, LLM_PREFIXES, hermesProxyTarget } from '$lib/server/hermes';

// llama-swap's OpenAI API on the console's origin (`/llm/v1/*`), for a caller
// that wants the raw model with no Hermes memory or tools. This server holds no
// key: the caller's own bearer goes through and llama-swap checks it against
// LLM_API_KEY, so a call without it gets llama-swap's 401.
// ponytail: Node fetch gives up after 300 s with no response headers; a cold load
// of a 27B can take ~7 min, so the first call after an eviction may 502 while
// llama-swap keeps loading. Pass an undici Agent with a longer headersTimeout if
// that bites.
const relay: RequestHandler = async ({ params, request, url }) => {
	const base = String(env.AURA_LLM_URL ?? '').trim().replace(/\/+$/, '');
	if (!base) return Response.json({ ok: false, error: 'server missing AURA_LLM_URL' }, { status: 503 });
	const target = hermesProxyTarget(params.path ?? '', base, url.search, LLM_PREFIXES);
	if (!target) return Response.json({ ok: false, error: 'not relayed' }, { status: 404 });
	const headers = new Headers();
	for (const name of ['authorization', ...FORWARDED_REQUEST_HEADERS]) {
		const value = request.headers.get(name);
		if (value) headers.set(name, value);
	}
	const method = request.method.toUpperCase();
	const body = method === 'GET' || method === 'HEAD' ? undefined : await request.arrayBuffer();
	let upstream: Response;
	try {
		// The platform fetch, not `event.fetch`, as in the /hermes relay. The signal
		// stops a generation the caller hung up on.
		upstream = await fetch(target, { method, headers, body, redirect: 'error', signal: request.signal });
	} catch (e) {
		return Response.json({ ok: false, error: `llama-swap unreachable: ${e instanceof Error ? e.message : String(e)}` }, { status: 502 });
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
