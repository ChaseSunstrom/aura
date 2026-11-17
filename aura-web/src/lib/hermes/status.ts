// `GET /api/status`, on a clock that knows what the page is waiting for.
//
// llama-swap loads the model on first use, and a cold start takes up to about
// three minutes; during it a turn sits with no token and the page would read
// as broken. The adapter's status says `model.state == "starting"`, and the
// contract is: poll every 5 s while a turn is pending and no token has come,
// every 30 s otherwise. That schedule is `pollInterval`, pure; the poller
// wraps it with injectable fetch and timers so it runs under Vitest. A hidden
// page does not poll at all; it polls once when it is seen again (T-0558).

import type { StatusReport } from './protocol';

export const POLL_PENDING_MS = 5_000;
export const POLL_IDLE_MS = 30_000;

/** How long to wait before the next poll. */
export function pollInterval(turnPending: boolean, gotToken: boolean): number {
	return turnPending && !gotToken ? POLL_PENDING_MS : POLL_IDLE_MS;
}

/** "warming up model": llama-swap is loading it and the first token is minutes off. */
export function isWarmingUp(report: StatusReport | null | undefined): boolean {
	if (!report?.model) return false;
	return report.model.state === 'starting' || report.model.warming_up === true;
}

/** The model the adapter is configured for, or the one loaded, or nothing. */
export function modelOf(report: StatusReport | null | undefined): string {
	return report?.model?.model || report?.model?.loaded?.[0] || '';
}

/** Whether the backend behind the adapter is answering. */
export function backendOk(report: StatusReport | null | undefined): boolean {
	if (!report) return false;
	return report.ok !== false && report.hermes?.backend?.ok !== false;
}

/** What a surface shows when the adapter does not answer at all: the table's `no_answer`. */
export const NO_ANSWER_LABEL = 'down: no answer';

/**
 * The adapter's own word and reason as `word: reason` (T-0583). The adapter decides; this only
 * prints. '' from an adapter older than the field, or before the first poll, so the caller keeps
 * its present wording. A failed poll is the one label written here: nothing answered to word it.
 */
export function statusLabel(report: StatusReport | null | undefined, error?: string | null): string {
	if (!report) return error ? NO_ANSWER_LABEL : '';
	const word = report.status?.word ?? '';
	const reason = report.status?.reason ?? '';
	return word && reason ? `${word}: ${reason}` : word;
}

/** Whether anyone can see the page, and a hook for when they can again. */
export interface PageVisibility {
	hidden(): boolean;
	/** Calls `fn` each time the page becomes visible; returns the unsubscribe. */
	onVisible(fn: () => void): () => void;
}

/** The document's own: always visible where there is no document (Vitest, the server). */
export const documentVisibility: PageVisibility = {
	hidden: () => typeof document !== 'undefined' && document.visibilityState === 'hidden',
	onVisible: (fn) => {
		if (typeof document === 'undefined') return () => {};
		const seen = () => document.visibilityState === 'visible' && fn();
		document.addEventListener('visibilitychange', seen);
		return () => document.removeEventListener('visibilitychange', seen);
	}
};

export interface StatusPollerOptions {
	url?: string;
	fetchFn?: typeof fetch;
	setTimeoutFn?: typeof setTimeout;
	clearTimeoutFn?: typeof clearTimeout;
	visibility?: PageVisibility;
}

export type StatusListener = (report: StatusReport | null, error: string | null) => void;

export class StatusPoller {
	private timer: ReturnType<typeof setTimeout> | null = null;
	private running = false;
	private pending = false;
	private gotToken = false;
	private listeners = new Set<StatusListener>();
	private _report: StatusReport | null = null;
	private _error: string | null = null;
	private generation = 0;

	private readonly url: string;
	private readonly fetchFn: typeof fetch;
	private readonly setTimeoutFn: typeof setTimeout;
	private readonly clearTimeoutFn: typeof clearTimeout;
	private readonly visibility: PageVisibility;
	/** Set while a hidden page skipped its poll; the next sight of the page polls at once. */
	private parked = false;
	private unwatch: (() => void) | null = null;

	constructor(opts: StatusPollerOptions = {}) {
		this.url = opts.url ?? '/hermes/api/status';
		this.fetchFn = opts.fetchFn ?? ((input, init) => fetch(input, init));
		this.setTimeoutFn = opts.setTimeoutFn ?? (setTimeout.bind(globalThis) as typeof setTimeout);
		this.clearTimeoutFn = opts.clearTimeoutFn ?? (clearTimeout.bind(globalThis) as typeof clearTimeout);
		this.visibility = opts.visibility ?? documentVisibility;
	}

	get report(): StatusReport | null {
		return this._report;
	}

	get error(): string | null {
		return this._error;
	}

	get warmingUp(): boolean {
		return isWarmingUp(this._report);
	}

	subscribe(listener: StatusListener): () => void {
		this.listeners.add(listener);
		listener(this._report, this._error);
		return () => this.listeners.delete(listener);
	}

	start(): void {
		if (this.running) return;
		this.running = true;
		this.unwatch = this.visibility.onVisible(() => {
			if (!this.running || !this.parked) return;
			this.parked = false;
			void this.poll();
		});
		void this.poll();
	}

	stop(): void {
		this.running = false;
		this.parked = false;
		this.unwatch?.();
		this.unwatch = null;
		this.generation += 1;
		if (this.timer !== null) this.clearTimeoutFn(this.timer);
		this.timer = null;
	}

	/**
	 * What the page is waiting for. Going from idle to pending polls at once —
	 * the 30 s clock would otherwise leave "warming up" unsaid for the whole
	 * window a person is staring at the reactor.
	 */
	setPending(pending: boolean, gotToken: boolean): void {
		const wasFast = this.pending && !this.gotToken;
		this.pending = pending;
		this.gotToken = gotToken;
		const nowFast = pending && !gotToken;
		if (nowFast && !wasFast && this.running) {
			if (this.timer !== null) this.clearTimeoutFn(this.timer);
			this.timer = null;
			void this.poll();
		}
	}

	/** Poll now, whatever the clock says. */
	refresh(): Promise<void> {
		if (this.timer !== null) this.clearTimeoutFn(this.timer);
		this.timer = null;
		return this.poll();
	}

	private async poll(): Promise<void> {
		const mine = ++this.generation;
		try {
			const res = await this.fetchFn(this.url, { headers: { accept: 'application/json' } });
			if (mine !== this.generation) return;
			if (!res.ok) {
				this._error = `status answered ${res.status}`;
				this._report = null;
			} else {
				this._report = (await res.json()) as StatusReport;
				this._error = null;
			}
		} catch (e) {
			if (mine !== this.generation) return;
			this._error = e instanceof Error ? e.message : String(e);
			this._report = null;
		}
		for (const listener of this.listeners) listener(this._report, this._error);
		this.schedule();
	}

	private schedule(): void {
		if (!this.running) return;
		if (this.timer !== null) this.clearTimeoutFn(this.timer);
		this.timer = this.setTimeoutFn(() => {
			this.timer = null;
			// A hidden page (the phone's WebView in a pocket) waits for sight instead of polling.
			if (this.visibility.hidden()) this.parked = true;
			else void this.poll();
		}, pollInterval(this.pending, this.gotToken));
		(this.timer as any)?.unref?.();
	}
}
