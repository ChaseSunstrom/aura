// The Hermes socket: JSON-RPC 2.0 over WebSocket, kept up.
//
// The browser opens `ws(s)://<origin>/hermes/ws`; the console's server relays
// it to the adapter's `/ws` with the bearer key injected, so nothing here
// knows a credential. Frames are newline-free JSON text; the first frame after
// connect is the event `gateway.ready`.
//
// This is the one Hermes connection in the app, and it reconnects — with the
// schedule `consoleLink.ts` already uses, because two backoff policies in one
// app is one too many. Everything is injectable (the WebSocket class, the
// timers, the jitter) so the whole class runs in plain Node under Vitest.

import { OFFLINE_AFTER_ATTEMPTS, backoffDelay } from '$lib/consoleLink';
import {
	HermesRpcError,
	type DispatchOutcome,
	type DispatchResult,
	type HermesEvent,
	type RpcErrorShape,
	type RpcRequest
} from './protocol';

export type HermesStatus = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'offline';

/**
 * What this screen can show, sent as `params.surface` on every `prompt.submit`
 * (`tests/contracts/surface-capabilities.json`, the `web` row). The adapter
 * records it per session and strips it; the artifacts plugin puts one line in
 * the prompt from it and aims `artifact_send` by it. The registry row
 * verbatim: a client may only take capabilities away, never add them, so
 * sending less than the row would hide what this console can do.
 */
export const SURFACE = {
	name: 'web',
	images: true,
	video: true,
	audio: true,
	documents: true,
	interactive: true,
	downloads: true,
	notifications: true,
	max_bytes: 536870912
} as const;

/** The subset of a WebSocket this class uses, so a test can hand it a fake. */
export interface SocketLike {
	readyState: number;
	send(data: string): void;
	close(code?: number, reason?: string): void;
	onopen: ((ev: unknown) => void) | null;
	onclose: ((ev: { code?: number; reason?: string }) => void) | null;
	onerror: ((ev: unknown) => void) | null;
	onmessage: ((ev: { data: unknown }) => void) | null;
}

export type SocketFactory = (url: string) => SocketLike;

export interface HermesClientOptions {
	/** The socket URL, or a function that computes it at dial time. */
	url?: string | (() => string);
	/** How to open a socket. Defaults to the browser's `WebSocket`. */
	openSocket?: SocketFactory;
	setTimeoutFn?: typeof setTimeout;
	clearTimeoutFn?: typeof clearTimeout;
	/** Injected in tests to make the backoff deterministic. */
	random?: () => number;
}

/** The label the bar shows for a status. */
export function hermesStatusLabel(status: HermesStatus): string {
	switch (status) {
		case 'connected':
			return 'HERMES OK';
		case 'connecting':
			return 'LINKING';
		case 'reconnecting':
			return 'RECONNECTING';
		case 'offline':
			return 'OFFLINE';
		default:
			return 'IDLE';
	}
}

const OPEN = 1;

export function defaultHermesUrl(): string {
	const proto = location.protocol === 'https:' ? 'wss' : 'ws';
	return `${proto}://${location.host}/hermes/ws`;
}

interface Pending {
	resolve: (value: unknown) => void;
	reject: (error: Error) => void;
	method: string;
}

export class HermesClient {
	private socket: SocketLike | null = null;
	private nextId = 1;
	private pending = new Map<number, Pending>();
	private eventListeners = new Set<(event: HermesEvent) => void>();
	private statusListeners = new Set<(status: HermesStatus) => void>();
	private _status: HermesStatus = 'idle';
	private running = false;
	private attempts = 0;
	private everConnected = false;
	private retryTimer: ReturnType<typeof setTimeout> | null = null;

	private readonly urlOf: () => string;
	private readonly openSocket: SocketFactory;
	private readonly setTimeoutFn: typeof setTimeout;
	private readonly clearTimeoutFn: typeof clearTimeout;
	private readonly random: () => number;

	constructor(opts: HermesClientOptions = {}) {
		const url = opts.url;
		this.urlOf = typeof url === 'function' ? url : url ? () => url : defaultHermesUrl;
		this.openSocket =
			opts.openSocket ?? ((target) => new WebSocket(target) as unknown as SocketLike);
		// Bound: called as a method, the browser's setTimeout would see this
		// class as `this` and throw — see the same note in consoleLink.ts.
		this.setTimeoutFn = opts.setTimeoutFn ?? (setTimeout.bind(globalThis) as typeof setTimeout);
		this.clearTimeoutFn = opts.clearTimeoutFn ?? (clearTimeout.bind(globalThis) as typeof clearTimeout);
		this.random = opts.random ?? Math.random;
	}

	get status(): HermesStatus {
		return this._status;
	}

	get connected(): boolean {
		return this._status === 'connected' && this.socket?.readyState === OPEN;
	}

	onStatus(listener: (status: HermesStatus) => void): () => void {
		this.statusListeners.add(listener);
		listener(this._status);
		return () => this.statusListeners.delete(listener);
	}

	onEvent(listener: (event: HermesEvent) => void): () => void {
		this.eventListeners.add(listener);
		return () => this.eventListeners.delete(listener);
	}

	start(): void {
		if (this.running) return;
		this.running = true;
		this.attempts = 0;
		this.dial();
	}

	stop(): void {
		this.running = false;
		if (this.retryTimer !== null) this.clearTimeoutFn(this.retryTimer);
		this.retryTimer = null;
		const socket = this.socket;
		this.socket = null;
		if (socket) {
			socket.onclose = null;
			socket.onerror = null;
			socket.onmessage = null;
			try {
				socket.close();
			} catch {
				/* already gone */
			}
		}
		this.rejectAll('stopped');
		this.attempts = 0;
		this.everConnected = false;
		this.setStatus('idle');
	}

	/** RECONNECT NOW: skip the backoff and dial at once. */
	reconnectNow(): void {
		if (!this.running) {
			this.start();
			return;
		}
		if (this.retryTimer !== null) {
			this.clearTimeoutFn(this.retryTimer);
			this.retryTimer = null;
		}
		if (this.socket && this.socket.readyState === OPEN) return;
		this.dial();
	}

	/** Send a request; resolve with `result`, reject with `HermesRpcError` on `error`. */
	request<T = unknown>(method: string, params: unknown = {}): Promise<T> {
		return new Promise<T>((resolve, reject) => {
			const socket = this.socket;
			if (!socket || socket.readyState !== OPEN) {
				reject(new Error('no link to Hermes'));
				return;
			}
			const id = this.nextId++;
			const frame: RpcRequest = { jsonrpc: '2.0', id, method, params };
			this.pending.set(id, { resolve: resolve as (value: unknown) => void, reject, method });
			try {
				socket.send(JSON.stringify(frame));
			} catch (e) {
				this.pending.delete(id);
				reject(e instanceof Error ? e : new Error(String(e)));
			}
		});
	}

	/**
	 * A slash command without the slash, and what the result told us to do.
	 *
	 * `send` results are acted on HERE: the client calls `prompt.submit` with
	 * the message, as the TUI does, because that is how `/goal <text>` starts
	 * its first turn and a page that forgot would set goals that never ran.
	 */
	async dispatchCommand(sessionId: string, name: string, arg = ''): Promise<DispatchOutcome> {
		let result: DispatchResult;
		try {
			result = await this.request<DispatchResult>('command.dispatch', {
				session_id: sessionId,
				name,
				arg
			});
		} catch (e) {
			return {
				kind: 'error',
				message: e instanceof Error ? e.message : String(e),
				code: e instanceof HermesRpcError ? e.code : undefined
			};
		}
		return this.actOn(sessionId, result);
	}

	private async actOn(sessionId: string, result: DispatchResult): Promise<DispatchOutcome> {
		// Read loosely: the backend's shape is the contract, and a field it did
		// not send is an empty string here rather than a crash.
		const r = (result && typeof result === 'object' ? result : {}) as Record<string, unknown>;
		const type = r.type;
		if (type === 'send') {
			const message = String(r.message ?? '');
			const notice = String(r.notice ?? '');
			if (message) {
				try {
					await this.request('prompt.submit', { session_id: sessionId, text: message, surface: SURFACE });
				} catch (e) {
					return { kind: 'error', message: e instanceof Error ? e.message : String(e) };
				}
			}
			return { kind: 'sent', notice, message };
		}
		if (type === 'exec' || type === 'plugin') return { kind: 'output', output: String(r.output ?? '') };
		if (type === 'prefill') return { kind: 'prefill', message: String(r.message ?? '') };
		return {
			kind: 'error',
			message: String(r.message ?? r.error ?? r.output ?? 'command failed')
		};
	}

	// --- the socket -------------------------------------------------------------

	private setStatus(status: HermesStatus): void {
		if (this._status === status) return;
		this._status = status;
		for (const listener of this.statusListeners) listener(status);
	}

	private dial(): void {
		if (!this.running) return;
		this.setStatus(this.everConnected || this.attempts > 0 ? 'reconnecting' : 'connecting');
		let socket: SocketLike;
		try {
			socket = this.openSocket(this.urlOf());
		} catch {
			this.onLost();
			return;
		}
		this.socket = socket;
		socket.onopen = () => {
			if (this.socket !== socket) return;
			this.attempts = 0;
			this.everConnected = true;
			this.setStatus('connected');
		};
		socket.onmessage = (ev) => {
			if (this.socket !== socket) return;
			if (typeof ev.data !== 'string') return; // binary frames are not ours
			this.handleFrame(ev.data);
		};
		socket.onerror = () => {
			// The close that follows carries the state change.
		};
		socket.onclose = () => {
			if (this.socket !== socket) return;
			this.socket = null;
			this.onLost();
		};
	}

	private handleFrame(text: string): void {
		let frame: any;
		try {
			frame = JSON.parse(text);
		} catch {
			return;
		}
		if (!frame || typeof frame !== 'object') return;
		if (frame.method === 'event' && frame.params && typeof frame.params === 'object') {
			const event = frame.params as HermesEvent;
			for (const listener of this.eventListeners) listener(event);
			return;
		}
		if (typeof frame.id === 'number' && this.pending.has(frame.id)) {
			const entry = this.pending.get(frame.id)!;
			this.pending.delete(frame.id);
			if (frame.error) entry.reject(new HermesRpcError(frame.error as RpcErrorShape));
			else entry.resolve(frame.result);
		}
	}

	private rejectAll(reason: string): void {
		const entries = [...this.pending.values()];
		this.pending.clear();
		for (const entry of entries) entry.reject(new Error(`${reason} (${entry.method})`));
	}

	private onLost(): void {
		this.rejectAll('link closed');
		if (!this.running) return;
		if (this.retryTimer !== null) return;
		const attempt = this.attempts;
		this.attempts += 1;
		this.setStatus(this.attempts >= OFFLINE_AFTER_ATTEMPTS ? 'offline' : 'reconnecting');
		const delay = backoffDelay(attempt, this.random());
		this.retryTimer = this.setTimeoutFn(() => {
			this.retryTimer = null;
			this.dial();
		}, delay);
		(this.retryTimer as any)?.unref?.();
	}
}
