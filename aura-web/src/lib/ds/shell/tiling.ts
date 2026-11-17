// The stage's panels tile (T-0951): the owner, 2026-11-10, "lock and tile like hyprland does
// instead of just being free floating". A scene's tiles are its board slots (`SLOTS`), one
// panel to a tile, so no two ever overlap and the board's look is the arrangement nothing
// has moved. A panel dropped on a tile takes it and whoever held it takes the dropped one's
// old tile, as two windows swap. Which panel holds which tile is kept per scene on the device.
//
// Does not cover: stacking two panels in one tile, resizing a tile, or the keyboard (the
// panels keep their reading order). Kept per device on purpose: a phone's column order means
// nothing on a desktop's stage.

import type { Scene } from '../stage';

/** Which tile each panel holds, by id. */
export type Tiles = Record<string, number>;

const KEY = 'jv-tiles';
/** Where T-0887's free drag kept a panel's offset; read by nothing now. */
const OLD = 'jv-drag:';

/**
 * The tile for each of [ids] (in their default order) over [count] tiles: its kept tile when
 * that is a tile and free, else the first free one. Assumes ids.length <= count.
 */
export function place(ids: readonly string[], count: number, kept: Tiles = {}): number[] {
	const taken = new Set<number>();
	const first = ids.map((id) => {
		const t = kept[id];
		if (!Number.isInteger(t) || t < 0 || t >= count || taken.has(t)) return -1;
		taken.add(t);
		return t;
	});
	return first.map((t) => {
		if (t >= 0) return t;
		let free = 0;
		while (taken.has(free)) free++;
		taken.add(free);
		return free;
	});
}

/** [id] dropped on [tile]: whoever held it takes [id]'s old tile. */
export function swap(placed: Tiles, id: string, tile: number): Tiles {
	const out: Tiles = { ...placed, [id]: tile };
	for (const [other, t] of Object.entries(placed)) if (t === tile && other !== id) out[other] = placed[id];
	return out;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Each scene's kept tiles on this device; nothing when there is no storage or it is unreadable. */
export function loadTiles(): Partial<Record<Scene, Tiles>> {
	try {
		for (let i = localStorage.length - 1; i >= 0; i--) {
			const k = localStorage.key(i);
			if (k?.startsWith(OLD)) localStorage.removeItem(k);
		}
		const all: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}');
		if (!isObject(all)) return {};
		return Object.fromEntries(Object.entries(all).filter(([, v]) => isObject(v))) as Partial<Record<Scene, Tiles>>;
	} catch {
		// No storage (a private window) or a value from elsewhere: the board's own arrangement.
		return {};
	}
}

export function saveTiles(all: Partial<Record<Scene, Tiles>>): void {
	try {
		localStorage.setItem(KEY, JSON.stringify(all));
	} catch {
		// As above: the arrangement holds until the page is reloaded.
	}
}
