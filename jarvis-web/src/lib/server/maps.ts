// The car's maps through the console (T-0660): a phone paired to the console asks it for
// `/api/maps/*` (the car hologram's `HouseMaps.kt`), and the house answers them. Only these
// four ops, each with the one method the house serves it on: the token this attaches must
// not reach the rest of the house's authenticated surface.

/** The op the phone names and the method it uses for it. */
export const MAPS_OPS: Record<string, 'GET' | 'POST'> = {
	route: 'POST',
	match: 'POST',
	buildings: 'GET',
	live: 'GET' // T-1108: the work zones under way in a box
};

/** A route or a match of 100 points is a few KiB; anything past this is not the phone. */
export const MAPS_BODY_MAX = 64 * 1024;

/** `west,south,east,north` in degrees, as `HouseMaps.buildings` writes it. */
export const BBOX = /^-?\d{1,3}(?:\.\d{1,8})?(?:,-?\d{1,3}(?:\.\d{1,8})?){3}$/;

/**
 * The house URL for one maps call, or null when the op, the method or the bbox is not
 * one the phone sends. The bbox's extent is the house's to cap (0.1 degrees); this only
 * keeps anything but four numbers out of the URL.
 */
export function mapsTarget(
	base: string,
	op: string,
	method: string,
	bbox: string | null
): string | null {
	if (!Object.hasOwn(MAPS_OPS, op) || MAPS_OPS[op] !== method) return null;
	const root = `${base.replace(/\/+$/, '')}/api/maps/${op}`;
	if (op !== 'buildings' && op !== 'live') return root;
	return bbox !== null && BBOX.test(bbox) ? `${root}?bbox=${bbox}` : null;
}

// --- map regions (T-0979) ----------------------------------------------------

/** A region's id (`tests/contracts/map-pack.json` `id`): Geofabrik's, with "/" as "-". */
export const REGION_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;

/** A name typed into the catalogue search; longer is not a place name. */
export const CATALOGUE_Q_MAX = 80;

/**
 * The house URL for one map-region call (the contract's `calls`), or null for anything
 * else: the list and an ask (`regions`, GET and POST), the catalogue search (`catalogue`,
 * GET with `q`), a removal (`regions/<id>`, DELETE) and a pack's bytes (`packs/<id>`, GET).
 * The id is checked to the contract's rule, so what is appended is an id and never a path.
 */
export function regionsTarget(base: string, parts: string[], method: string, q: string | null): string | null {
	const root = `${base.replace(/\/+$/, '')}/api/maps`;
	const [op, id] = parts;
	if (parts.length === 1) {
		if (op === 'regions' && (method === 'GET' || method === 'POST')) return `${root}/regions`;
		if (op === 'catalogue' && method === 'GET' && (q ?? '').length <= CATALOGUE_Q_MAX) {
			return `${root}/catalogue?q=${encodeURIComponent(q ?? '')}`;
		}
		return null;
	}
	if (parts.length !== 2 || !REGION_ID.test(id)) return null;
	if (op === 'regions' && method === 'DELETE') return `${root}/regions/${id}`;
	if (op === 'packs' && method === 'GET') return `${root}/packs/${id}`;
	return null;
}
