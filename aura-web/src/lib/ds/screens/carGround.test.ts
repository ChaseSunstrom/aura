import { describe, expect, it } from 'vitest';
import { worldPx } from '$lib/hud/mapTiles';
import { band, groundBox, groundPaths, needsStreets } from './carGround';

// A made-up place (no real street): the projection is checked against worldPx itself.
const AT = { lat: 45, lon: 10 };
const ZOOM = 16;

describe('groundBox', () => {
	it('covers the map round the car, with room to drive, as west,south,east,north', () => {
		const [w, s, e, n] = groundBox(AT.lat, AT.lon, 800, 480, ZOOM);
		expect(w).toBeLessThan(AT.lon);
		expect(e).toBeGreaterThan(AT.lon);
		expect(s).toBeLessThan(AT.lat);
		expect(n).toBeGreaterThan(AT.lat);
		const c = worldPx(AT.lat, AT.lon, ZOOM);
		// The corners land a map and a half wide and high, centred on the car.
		expect(worldPx(n, w, ZOOM).x).toBeCloseTo(c.x - 600, 3);
		expect(worldPx(n, w, ZOOM).y).toBeCloseTo(c.y - 360, 3);
		expect(worldPx(s, e, ZOOM).x).toBeCloseTo(c.x + 600, 3);
	});
});

describe('groundPaths', () => {
	const origin = worldPx(AT.lat, AT.lon, ZOOM);
	const east = worldPx(AT.lat, AT.lon + 0.001, ZOOM);

	it('draws each road as a line in pixels from the origin, minor roads under major ones', () => {
		const g = groundPaths(
			{
				roads: [
					{ line: [[AT.lon, AT.lat], [AT.lon + 0.001, AT.lat]], kind: 'primary' },
					{ line: [[AT.lon, AT.lat], [AT.lon, AT.lat + 0.001]], kind: 'residential' }
				]
			},
			origin,
			ZOOM
		);
		expect(g.roads.map((r) => r.band)).toEqual(['minor', 'major']);
		expect(g.roads[1].d).toBe(`M0.0,0.0 L${(east.x - origin.x).toFixed(1)},0.0`);
	});

	it('closes each building footprint', () => {
		const g = groundPaths(
			{ buildings: [{ ring: [[AT.lon, AT.lat], [AT.lon + 0.001, AT.lat], [AT.lon + 0.001, AT.lat + 0.001]] }] },
			origin,
			ZOOM
		);
		expect(g.buildings).toHaveLength(1);
		expect(g.buildings[0]).toMatch(/^M0\.0,0\.0 L[\d.-]+,0\.0 L[\d.-]+,-[\d.]+ Z$/);
	});

	it('drops what cannot be drawn: a one-point road, a two-point ring, a value that is not a number', () => {
		const g = groundPaths(
			{
				roads: [{ line: [[AT.lon, AT.lat]] }, { line: [[AT.lon, 'x' as unknown as number], [AT.lon, AT.lat]] }],
				buildings: [{ ring: [[AT.lon, AT.lat], [AT.lon, AT.lat]] }]
			},
			origin,
			ZOOM
		);
		expect(g).toEqual({ roads: [], buildings: [] });
	});

	it('draws nothing for an answer that is not the house shape', () => {
		expect(groundPaths(null, origin, ZOOM)).toEqual({ roads: [], buildings: [] });
		expect(groundPaths({ roads: 'nope' } as never, origin, ZOOM)).toEqual({ roads: [], buildings: [] });
	});
});

describe('band', () => {
	it('sorts the highway classes into three weights', () => {
		expect(band('motorway')).toBe('major');
		expect(band('trunk_link')).toBe('major');
		expect(band('tertiary')).toBe('mid');
		expect(band('service')).toBe('minor');
		expect(band(undefined)).toBe('minor');
	});
});

describe('needsStreets', () => {
	it('asks again only once the car has driven a quarter of the map from where the streets were asked', () => {
		const at = { x: 1000, y: 1000 };
		expect(needsStreets(null, at, 800, 480)).toBe(true);
		expect(needsStreets(at, { x: 1150, y: 1000 }, 800, 480)).toBe(false);
		expect(needsStreets(at, { x: 1201, y: 1000 }, 800, 480)).toBe(true);
		expect(needsStreets(at, { x: 1000, y: 1121 }, 800, 480)).toBe(true);
	});
});
