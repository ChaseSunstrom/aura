import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend } from '$lib/server/backend';
import { regionsTarget } from '$lib/server/maps';
import type { RequestHandler } from './$types';

// One map region (T-0979): `DELETE /api/maps/regions/<id>` removes it from the house, and
// `GET /api/maps/packs/<id>` is its pack, which a phone paired to the console copies on Wi-Fi.
// A pack is hundreds of MB, so its body is passed through as a stream and never held here,
// and a `Range` (a resumed download) goes up and its 206 comes back as it is.
const RANGE = /^bytes=\d*-\d*$/;
const PASSED = ['content-type', 'content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified'];

const relay: RequestHandler = async ({ params, request }) => {
	const backend = resolveBackend(env);
	if (!backend.url) throw error(500, `server missing ${backend.source.url}`);
	if (!backend.token) throw error(500, `server missing ${backend.source.token}`);
	const target = regionsTarget(backend.url, [params.op, params.region], request.method, null);
	if (!target) throw error(404, 'no such maps call');

	const range = request.headers.get('range');
	const upstream = await globalThis
		.fetch(target, {
			method: request.method,
			headers: { Authorization: `Bearer ${backend.token}`, ...(range && RANGE.test(range) ? { range } : {}) },
			redirect: 'error'
		})
		.catch(() => null);
	if (!upstream) throw error(502, 'the house did not answer');
	const headers = new Headers();
	for (const name of PASSED) {
		const value = upstream.headers.get(name);
		if (value) headers.set(name, value);
	}
	return new Response(upstream.body, { status: upstream.status, headers });
};

export const GET = relay;
export const DELETE = relay;
