import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend } from '$lib/server/backend';
import { MAPS_BODY_MAX, mapsTarget, regionsTarget } from '$lib/server/maps';
import type { RequestHandler } from './$types';

// The car hologram's maps (`HouseMaps.kt`) for a phone paired to the console (T-0660): the
// house answers them, and without this the console 404s a drive that has a working house.
// The house's status and body pass through as they are, since the phone tells "no route"
// (a 404 with a code) from "the house is away" by them.
const relay: RequestHandler = async ({ params, request, url }) => {
	const backend = resolveBackend(env);
	if (!backend.url) throw error(500, `server missing ${backend.source.url}`);
	if (!backend.token) throw error(500, `server missing ${backend.source.token}`);
	// The map regions' list, an ask for one and the catalogue search ride the same relay (T-0979).
	const target =
		mapsTarget(backend.url, params.op, request.method, url.searchParams.get('bbox')) ??
		regionsTarget(backend.url, [params.op], request.method, url.searchParams.get('q'));
	if (!target) throw error(404, 'no such maps call');

	let body: string | undefined;
	if (request.method === 'POST') {
		body = await request.text();
		if (body.length > MAPS_BODY_MAX) throw error(413, 'a maps request is a few KiB');
	}
	const upstream = await globalThis
		.fetch(target, {
			method: request.method,
			headers: {
				Authorization: `Bearer ${backend.token}`,
				...(body === undefined ? {} : { 'content-type': 'application/json' })
			},
			body,
			redirect: 'error'
		})
		.catch(() => null);
	if (!upstream) throw error(502, 'the house did not answer');
	return new Response(await upstream.text(), {
		status: upstream.status,
		headers: { 'content-type': upstream.headers.get('content-type') ?? 'application/json' }
	});
};

export const GET = relay;
export const POST = relay;
