import { describe, it, expect } from 'vitest';
import {
	POLL_IDLE_MS,
	POLL_PENDING_MS,
	StatusPoller,
	backendOk,
	isWarmingUp,
	modelOf,
	pollInterval,
	statusLabel
} from './status';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// No try, no skip: a moved table fails this file (the phone and the desktop read the same one).
const WORDS = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../../tests/contracts/status-words.json', import.meta.url)), 'utf8')
) as {
	no_answer: { label: string };
	cases: { name: string; word: string; reason: string; label: string; model_state: string }[];
};

describe('the one status word (tests/contracts/status-words.json)', () => {
	for (const c of WORDS.cases) {
		it(`prints the adapter's field: ${c.name}`, () => {
			// The rest of the report says the opposite of the field: the label must not look at it.
			const report = { ok: c.word !== 'ready', status: { word: c.word, reason: c.reason }, model: { state: 'ready' as const } };
			expect(statusLabel(report)).toBe(c.label);
		});
	}

	it('reads degraded: model warming with the model down mid-start', () => {
		expect(statusLabel({ ok: true, status: { word: 'degraded', reason: 'model warming' } })).toBe('degraded: model warming');
	});

	it('says the table’s one fixed label when the adapter does not answer', () => {
		expect(statusLabel(null, 'fetch failed')).toBe(WORDS.no_answer.label);
		expect(statusLabel(null, null)).toBe('');
	});

	it('shows a word it does not know as it came', () => {
		expect(statusLabel({ ok: true, status: { word: 'draining', reason: '' } })).toBe('draining');
	});

	it('older adapter: no field, no label, so the present wording stays', () => {
		expect(statusLabel({ ok: true, model: { state: 'starting' } })).toBe('');
		expect(isWarmingUp({ ok: true, model: { state: 'starting' } })).toBe(true);
	});
});

describe('pollInterval', () => {
	it('is 5 s while a turn is pending with no token, 30 s otherwise', () => {
		expect(pollInterval(true, false)).toBe(POLL_PENDING_MS);
		expect(pollInterval(true, true)).toBe(POLL_IDLE_MS);
		expect(pollInterval(false, false)).toBe(POLL_IDLE_MS);
	});
});

describe('reading the report', () => {
	it('is warming up when llama-swap says starting, or the adapter says so', () => {
		expect(isWarmingUp({ ok: true, model: { state: 'starting' } })).toBe(true);
		expect(isWarmingUp({ ok: true, model: { state: 'ready', warming_up: true } })).toBe(true);
		expect(isWarmingUp({ ok: true, model: { state: 'ready', warming_up: false } })).toBe(false);
		expect(isWarmingUp(null)).toBe(false);
	});

	it('names the configured model, falling back to the loaded one', () => {
		expect(modelOf({ ok: true, model: { model: 'qwen3.8-27b', state: 'ready' } })).toBe('qwen3.8-27b');
		expect(modelOf({ ok: true, model: { loaded: ['other'] } })).toBe('other');
		expect(modelOf(null)).toBe('');
	});

	it('reads the backend’s health', () => {
		expect(backendOk({ ok: true, hermes: { backend: { ok: true } } })).toBe(true);
		expect(backendOk({ ok: true, hermes: { backend: { ok: false } } })).toBe(false);
		expect(backendOk(null)).toBe(false);
	});
});

function poller() {
	const timers: Array<{ fn: () => void; ms: number }> = [];
	const calls: string[] = [];
	let report: any = { ok: true, model: { model: 'm', state: 'ready', warming_up: false } };
	let status = 200;
	const p = new StatusPoller({
		url: '/hermes/api/status',
		fetchFn: (async (url: string) => {
			calls.push(url);
			return { ok: status === 200, status, json: async () => report } as Response;
		}) as typeof fetch,
		setTimeoutFn: ((fn: () => void, ms: number) => {
			timers.push({ fn, ms });
			return timers.length as any;
		}) as any,
		clearTimeoutFn: (() => {}) as any
	});
	return {
		p,
		timers,
		calls,
		setReport: (r: any) => (report = r),
		setStatus: (s: number) => (status = s)
	};
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('StatusPoller', () => {
	it('polls on start, tells listeners, and schedules the idle interval', async () => {
		const { p, timers, calls } = poller();
		const seen: any[] = [];
		p.subscribe((r) => seen.push(r));
		p.start();
		await flush();
		expect(calls).toEqual(['/hermes/api/status']);
		expect(seen[seen.length - 1]).toMatchObject({ ok: true });
		expect(p.warmingUp).toBe(false);
		expect(timers[0].ms).toBe(POLL_IDLE_MS);
	});

	it('polls at once and then every 5 s when a turn starts waiting for its first token', async () => {
		const { p, timers, calls, setReport } = poller();
		p.start();
		await flush();
		setReport({ ok: true, model: { model: 'm', state: 'starting' } });
		p.setPending(true, false);
		await flush();
		expect(calls).toHaveLength(2);
		expect(p.warmingUp).toBe(true);
		expect(timers[timers.length - 1].ms).toBe(POLL_PENDING_MS);
		// The first token arrives: back to the slow clock at the next tick.
		p.setPending(true, true);
		timers[timers.length - 1].fn();
		await flush();
		expect(timers[timers.length - 1].ms).toBe(POLL_IDLE_MS);
	});

	it('reports a failed fetch as an error and keeps polling', async () => {
		const { p, timers, setStatus } = poller();
		setStatus(502);
		let err: string | null = null;
		p.subscribe((_r, e) => (err = e));
		p.start();
		await flush();
		expect(err).toBe('status answered 502');
		expect(p.report).toBeNull();
		expect(timers).toHaveLength(1);
	});

	it('stop() cancels the clock and ignores an answer that lands afterwards', async () => {
		const { p, calls } = poller();
		p.start();
		p.stop();
		await flush();
		expect(calls).toHaveLength(1);
		expect(p.report).toBeNull();
	});
});

// T-0558: the console is the phone's home in a WebView; polling a hidden page woke the
// phone's radio every 30 s for a panel nobody could see (brief §2.1, §2.6).
describe('StatusPoller on a hidden page', () => {
	it('does not poll while hidden and polls once when the page is seen again', async () => {
		const timers: Array<() => void> = [];
		const calls: string[] = [];
		let hidden = false;
		let seen: (() => void) | null = null;
		const p = new StatusPoller({
			fetchFn: (async (url: string) => {
				calls.push(url);
				return { ok: true, status: 200, json: async () => ({ ok: true }) } as Response;
			}) as typeof fetch,
			setTimeoutFn: ((fn: () => void) => (timers.push(fn), timers.length)) as any,
			clearTimeoutFn: (() => {}) as any,
			visibility: { hidden: () => hidden, onVisible: (fn) => ((seen = fn), () => (seen = null)) }
		});
		p.start();
		await flush();
		expect(calls).toHaveLength(1);
		hidden = true;
		timers[timers.length - 1]();
		await flush();
		expect(calls).toHaveLength(1);
		const parked = timers.length;
		hidden = false;
		seen!();
		await flush();
		expect(calls).toHaveLength(2);
		expect(timers.length).toBe(parked + 1); // back on its clock
		p.stop();
		expect(seen).toBeNull();
	});
});
