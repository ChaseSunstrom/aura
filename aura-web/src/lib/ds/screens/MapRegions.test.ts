import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { REGIONS_POLL_MS, type Region } from '../../hud/regions';
import { regionsPoll } from './map-regions-poll';

const changing: Region[] = [{ id: 'us-utah', name: 'Utah', state: 'working' }];
const settled: Region[] = [{ id: 'us-utah', name: 'Utah', state: 'ready' }];

const sink = () => ({ ok: vi.fn(), fail: vi.fn() });

describe('the map regions poll', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	it('reads again while a region is still changing', async () => {
		const load = vi.fn(async () => changing);
		const poll = regionsPoll(load, sink());
		await poll.read();
		await vi.advanceTimersByTimeAsync(REGIONS_POLL_MS);
		expect(load).toHaveBeenCalledTimes(2);
		poll.stop();
	});

	it('stops reading once every region has settled', async () => {
		const load = vi.fn(async () => settled);
		const poll = regionsPoll(load, sink());
		await poll.read();
		await vi.advanceTimersByTimeAsync(REGIONS_POLL_MS * 10);
		expect(load).toHaveBeenCalledTimes(1);
	});

	it('a read still in flight when the screen is destroyed arms no further poll', async () => {
		let land: (regions: Region[]) => void = () => {};
		const load = vi.fn(() => new Promise<Region[]>((resolve) => (land = resolve)));
		const poll = regionsPoll(load, sink());
		const pending = poll.read();
		poll.stop(); // the screen closes while the read is out on the wire
		land(changing);
		await pending;
		await vi.advanceTimersByTimeAsync(REGIONS_POLL_MS * 10);
		expect(load).toHaveBeenCalledTimes(1);
	});

	it('a timer armed before the screen is destroyed is cleared, not left to fire', async () => {
		const load = vi.fn(async () => changing);
		const poll = regionsPoll(load, sink());
		await poll.read();
		poll.stop(); // a poll is armed and waiting when the screen closes
		await vi.advanceTimersByTimeAsync(REGIONS_POLL_MS * 10);
		expect(load).toHaveBeenCalledTimes(1);
	});
});

/** A stand-in for the one document surface the poll touches: visibility and its listeners. */
function fakeDocument() {
	const listeners = new Set<() => void>();
	const doc = {
		hidden: false,
		addEventListener: (_type: string, fn: () => void) => void listeners.add(fn),
		removeEventListener: (_type: string, fn: () => void) => void listeners.delete(fn),
		listenerCount: () => listeners.size,
		/** Flip visibility and fire visibilitychange, as the browser does. */
		turn(hidden: boolean) {
			doc.hidden = hidden;
			for (const fn of [...listeners]) fn();
		}
	};
	return doc;
}

describe('the map regions poll while the house fails', () => {
	const B = REGIONS_POLL_MS;
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it('doubles the wait after each failed read, up to eight times the base', async () => {
		let up = true;
		const load = vi.fn(async () => {
			if (up) return changing;
			throw new Error('house down');
		});
		const poll = regionsPoll(load, sink());
		await poll.read();
		up = false;
		// The wait before each next read: 1x, then 2x, 4x, and capped at 8x from there on.
		for (const times of [1, 2, 4, 8, 8, 8]) {
			const before = load.mock.calls.length;
			await vi.advanceTimersByTimeAsync(times * B - 1);
			expect(load.mock.calls.length).toBe(before);
			await vi.advanceTimersByTimeAsync(1);
			expect(load.mock.calls.length).toBe(before + 1);
		}
		poll.stop();
	});

	it('one good read resets the wait to the base', async () => {
		let up = true;
		const load = vi.fn(async () => {
			if (up) return changing;
			throw new Error('house down');
		});
		const poll = regionsPoll(load, sink());
		await poll.read();
		up = false;
		await vi.advanceTimersByTimeAsync(B); // fail: wait becomes 2x
		await vi.advanceTimersByTimeAsync(2 * B); // fail: wait becomes 4x
		expect(load).toHaveBeenCalledTimes(3);
		up = true;
		await vi.advanceTimersByTimeAsync(4 * B); // the good read lands and resets the count
		expect(load).toHaveBeenCalledTimes(4);
		await vi.advanceTimersByTimeAsync(B - 1);
		expect(load).toHaveBeenCalledTimes(4);
		await vi.advanceTimersByTimeAsync(1);
		expect(load).toHaveBeenCalledTimes(5);
		poll.stop();
	});

	it('a timer that fired on a hidden page reads once when the page is visible again', async () => {
		const doc = fakeDocument();
		vi.stubGlobal('document', doc);
		const load = vi.fn(async () => changing);
		const poll = regionsPoll(load, sink());
		await poll.read();
		doc.turn(true);
		await vi.advanceTimersByTimeAsync(B); // the timer fires while hidden: no read
		await vi.advanceTimersByTimeAsync(10 * B);
		expect(load).toHaveBeenCalledTimes(1);
		doc.turn(false);
		await vi.advanceTimersByTimeAsync(0);
		expect(load).toHaveBeenCalledTimes(2);
		poll.stop();
	});

	it('the screen re-reading on the same visibility change does not double the read', async () => {
		const doc = fakeDocument();
		vi.stubGlobal('document', doc);
		const load = vi.fn(async () => changing);
		const poll = regionsPoll(load, sink());
		// The screen's own listener, registered beside the poll, reads on every visible change.
		doc.addEventListener('visibilitychange', () => {
			if (!doc.hidden) void poll.read();
		});
		await poll.read();
		doc.turn(true);
		await vi.advanceTimersByTimeAsync(B);
		doc.turn(false);
		await vi.advanceTimersByTimeAsync(0);
		expect(load).toHaveBeenCalledTimes(2);
		poll.stop();
	});

	it('stop() removes the visibility listener, so a later visible change reads nothing', async () => {
		const doc = fakeDocument();
		vi.stubGlobal('document', doc);
		const load = vi.fn(async () => changing);
		const poll = regionsPoll(load, sink());
		await poll.read();
		doc.turn(true);
		await vi.advanceTimersByTimeAsync(B);
		expect(doc.listenerCount()).toBe(1);
		poll.stop();
		expect(doc.listenerCount()).toBe(0);
		doc.turn(false);
		await vi.advanceTimersByTimeAsync(10 * B);
		expect(load).toHaveBeenCalledTimes(1);
	});
});
