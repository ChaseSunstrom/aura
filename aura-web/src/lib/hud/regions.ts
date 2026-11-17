// The house's map regions (T-0979), as the console's Devices screen lists them: what a
// state is called, how a size reads, and when the list is worth reading again. The house
// owns the list (`/api/maps/regions`, jarvis-core `maps/regions.py`); the states are
// `tests/contracts/map-pack.json`'s.

export interface Region {
	id: string;
	name: string;
	state: string;
	note?: string;
	bytes?: number;
}

/** A region the catalogue offers: Geofabrik's, by the id an ask names. */
export interface Offered {
	id: string;
	name: string;
	parent?: string;
}

/** The home region is the house's own (`map-region.sh`); it is never removed from here. */
export const HOME_REGION = 'home';

/** How often the list is read again while a region is on its way in or out. */
export const REGIONS_POLL_MS = 5_000;

export const STATE_WORD: Record<string, string> = {
	queued: 'Waiting to download',
	working: 'Downloading',
	ready: 'Ready',
	failed: 'Failed',
	held: 'On hold',
	removing: 'Removing'
};

const MB = 2 ** 20;

/** "116 MB", "1.4 GB"; nothing for a region with no pack yet. */
export function sizeWords(bytes: number | undefined): string {
	if (!bytes || bytes <= 0) return '';
	const mb = bytes / MB;
	return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`;
}

/** One line under a region's name: its state, then its size or what the worker last said. */
export function regionLine(r: Region): string {
	const word = STATE_WORD[r.state] ?? r.state;
	const more = r.state === 'ready' ? sizeWords(r.bytes) : (r.note ?? '');
	return more ? `${word} · ${more}` : word;
}

/** How many of a search's results the screen lists, and how many the house sends at most
 * (jarvis-core `maps/regions.py` `catalogue`, whose default `limit` is 20). */
export const OFFERED_SHOWN = 8;
export const CATALOGUE_LIMIT = 20;

/** A line for a search with more results than the list shows; nothing when all are shown. */
export function moreResults(count: number): string {
	if (count <= OFFERED_SHOWN) return '';
	const of = count >= CATALOGUE_LIMIT ? `at least ${CATALOGUE_LIMIT}` : String(count);
	return `Showing ${OFFERED_SHOWN} of ${of}. Add a word to narrow the search.`;
}

/** Whether any region is still changing, so the list should be read again soon. */
export function unsettled(regions: Region[]): boolean {
	return regions.some((r) => r.state === 'queued' || r.state === 'working' || r.state === 'removing');
}
