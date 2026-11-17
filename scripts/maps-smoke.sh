#!/usr/bin/env bash
# scripts/maps-smoke.sh — does the house's map answer what the car draws and reads?
#
#   scripts/maps-smoke.sh [CONTAINER]   (default jarvis-web: it holds the house URL and token)
#
# The car's hologram was empty and its limit "unknown" everywhere because the house had no
# region loaded and no Valhalla (T-0782, T-0781): nothing failed loudly, every route just
# answered empty. This asks the house, from inside the console's container with the token
# it already holds, for the region, the buildings around its middle, and a map-match of a
# drive there: a route out of the middle (Valhalla snaps its ends to roads), then a match of
# the route's own points, as the car sends its fixes. It prints counts and the limit's source
# only: no coordinate, street name or token. Exit 1 when any of them comes back empty.
#
# Does not cover: whether the phone reaches these routes (the console gate, consoleAuth
# tests). A limit of "none" is not a failure (the road has no posted or usual limit); an
# error or no answer is.
set -euo pipefail
CONTAINER=${1:-jarvis-web}
docker exec -i "$CONTAINER" node --input-type=module - <<'JS'
const url = (process.env.AURA_URL || process.env.HA_URL || '').replace(/\/+$/, '');
const token = process.env.AURA_TOKEN || process.env.HA_TOKEN || '';
if (!url || !token) { console.log('no house url or token in this container'); process.exit(1); }
const ask = async (path, body) => {
	const r = await fetch(url + path, {
		method: body ? 'POST' : 'GET',
		headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
		body: body ? JSON.stringify(body) : undefined
	});
	return { status: r.status, body: await r.json().catch(() => null) };
};
let failed = false;
const region = await ask('/api/maps/region');
const bbox = region.body?.bbox;
console.log(`region: ${region.status} source=${region.body?.source ?? '?'}`);
if (!Array.isArray(bbox) || bbox.length !== 4) process.exit(1);
const lat = (bbox[1] + bbox[3]) / 2, lon = (bbox[0] + bbox[2]) / 2, d = 0.004;
const q = new URLSearchParams({ bbox: [lon - d, lat - d, lon + d, lat + d].join(',') });
const b = await ask(`/api/maps/buildings?${q}`);
const n = b.body?.features?.length ?? b.body?.buildings?.length ?? 0;
console.log(`buildings: ${b.status} count=${n}`);
if (b.status !== 200 || !n) failed = true;
const r = await ask('/api/maps/route', { from: [lat, lon], to: [lat + 0.01, lon + 0.01] });
const shape = Array.isArray(r.body?.shape) ? r.body.shape : [];
console.log(`route: ${r.status} points=${shape.length}`);
if (shape.length < 3) process.exit(1);
const mid = Math.floor(shape.length / 2);
const m = await ask('/api/maps/match', { points: shape.slice(Math.max(0, mid - 3), mid + 1) });
console.log(`match: ${m.status} limit_source=${m.body?.limit_source ?? '-'} limit=${m.body?.limit_mph ?? '-'} road=${m.body?.road ?? '-'}`);
if (m.status !== 200 || !m.body || 'error' in m.body) failed = true;
process.exit(failed ? 1 : 0);
JS
