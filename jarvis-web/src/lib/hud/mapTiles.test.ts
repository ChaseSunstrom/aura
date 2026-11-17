import { describe, expect, it } from 'vitest';
import { TILE_PX, worldPx } from './mapTiles';

describe('the car map projection', () => {
	it('a point falls in the tile the OSM wiki formula names (Chicago, zoom 15: 8407/12178)', () => {
		const [lat, lon, z] = [41.8781, -87.6298, 15];
		const r = (lat * Math.PI) / 180;
		const wiki = [Math.floor(((lon + 180) / 360) * 2 ** z), Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z)];
		const p = worldPx(lat, lon, z);
		expect([Math.floor(p.x / TILE_PX), Math.floor(p.y / TILE_PX)]).toEqual(wiki);
		expect(wiki).toEqual([8407, 12178]);
	});
});
