import { afterEach, describe, expect, it, vi } from 'vitest';
import { Feed } from './feeds.svelte';

// A house command that never answered left the Telemetry, House and Integrations panels on
// their skeletons for good (T-0321). A feed now gives up after its deadline and is the error
// state, with the panel's Retry.

afterEach(() => vi.useRealTimers());

describe('Feed.load', () => {
	it('turns a load that never answers into the error state', async () => {
		vi.useFakeTimers();
		const feed = new Feed<string[]>('stuck', [], () => new Promise<string[]>(() => {}), (r) => r.length === 0, 'none');
		const loading = feed.load({ conn: null, hermes: null } as never);
		expect(feed.status).toBe('loading');
		await vi.advanceTimersByTimeAsync(5000);
		await loading;
		expect(feed.status).toBe('error');
		expect(feed.error).toMatch(/No answer in 5 s/);
	});

	it('takes an answer that comes after the deadline: the rows replace the error', async () => {
		// T-0830: the models list answered at 7 s on a loaded host; the feed said "no answer" at 5 s
		// and dropped what came, so the chat's model select never listed the models.
		vi.useFakeTimers();
		const feed = new Feed<string[]>('slow', [], () => new Promise<string[]>((r) => setTimeout(() => r(['a', 'b']), 7000)), (r) => r.length === 0, 'none');
		const loading = feed.load({ conn: null, hermes: null } as never);
		await vi.advanceTimersByTimeAsync(5000);
		await loading;
		expect(feed.status).toBe('error');
		await vi.advanceTimersByTimeAsync(2000);
		expect(feed.status).toBe('ready');
		expect(feed.data).toEqual(['a', 'b']);
		expect(feed.error).toBe('');
	});

	it('drops a late answer once a newer load has started', async () => {
		vi.useFakeTimers();
		let call = 0;
		const feed = new Feed<string[]>('slow', [], () => {
			const mine = ++call;
			return new Promise<string[]>((r) => setTimeout(() => r([`load ${mine}`]), mine === 1 ? 7000 : 100));
		}, (r) => r.length === 0, 'none');
		const first = feed.load({ conn: null, hermes: null } as never);
		await vi.advanceTimersByTimeAsync(5000);
		await first;
		const second = feed.load({ conn: null, hermes: null } as never);
		await vi.advanceTimersByTimeAsync(2100); // the second answers at 100 ms, the first's late one at 2000
		await second;
		expect(feed.data).toEqual(['load 2']);
	});

	it('still loads a feed that answers in time', async () => {
		const feed = new Feed<string[]>('quick', [], async () => ['a'], (r) => r.length === 0, 'none');
		await feed.load({ conn: null, hermes: null } as never);
		expect(feed.status).toBe('ready');
	});
});
