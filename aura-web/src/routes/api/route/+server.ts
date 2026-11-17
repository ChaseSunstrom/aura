import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { resolveBackend } from '$lib/server/backend';
import type { RequestHandler } from './$types';

// A driving route for the car's Map panel (`$lib/hud/route.ts`), in OSRM's shape, from the
// house's own router (jarvis-core `/api/maps/directions`, its Valhalla), through here so the
// car page needs no second origin. It was the public OSRM, which saw both ends of every
// drive (T-0765); with the house's router down there is no route, not a public one.

const point = (raw: string | null): [number, number] | null => {
	const m = /^(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)$/.exec(raw ?? '');
	if (!m) return null;
	const lat = Number(m[1]);
	const lon = Number(m[2]);
	return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
};

export const GET: RequestHandler = async ({ url }) => {
	const from = point(url.searchParams.get('from'));
	const to = point(url.searchParams.get('to'));
	if (!from || !to) throw error(400, 'from and to are "lat,lon"');
	// Stops on the way, "lat,lon;lat,lon" in order (`car_navigate`'s `via`, at most five).
	const viaRaw = url.searchParams.get('via');
	const via = viaRaw ? viaRaw.split(';').map(point) : [];
	if (via.length > 5 || via.some((p) => !p)) throw error(400, 'via is up to five "lat,lon" joined by ";"');
	const backend = resolveBackend(env);
	if (!backend.url) throw error(500, `server missing ${backend.source.url}`);
	if (!backend.token) throw error(500, `server missing ${backend.source.token}`);
	const upstream = await fetch(`${backend.url.replace(/\/+$/, '')}/api/maps/directions`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${backend.token}`, 'content-type': 'application/json' },
		body: JSON.stringify({ from, via, to }),
		redirect: 'error'
	}).catch(() => null);
	// "No route" is an answer, not a failure: the page believes it and does not ask the phone (T-1075).
	if (upstream?.status === 404) throw error(404, 'no route');
	// A request the house refused (400 bad_via, ...) is the caller's to see, with the house's
	// reason; a house that cannot answer, or fails, is a bad gateway (T-1083). 401 and 403 stay
	// 502: a rotated core token is the console's own sign-in failing, not the driver's request.
	const refused = upstream && upstream.status >= 400 && upstream.status < 500 && upstream.status !== 401 && upstream.status !== 403;
	if (upstream && refused) {
		const reason = await upstream.json().then((b) => b?.detail?.message ?? b?.message, () => null);
		const text = typeof reason === 'string' && reason ? reason.slice(0, 200) : `the house refused the route (${upstream.status})`;
		throw error(upstream.status as 400, text);
	}
	if (!upstream?.ok) throw error(502, `the house's router answered ${upstream?.status ?? 'nothing'}`);
	return json(await upstream.json());
};
