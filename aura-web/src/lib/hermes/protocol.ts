// The HUD protocol, as types.
//
// Everything the console says to Hermes and everything Hermes says back goes
// through the jarvis-adapter's `/ws` socket as JSON-RPC 2.0 text frames, and
// this file is the shape of those frames — verified against v0.21.0 on
// 2026-09-02 and written down in `docs/hermes-hud-protocol.md`, which is the
// contract. Nothing here is behaviour: the client is `client.ts`, the state
// the HUD draws from is `hud.ts`.

export interface RpcRequest {
	jsonrpc: '2.0';
	id: number;
	method: string;
	params?: unknown;
}

export interface RpcErrorShape {
	code: number;
	message: string;
	data?: unknown;
}

/** @public — the response frame half of the documented HUD wire contract. */
export interface RpcResponse {
	jsonrpc: '2.0';
	id: number;
	result?: unknown;
	error?: RpcErrorShape;
}

/** A server → client notification: `{"method":"event","params":{type,session_id,seq,payload}}`. */
export interface HermesEvent {
	type: string;
	session_id?: string;
	seq?: number;
	payload?: Record<string, unknown>;
}

/**
 * A JSON-RPC error, as a thrown thing.
 *
 * The code is what a page decides on: `4018` is "not a plugin command" —
 * the `jgoal`/`subgoal` commands are not routed on every backend — and the
 * page shows the message rather than a stack trace.
 */
export class HermesRpcError extends Error {
	readonly code: number;
	readonly data: unknown;
	constructor(error: RpcErrorShape) {
		super(error.message);
		this.name = 'HermesRpcError';
		this.code = error.code;
		this.data = error.data;
	}
}

/** JSON-RPC's own "no such method". @public — part of the wire contract. */
export const METHOD_NOT_FOUND = -32601;
/** The one plugin command the console uses; its sub-commands ride in the arg. */
export const PLUGIN_COMMAND = 'jgoal';

/** The arg a plugin command wants: the session it should act on, then the words. */
export function pluginArg(sessionId: string, arg: string): string {
	return `@${sessionId} ${arg}`.trim();
}

/** Hermes: "not a quick/plugin/bundle/skill command" — the plugin is not installed. @public — wire contract. */
export const NOT_A_PLUGIN_COMMAND = 4018;
/** Hermes: the session id is not one this gateway knows. */
export const SESSION_NOT_FOUND = [4001, 4007] as const;
/** Hermes: `config.get`/`config.set` on a key it does not have. @public — wire contract. */
export const UNKNOWN_CONFIG_KEY = 4002;

export function isSessionNotFound(error: unknown): boolean {
	return (
		error instanceof HermesRpcError &&
		(SESSION_NOT_FOUND as readonly number[]).includes(error.code)
	);
}

// --- results -----------------------------------------------------------------

/** `session.create`: the live id (events carry it) and the stored one (the list shows it). */
export interface SessionCreated {
	session_id: string;
	stored_session_id?: string;
	message_count?: number;
	messages?: HistoryMessage[];
	info?: Partial<SessionInfo>;
}

/** One row of `session.list`. `id` is the STORED id — what `session.resume` takes. */
export interface SessionSummary {
	id: string;
	title: string;
	preview?: string;
	/** Epoch seconds. */
	started_at?: number;
	message_count?: number;
	source?: string;
}

export interface SessionList {
	sessions: SessionSummary[];
}

/** `session.resume`: the live id this socket now speaks for, and the transcript so far. */
export interface SessionResumed {
	session_id: string;
	resumed?: boolean;
	message_count?: number;
	messages?: HistoryMessage[];
	messages_omitted?: number;
	info?: Partial<SessionInfo>;
	running?: boolean;
}

/** `session.history`. */
export interface SessionHistory {
	count?: number;
	messages: HistoryMessage[];
}

/**
 * One stored message. A tool call is its own row (`role: "tool"`) with the
 * call's name and arguments; the reply that followed carries its reasoning.
 */
export interface HistoryMessage {
	role: 'user' | 'assistant' | 'tool' | 'system' | string;
	text?: string;
	/** Epoch seconds. */
	timestamp?: number;
	row_id?: number;
	name?: string;
	context?: string;
	args?: Record<string, unknown>;
	reasoning?: string;
	error?: string;
}

/** The `session.info` event's payload: what the session runs on. */
export interface SessionInfo {
	model: string;
	provider?: string;
	approval_mode?: string;
	running?: boolean;
	turn_started_at?: number | null;
	title?: string;
	stored_session_id?: string;
	tools?: Record<string, string[]>;
	skills?: Record<string, string[]>;
	usage?: Record<string, unknown>;
	version?: string;
}

/**
 * What `command.dispatch` answers, and what the client does with each:
 *
 * * `send` — show `notice`, then the client MUST `prompt.submit` `message`.
 *   This is how `/goal <text>` starts its first turn.
 * * `exec` — show `output`.
 * * `plugin` — a plugin command's reply (`jgoal`); show `output`.
 * * `prefill` — put `message` in the composer; do not send.
 * * `error` — show it.
 *
 * Plugin command handlers do not receive the session, so the apps prefix the
 * arg with `@<session_id> ` — `{name: "jgoal", arg: "@<sid> draft …"}`. The
 * built-in `goal` command needs no prefix.
 */
export type DispatchResult =
	| { type: 'send'; notice?: string; message: string }
	| { type: 'exec'; output?: string }
	| { type: 'plugin'; output?: string }
	| { type: 'prefill'; message: string }
	| { type: 'error'; message?: string; error?: string; output?: string };

/** What the page shows after a command: the client has already done the sending. */
export type DispatchOutcome =
	| { kind: 'sent'; notice: string; message: string }
	| { kind: 'output'; output: string }
	| { kind: 'prefill'; message: string }
	| { kind: 'error'; message: string; code?: number };

export type ApprovalChoice = 'once' | 'session' | 'always' | 'deny';
export const APPROVAL_CHOICES: ApprovalChoice[] = ['once', 'session', 'always', 'deny'];

// --- REST: `/api/status` -------------------------------------------------------

/** llama-swap's state for the configured model. `starting` is "warming up model". */
export type ModelState = 'ready' | 'starting' | 'stopping' | 'unloaded' | 'other-model' | 'unreachable' | string;

export interface StatusReport {
	ok: boolean;
	/**
	 * The one word every surface shows and why (T-0583, tests/contracts/status-words.json):
	 * decided by the adapter, printed here as `word: reason`, never derived from the rest of
	 * this report. Absent on an adapter older than the field.
	 */
	status?: { word?: string; reason?: string };
	/**
	 * The kill switch, read off the Hermes volume by the adapter rather than
	 * asked of Hermes: the HUD has to be able to show it while the gateway is
	 * wedged, which is the state the reactor's precedence puts `kill` above.
	 * Absent on an adapter older than the switch, which reads as released.
	 */
	kill_switch?: { engaged?: boolean; actor?: string; reason?: string; at?: string };
	/**
	 * Which persona is primary, read off the same volume, so a console that
	 * connects after a switch is tinted and voiced right from its first frame.
	 * Absent on an adapter older than personas. `theme` is the pack the persona
	 * wears (the contract's `themes`); absent on an adapter older than T-0861.
	 */
	persona?: { active?: string; theme?: string; since?: string; previous?: string };
	model?: {
		model?: string;
		state?: ModelState;
		warming_up?: boolean;
		loaded?: string[];
	};
	hermes?: {
		api_server?: { ok?: boolean; version?: string };
		backend?: { ok?: boolean; version?: string };
	};
	llama_swap?: { ok?: boolean; url?: string };
	latency_ms?: number;
}

/** One toolset from `GET /v1/toolsets`. */
export interface Toolset {
	name: string;
	label?: string;
	description?: string;
	enabled?: boolean;
	configured?: boolean;
	tools?: string[];
}

/** One skill from `GET /v1/skills`. */
export interface Skill {
	name: string;
	description?: string;
	category?: string;
}

/** One scheduled job from `GET /api/jobs`. */
export interface CronJob {
	id: string;
	name: string;
	prompt?: string;
	schedule?: { kind?: string; expr?: string; display?: string };
	schedule_display?: string;
	enabled?: boolean;
	state?: string;
	paused_at?: string | null;
	paused_reason?: string | null;
	next_run_at?: string | null;
	last_run_at?: string | null;
	last_status?: string | null;
	last_error?: string | null;
	model_snapshot?: string | null;
	deliver?: string | null;
}
