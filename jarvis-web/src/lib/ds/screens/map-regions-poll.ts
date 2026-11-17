// The map-regions screen's poll loop (T-0979, T-1268): read the house's list, then read it
// again while a region is still changing. It lives here, not in MapRegions.svelte, because
// the screen has no component-test setup and the re-arm decision has to be testable.

import { REGIONS_POLL_MS, unsettled, type Region } from '../../hud/regions';

/** Where one read lands: the list, or the reason it did not arrive. */
export interface RegionsSink {
	ok(regions: Region[]): void;
	fail(error: unknown): void;
}

export interface RegionsPoll {
	/** One read now; re-arms the next one only while the poll is live and the list is changing. */
	read(): Promise<void>;
	/** The screen is gone: nothing re-arms from here on, including a read already in flight. */
	stop(): void;
}

/**
 * `load` is one fetch of the list; it throws on a refusal or a dead link. The sink takes
 * the outcome. Does not cover: a fetch in flight when the screen closes is not aborted; it
 * runs to its end and its result is dropped from the poll (not from the screen's state).
 */
/**
 * The most the wait grows: 8x the base, so a dead house is asked every 40 s, not every 5 s.
 * ponytail: a fixed ceiling, not a jittered exponential; add jitter if many screens share one house.
 */
const MAX_BACKOFF = 8;

export function regionsPoll(load: () => Promise<Region[]>, sink: RegionsSink): RegionsPoll {
	let gone = false;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let last: Region[] | null = null;
	// Consecutive failed reads; one good read clears it. Without it a house that is down for an
	// evening is asked every 5 s for the whole evening.
	let fails = 0;
	let inFlight: Promise<void> | null = null;

	function read(): Promise<void> {
		// Overlapping calls share the read in flight. The screen's own visibilitychange listener
		// and this poll's both re-read when the page comes back; without sharing, one visible
		// change costs two requests.
		inFlight ??= fetchOnce().finally(() => {
			inFlight = null;
		});
		return inFlight;
	}

	async function fetchOnce(): Promise<void> {
		clearTimeout(timer);
		try {
			last = await load();
			fails = 0;
			sink.ok(last);
		} catch (e) {
			fails++;
			sink.fail(e);
		}
		// Checked after the await: a read that was in flight when the screen closed must not
		// arm a timer that outlives the screen and polls the house for good (T-1268).
		if (gone || !last || !unsettled(last)) return;
		// Clear again before arming: two overlapping reads must not leave two timers, only one
		// of which stop() could reach.
		clearTimeout(timer);
		timer = setTimeout(fire, REGIONS_POLL_MS * Math.min(2 ** fails, MAX_BACKOFF));
	}

	function fire(): void {
		// A timer that fires on a hidden page does not read: it waits for the page to be seen
		// again, then reads once. Reading while hidden spends the house's time on nobody.
		if (typeof document !== 'undefined' && document.hidden) {
			document.addEventListener('visibilitychange', onVisible);
			return;
		}
		void read();
	}

	function onVisible(): void {
		if (document.hidden) return;
		document.removeEventListener('visibilitychange', onVisible);
		if (!gone) void read();
	}

	return {
		read,
		stop() {
			gone = true;
			clearTimeout(timer);
			if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible);
		}
	};
}
