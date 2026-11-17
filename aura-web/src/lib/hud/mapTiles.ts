/**
 * Web Mercator world pixels (256 px tiles, the standard `z/x/y` scheme): where the
 * car page draws the route round the car. The tiles themselves went with T-0765.
 */
export const TILE_PX = 256;

/** The point as world pixels at [zoom]. */
export function worldPx(lat: number, lon: number, zoom: number): { x: number; y: number } {
	const scale = TILE_PX * 2 ** zoom;
	const s = Math.sin((Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI) / 180);
	return { x: ((lon + 180) / 360) * scale, y: (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale };
}
