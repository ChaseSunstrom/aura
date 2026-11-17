import { describe, expect, it, vi } from 'vitest';
import { Feeds, type Sources } from './feeds.svelte';

// The host hands the feeds their links from an effect that re-runs on every Hermes event,
// because the link's snapshot is a new object each time. Reloading every active feed on each
// call was six house round trips per panel update in the 8-panel scene, and a re-render of
// every panel per answer: the hud-frametime regression since 3344eed8 (T-0324).

const conn = { client: { subscribeEvents: async () => ({ unsubscribe: async () => {} }) } } as unknown as NonNullable<Sources['conn']>;

describe('Feeds.setSources', () => {
	it('reloads the active feeds when a link comes up, not when the same links are handed back', () => {
		const feeds = new Feeds();
		const load = vi.spyOn(feeds.moments, 'load').mockResolvedValue();
		feeds.activate(['notifications']);
		expect(load).toHaveBeenCalledTimes(1);

		feeds.setSources({ conn, hermes: null });
		expect(load).toHaveBeenCalledTimes(2);

		feeds.setSources({ conn, hermes: null });
		feeds.setSources({ conn, hermes: null });
		expect(load).toHaveBeenCalledTimes(2);

		// Gone and back: the offline state, then a reload.
		const dropped = vi.spyOn(feeds.moments, 'dropped');
		feeds.setSources({ conn: null, hermes: null });
		expect(dropped).toHaveBeenCalledTimes(1);
		feeds.setSources({ conn, hermes: null });
		expect(load).toHaveBeenCalledTimes(3);
	});

	// The review of T-0324: Telemetry's readings, the sky, Jobs, automations and notes have no
	// bus event of their own; the per-event reload was what kept them fresh. A burst of events
	// is now one reload, REFRESH_MS after the first -- fresh within seconds, not per event.
	it('a burst of the same links is one trailing reload, so feeds with no event of their own stay fresh', () => {
		vi.useFakeTimers();
		try {
			const feeds = new Feeds();
			const load = vi.spyOn(feeds.moments, 'load').mockResolvedValue();
			feeds.activate(['notifications']);
			feeds.setSources({ conn, hermes: null });
			expect(load).toHaveBeenCalledTimes(2);
			for (let i = 0; i < 10; i++) feeds.setSources({ conn, hermes: null });
			expect(load).toHaveBeenCalledTimes(2);
			vi.advanceTimersByTime(Feeds.REFRESH_MS);
			expect(load).toHaveBeenCalledTimes(3);
			// Quiet: nothing more.
			vi.advanceTimersByTime(Feeds.REFRESH_MS * 3);
			expect(load).toHaveBeenCalledTimes(3);
		} finally {
			vi.useRealTimers();
		}
	});
});

// T-0746: the car page keeps the settings feed active, and every Hermes event's trailing reload
// re-read the whole settings list from both sides -- about 550 KB a minute of the car's
// ~566 KB. Settings now change only on an event (the house's `jarvis_setting_changed`, Hermes's
// `settings.changed`), so the timer leaves them alone.
describe('the trailing reload', () => {
	it('skips the settings feed, which reloads on its own change events', () => {
		vi.useFakeTimers();
		try {
			const feeds = new Feeds();
			const hermes = {} as NonNullable<Sources['hermes']>;
			const settings = vi.spyOn(feeds.settings, 'load').mockResolvedValue();
			const moments = vi.spyOn(feeds.moments, 'load').mockResolvedValue();
			feeds.activate(['settings', 'notifications']);
			feeds.setSources({ conn, hermes });
			feeds.settings.loaded = true;
			feeds.settings.status = 'ready';
			const first = settings.mock.calls.length;
			expect(first).toBeGreaterThan(0);
			for (let i = 0; i < 10; i++) feeds.setSources({ conn, hermes });
			vi.advanceTimersByTime(Feeds.REFRESH_MS * 3);
			expect(settings).toHaveBeenCalledTimes(first);
			expect(moments.mock.calls.length).toBeGreaterThan(1); // the others still refresh
			feeds.reload(feeds.settings);
			expect(settings).toHaveBeenCalledTimes(first + 1);
		} finally {
			vi.useRealTimers();
		}
	});

	it('still retries a settings load that failed, which no change event would reload', () => {
		vi.useFakeTimers();
		try {
			const feeds = new Feeds();
			const hermes = {} as NonNullable<Sources['hermes']>;
			const settings = vi.spyOn(feeds.settings, 'load').mockResolvedValue();
			feeds.activate(['settings']);
			feeds.setSources({ conn, hermes });
			feeds.settings.loaded = true;
			feeds.settings.status = 'error'; // the car page opened before the session attached
			const first = settings.mock.calls.length;
			feeds.setSources({ conn, hermes });
			vi.advanceTimersByTime(Feeds.REFRESH_MS * 2);
			expect(settings.mock.calls.length).toBeGreaterThan(first);
		} finally {
			vi.useRealTimers();
		}
	});
});
