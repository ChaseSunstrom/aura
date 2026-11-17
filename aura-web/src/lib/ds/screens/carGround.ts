// The car page's own street map (T-1521): the house's roads and building footprints round the
// car, drawn under the route when the phone draws no hologram ground. Before this the page drew
// the route on an empty ground, since the street tiles went with T-0765 (OpenStreetMap's tile
// servers heard where the car was); the README's car clip showed a line on black. The streets
// come from the house's own region packs (`/api/maps/buildings`, the phone's hologram source),
// through the console's own origin: nothing outside the house hears the box.
//
// Not covered: the phone's hologram (that is the phone's own renderer), labels, and any place
// the house has no pack for, where the answer is a 404 and the route is drawn alone, as before.

import { TILE_PX, worldPx } from '$lib/hud/mapTiles';

/** `west,south,east,north` in degrees, as the house's buildings route takes it. */
export type Box = [number, number, number, number];

type LonLat = [number, number];
/** The part of the house's `/api/maps/buildings` answer drawn here (aura-core maps/pack.py `read`). */
export interface StreetsBody {
	roads?: { line: LonLat[]; kind?: string }[];
	buildings?: { ring: LonLat[] }[];
}

export type RoadBand = 'major' | 'mid' | 'minor';

export interface Streets {
	/** Minor roads first, so a major road is drawn over the side streets that meet it. */
	roads: { d: string; band: RoadBand }[];
	buildings: string[];
}

/** How far past the map's own edges the box reaches (a fraction of the map), so the car can
 * drive a while before the streets run out. */
const ROOM = 0.5;

const MAJOR = new Set(['motorway', 'trunk', 'primary']);
const MID = new Set(['secondary', 'tertiary']);

/** The weight a highway class is drawn at; `_link` roads take their road's. */
export function band(kind: string | undefined): RoadBand {
	const base = (kind ?? '').replace(/_link$/, '');
	return MAJOR.has(base) ? 'major' : MID.has(base) ? 'mid' : 'minor';
}

/** The inverse of worldPx: world pixels at [zoom] back to degrees. */
function degrees(x: number, y: number, zoom: number): LonLat {
	const scale = TILE_PX * 2 ** zoom;
	const lon = (x / scale) * 360 - 180;
	const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / scale))) * 180) / Math.PI;
	return [lon, lat];
}

/** The box a [width]×[height] px map at [zoom] round the car shows, with room to drive. */
export function groundBox(lat: number, lon: number, width: number, height: number, zoom: number): Box {
	const c = worldPx(lat, lon, zoom);
	const hw = (width / 2) * (1 + ROOM);
	const hh = (height / 2) * (1 + ROOM);
	const [west, north] = degrees(c.x - hw, c.y - hh, zoom);
	const [east, south] = degrees(c.x + hw, c.y + hh, zoom);
	return [west, south, east, north];
}

const finite = (p: unknown): p is LonLat =>
	Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]);

/** [points] as an SVG path in pixels from [origin], or '' when fewer than [least] can be drawn. */
function trace(points: unknown, origin: { x: number; y: number }, zoom: number, least: number): string {
	if (!Array.isArray(points) || points.length < least || !points.every(finite)) return '';
	return points
		.map(([lon, lat], i) => {
			const q = worldPx(lat, lon, zoom);
			return `${i ? 'L' : 'M'}${(q.x - origin.x).toFixed(1)},${(q.y - origin.y).toFixed(1)}`;
		})
		.join(' ');
}

const ORDER: Record<RoadBand, number> = { minor: 0, mid: 1, major: 2 };

/**
 * The house's answer as SVG paths in pixels from [origin] (the car's world pixel when it was
 * asked). The page moves them with one translate as the car drives, so a fix redraws nothing.
 */
export function groundPaths(body: StreetsBody | null, origin: { x: number; y: number }, zoom: number): Streets {
	const roads = Array.isArray(body?.roads) ? body.roads : [];
	const buildings = Array.isArray(body?.buildings) ? body.buildings : [];
	return {
		roads: roads
			.map((r) => ({ d: trace(r?.line, origin, zoom, 2), band: band(r?.kind) }))
			.filter((r) => r.d)
			.sort((a, b) => ORDER[a.band] - ORDER[b.band]),
		buildings: buildings.map((b) => trace(b?.ring, origin, zoom, 3)).filter(Boolean).map((d) => `${d} Z`)
	};
}

/** Whether to ask the house again: never asked, or the car is a quarter of the map from where it was. */
export function needsStreets(
	askedAt: { x: number; y: number } | null,
	now: { x: number; y: number },
	width: number,
	height: number
): boolean {
	return !askedAt || Math.abs(now.x - askedAt.x) > width / 4 || Math.abs(now.y - askedAt.y) > height / 4;
}
