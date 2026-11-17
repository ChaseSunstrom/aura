// What the HUD draws, as data — and the one function that changes it.
//
// Every event off the Hermes socket goes through `reduce(state, event)` and
// nothing else touches the state. The component holds the result in `$state`
// and reassigns it, which is what makes Svelte 5 redraw and what makes this
// file testable in plain Node: a whole scripted turn is a list of events and
// an expected final state, with no socket and no DOM.
//
// The transcript reuses `ChatMessage` from `$lib/chat` — the chat surface and
// its message component already know how to draw reasoning collapsed and tool
// rows inline, and a second shape for the same thing would be two components
// that drift.

import { assistantPlaceholder, userMessage, type ChatMessage, type ChatToolCall } from '$lib/chat';
import { projectionFromEvent, type ProjectionFrame } from '$lib/ds/gen/contract';
import { validPack, type Pack } from '$lib/ds/pack';
import { reduceActivity, stopActivity, type ActivityState } from './activity';
import { isPluginRuleLabel, reasonOfApproval, toolOfApproval } from './approvalEvent';
import { NO_GOAL, parseGoalText, type GoalState } from './goal';
import {
	applyDismiss,
	applyShow,
	applyUpdate,
	panelFromShow,
	pruneExpired,
	seedFromList,
	type Panel
} from './panels';
import {
	APPROVAL_CHOICES,
	type ApprovalChoice,
	type HermesEvent,
	type HistoryMessage,
	type SessionInfo
} from './protocol';

export type ToolCardState = 'running' | 'done' | 'failed';

/** One tool call, keyed by Hermes's `tool_id`, from `tool.start` to `tool.complete`. */
export interface ToolCard {
	id: string;
	name: string;
	context: string;
	args: Record<string, unknown>;
	state: ToolCardState;
	/** The latest `tool.progress` line. */
	progress: string;
	/** Seconds, as Hermes reports it on `tool.complete`. */
	duration: number | null;
	error: string | null;
	result: string | null;
	startedAt: number;
	finishedAt: number | null;
}

export interface ApprovalRequest {
	requestId: string;
	sessionId: string;
	tool: string;
	/** The command or the arguments, as one line for the card. */
	detail: string;
	reason: string;
	/** The choices the backend allows; a smart-denied request offers only once/deny. */
	choices: ApprovalChoice[];
	/**
	 * A sensitive ask (T-0945): only the phone's fingerprint says yes. The adapter
	 * refuses an allow from here, so the card offers the no and says where the yes is.
	 */
	onPhone?: boolean;
	at: number;
}

export interface ClarifyRequest {
	requestId: string;
	sessionId: string;
	question: string;
	options: string[];
	at: number;
	/**
	 * One question of a batch (`{questions: [{qid, …}]}`): the gateway wants
	 * each answered by its `question_id`, and the request resolves once every
	 * one is. Absent for the single-question form.
	 */
	questionId?: string;
}

/** A background process or a delegated subagent: "running in the background" rows. */
export interface BackgroundRow {
	id: string;
	kind: 'process' | 'subagent';
	label: string;
	state: 'running' | 'done' | 'failed' | 'interrupted';
	detail: string;
	at: number;
	/** The subagent that delegated this one. Absent for JARVIS' own children. */
	parent?: string;
	/** Hermes' `depth`: 0 is JARVIS' own child, 1 that child's child. */
	depth?: number;
	/** Tool calls the subagent has made so far. */
	tools?: number;
	/** The child's own Hermes session, for a surface that wants to open it. */
	session?: string;
	/** What it reported when it finished. */
	summary?: string;
}

export interface TurnState {
	/** A turn is in flight: from `prompt.submit` (or `message.start`) to `message.complete`. */
	running: boolean;
	/** The first `message.delta` has arrived — the model is awake. */
	gotToken: boolean;
	startedAt: number | null;
	firstTokenAt: number | null;
	finishedAt: number | null;
	/** The reply so far, or the whole reply once complete. */
	text: string;
	/** When the reply last streamed: a trailing thinking step ends there (`ds/activity`). */
	textAt?: number | null;
	reasoning: string;
	/** The latest non-goal `status.update`, or the thinking indicator. */
	status: string;
	/** Tool calls this turn, in the order they started. */
	tools: ToolCard[];
	/**
	 * The `hud.projection` frames this turn drew (a chart, a map, a table), oldest first, one per
	 * id. Kept past `message.complete` so the answer scene shows them while it speaks; the next
	 * turn clears them. Does not cover pinning one past its turn.
	 */
	visuals: ProjectionFrame[];
	/** When the newest of them arrived or was redrawn (`visualsHeld`); null with none. */
	visualsAt: number | null;
	/** Why the turn ended, when it did not end well. */
	error: string | null;
}

export interface HudState {
	/** The live session id events carry; null until `session.create`/`session.resume`. */
	sessionId: string | null;
	/** The stored id `session.list` shows and `session.resume` takes. */
	storedSessionId: string | null;
	title: string;
	model: string;
	provider: string;
	approvalMode: string;
	usage: Record<string, unknown> | null;
	turn: TurnState;
	messages: ChatMessage[];
	approvals: ApprovalRequest[];
	clarifies: ClarifyRequest[];
	goal: GoalState;
	background: BackgroundRow[];
	/** The typed cards JARVIS has pulled up, driven by `hud.panel.*` events. */
	panels: Panel[];
	/** What the browser or the desktop is doing (`activity.*`); null when nothing is. */
	activity: ActivityState | null;
	/** The adapter said it cannot reach Hermes: the offline screen. */
	backendDown: boolean;
	/** The last `error` event's message; cleared on the next turn. */
	error: string | null;
	lastSeq: number;
	/**
	 * Who is primary (ADR-0043): one of the four, lower-case, as
	 * `persona.switch` carries it. Empty until an event says -- the status
	 * report (`GET /api/status`, off the same file) answers for a console that
	 * connected after the switch, and JARVIS answers for both being silent.
	 * The voice, the orb's hue and the companion pool follow it;
	 * `$lib/orb/personas` turns the name into the rest.
	 */
	persona: string;
	/**
	 * The theme pack the switch named (`persona.switch` `theme`), lower-case;
	 * empty when the event named none. `$lib/orb/personas` `themeFrom` resolves it.
	 */
	theme: string;
	/**
	 * A drafted pack shown for a look before anyone wears it (`theme.preview`, T-0870);
	 * cleared when it is applied. Only a pack `validPack` admits gets here.
	 */
	themePreview: { id: string; name: string; pack: Pack } | null;
	/**
	 * The last `theme.apply`: `pack` for a draft, null for a shipped pack (or a draft
	 * named without one: the console fetches `/api/themes/<id>`). `theme` names it too.
	 */
	themeApplied: { id: string; pack: Pack | null } | null;
}

export function freshTurn(): TurnState {
	return {
		running: false,
		gotToken: false,
		startedAt: null,
		firstTokenAt: null,
		finishedAt: null,
		text: '',
		reasoning: '',
		status: '',
		tools: [],
		visuals: [],
		visualsAt: null,
		error: null
	};
}

/** The stage has three slots beside the steps; more frames than this would only pile up. */
const VISUALS_KEPT = 4;

/**
 * The `message.complete` statuses that end a turn without an error
 * (`tests/contracts/reactor.json` `derivation.quiet_ends`). `interrupted` is
 * one: a stop the person asked for -- Cancel, Stop, a barge-in -- is not a
 * failure, and counting it as one put the reactor at alert on every interrupt.
 */
export const QUIET_ENDS: readonly string[] = ['complete', 'completed', 'ok', 'interrupted'];

export function initialHud(): HudState {
	return {
		sessionId: null,
		storedSessionId: null,
		title: '',
		model: '',
		provider: '',
		approvalMode: '',
		usage: null,
		turn: freshTurn(),
		messages: [],
		approvals: [],
		clarifies: [],
		goal: NO_GOAL,
		background: [],
		panels: [],
		activity: null,
		backendDown: false,
		error: null,
		lastSeq: 0,
		persona: '',
		theme: '',
		themePreview: null,
		themeApplied: null
	};
}

// --- helpers over the transcript ----------------------------------------------

function pendingIndex(messages: ChatMessage[]): number {
	for (let i = messages.length - 1; i >= 0; i--) {
		if (messages[i].role === 'assistant' && messages[i].pending) return i;
	}
	return -1;
}

function patchPending(
	messages: ChatMessage[],
	change: (message: ChatMessage) => ChatMessage
): ChatMessage[] {
	const index = pendingIndex(messages);
	if (index === -1) return messages;
	const next = messages.slice();
	next[index] = change(next[index]);
	return next;
}

/** The placeholder a turn streams into, added when one is not already there. */
function ensurePending(messages: ChatMessage[], at: number, persona = ''): ChatMessage[] {
	return pendingIndex(messages) === -1 ? [...messages, { ...assistantPlaceholder(at), persona }] : messages;
}

function str(value: unknown): string {
	if (value === null || value === undefined) return '';
	return typeof value === 'string' ? value : typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function num(value: unknown): number | null {
	const n = typeof value === 'number' ? value : Number(value);
	return Number.isFinite(n) ? n : null;
}

/** A tool's arguments as one line, for a card or a row. */
export function summariseToolArgs(args: Record<string, unknown> | undefined, max = 3): string {
	const parts: string[] = [];
	for (const [key, value] of Object.entries(args ?? {})) {
		if (value === null || value === undefined || value === '') continue;
		const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
		parts.push(`${key}: ${text.length > 60 ? `${text.slice(0, 59)}…` : text}`);
		if (parts.length >= max) break;
	}
	return parts.join(' · ');
}

/**
 * A tool's arguments in full, for a row's hover title — every key, values not
 * capped per-field the way `summariseToolArgs` caps them for the visible line,
 * so the whole command is there when a person hovers the truncated row. Bounded
 * only so a pathological argument cannot make a title megabytes long.
 */
export function toolArgsFull(args: Record<string, unknown> | undefined, max = 800): string {
	const parts: string[] = [];
	for (const [key, value] of Object.entries(args ?? {})) {
		if (value === null || value === undefined || value === '') continue;
		parts.push(`${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`);
	}
	const line = parts.join(' · ');
	return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/**
 * A streamed reply, shaped for display: trailing spaces and whitespace-only
 * lines drop out, and a run of blank lines collapses to a single paragraph
 * break. Consecutive deltas then read as one flowing block and the segments a
 * turn streams between tool calls are never separated by several blank lines of
 * empty space. The raw `turn.text` is kept untouched for the spoken reply and
 * the transcript; this is only what the eye reads, and Markdown renders it.
 */
export function shapeReply(text: string): string {
	return String(text ?? '')
		.replace(/\r\n?/g, '\n')
		.replace(/[ \t]+\n/g, '\n')
		.replace(/\n{3,}/g, '\n\n')
		.replace(/^\s+|\s+$/g, '');
}

function toolAsChatCall(card: ToolCard): ChatToolCall {
	return {
		key: card.id,
		name: card.name,
		arguments: card.args,
		state: card.state === 'running' ? 'running' : card.state === 'failed' ? 'failed' : 'ok',
		error: card.error,
		durationMs: card.duration !== null ? Math.round(card.duration * 1000) : undefined
	};
}

function withToolCard(messages: ChatMessage[], card: ToolCard): ChatMessage[] {
	const call = toolAsChatCall(card);
	return patchPending(messages, (m) => ({
		...m,
		tools: m.tools.some((t) => t.key === call.key)
			? m.tools.map((t) => (t.key === call.key ? call : t))
			: [...m.tools, call]
	}));
}

// --- the turn's beginning, from the page's side --------------------------------

/**
 * A turn the person started: their words into the transcript, a placeholder
 * for the reply, the turn marked running. Called before `prompt.submit` so
 * the nine seconds a turn can spend in tool calls have somewhere to land.
 */
export function beginTurn(state: HudState, text: string, at = Date.now()): HudState {
	const messages = state.messages.slice();
	const pending = pendingIndex(messages);
	const user = userMessage(text, at);
	if (pending === -1) {
		messages.push(user, { ...assistantPlaceholder(at), persona: state.persona });
	} else {
		// `message.start` beat us to it (a `/goal` whose first turn the client
		// submitted): the question goes in front of the placeholder it feeds.
		messages.splice(pending, 0, user);
	}
	return {
		...state,
		error: null,
		turn: { ...freshTurn(), running: true, startedAt: at },
		messages
	};
}

/** A turn that failed before the socket answered: say so on the message. */
export function failTurn(state: HudState, message: string, at = Date.now()): HudState {
	const patched = patchPending(state.messages, (m) => ({ ...m, pending: false, error: message }));
	return {
		...state,
		error: message,
		turn: { ...state.turn, running: false, finishedAt: at, error: message },
		messages:
			patched !== state.messages
				? patched
				: [...state.messages, { ...assistantPlaceholder(at), pending: false, error: message }]
	};
}

/** The transcript of a resumed session, from `session.resume` / `session.history`. */
export function fromHistory(
	rows: HistoryMessage[] | undefined,
	sessionId: string,
	at = Date.now()
): ChatMessage[] {
	const messages: ChatMessage[] = [];
	let pendingTools: ChatToolCall[] = [];
	let n = 0;
	for (const row of rows ?? []) {
		n += 1;
		const stamp = row.timestamp ? row.timestamp * 1000 : at;
		if (row.role === 'user') {
			messages.push({
				id: `${sessionId}-${n}`,
				role: 'user',
				content: str(row.text),
				thinking: '',
				tools: [],
				at: stamp,
				pending: false
			});
		} else if (row.role === 'tool') {
			pendingTools.push({
				key: `${sessionId}-${n}`,
				name: str(row.name) || 'tool',
				arguments: (row.args ?? {}) as Record<string, unknown>,
				state: row.error ? 'failed' : 'ok',
				error: row.error ?? null
			});
		} else if (row.role === 'assistant') {
			messages.push({
				id: `${sessionId}-${n}`,
				role: 'assistant',
				content: str(row.text),
				thinking: str(row.reasoning),
				tools: pendingTools,
				at: stamp,
				pending: false
			});
			pendingTools = [];
		}
	}
	if (pendingTools.length) {
		// Calls with no reply after them — the session was interrupted mid-turn.
		messages.push({
			id: `${sessionId}-${n + 1}`,
			role: 'assistant',
			content: '',
			thinking: '',
			tools: pendingTools,
			at,
			pending: false
		});
	}
	return messages;
}

/** Bind the state to a session: its ids, its transcript, what it runs on. */
export function attachSession(
	state: HudState,
	session: {
		sessionId: string;
		storedSessionId?: string | null;
		messages?: HistoryMessage[];
		info?: Partial<SessionInfo> | null;
		running?: boolean;
	},
	at = Date.now()
): HudState {
	const info = session.info ?? {};
	return {
		...initialHud(),
		backendDown: state.backendDown,
		goal: NO_GOAL,
		sessionId: session.sessionId,
		storedSessionId: session.storedSessionId ?? info.stored_session_id ?? null,
		model: str(info.model) || state.model,
		provider: str(info.provider) || state.provider,
		approvalMode: str(info.approval_mode) || state.approvalMode,
		title: str(info.title),
		messages: fromHistory(session.messages, session.sessionId, at),
		turn: session.running ? { ...freshTurn(), running: true, startedAt: at } : freshTurn()
	};
}

/** Forget the session: a `new conversation`. */
export function detachSession(state: HudState): HudState {
	return {
		...initialHud(),
		backendDown: state.backendDown,
		model: state.model,
		provider: state.provider,
		approvalMode: state.approvalMode
	};
}

// --- the reducer ---------------------------------------------------------------

function isOurs(state: HudState, event: HermesEvent): boolean {
	// Session-less events (gateway.ready, adapter.error, sessions.changed) are
	// for everybody; a session's events are only for the session on screen.
	// Before one is attached nothing is on screen, and a turn another client
	// is running must not stream into a page that has not said a word.
	if (!event.session_id) return true;
	return state.sessionId !== null && event.session_id === state.sessionId;
}

export function reduce(state: HudState, event: HermesEvent, now = Date.now()): HudState {
	if (!event || typeof event.type !== 'string') return state;
	const p = event.payload ?? {};
	const seq = typeof event.seq === 'number' ? event.seq : state.lastSeq;

	// The adapter's "backend unavailable" goes with the outage it reported: left in `error` it
	// read as an error event the moment the backend answered, and a console open across a
	// restart of the assistant sat at alert until the next turn (T-1483). Only then: with no
	// outage to end, `error` is an `error` event's and stays until a turn clears it.
	if (event.type === 'gateway.ready') return state.backendDown ? { ...state, backendDown: false, error: null } : state;
	if (event.type === 'adapter.error') {
		return { ...state, backendDown: true, error: str(p.error) || 'backend unavailable' };
	}
	if (!isOurs(state, event)) return state;

	const next = (patch: Partial<HudState>): HudState => ({ ...state, ...patch, lastSeq: seq });
	const turn = (patch: Partial<TurnState>): HudState => next({ turn: { ...state.turn, ...patch } });

	// `activity.*`: the browser/desktop tile, folded by its own reducer (`activity.ts`).
	if (event.type.startsWith('activity.')) return next({ activity: reduceActivity(state.activity, event, now) });

	switch (event.type) {
		case 'session.info': {
			const info = p as Partial<SessionInfo>;
			return next({
				model: str(info.model) || state.model,
				provider: str(info.provider) || state.provider,
				approvalMode: str(info.approval_mode) || state.approvalMode,
				title: str(info.title) || state.title,
				storedSessionId: str(info.stored_session_id) || state.storedSessionId,
				usage: (info.usage as Record<string, unknown>) ?? state.usage,
				// A resumed session that is mid-turn says so here.
				turn:
					info.running && !state.turn.running
						? { ...freshTurn(), running: true, startedAt: now }
						: state.turn
			});
		}
		case 'session.title':
			return next({ title: str(p.title) || state.title });
		case 'session.usage':
			return next({ usage: p as Record<string, unknown> });

		case 'message.start':
			return next({
				error: null,
				turn: state.turn.running ? state.turn : { ...freshTurn(), running: true, startedAt: now },
				messages: ensurePending(state.messages, now, state.persona)
			});

		case 'message.delta': {
			const delta = str(p.text);
			if (!delta) return state;
			return next({
				turn: {
					...state.turn,
					running: true,
					gotToken: true,
					firstTokenAt: state.turn.firstTokenAt ?? now,
					text: state.turn.text + delta,
					textAt: now
				},
				messages: patchPending(ensurePending(state.messages, now, state.persona), (m) => ({
					...m,
					content: m.content + delta
				}))
			});
		}

		case 'message.interim': {
			// A mid-turn note the model wants shown; skipped when it already streamed.
			if (p.already_streamed) return state;
			const note = str(p.text);
			if (!note) return state;
			const joined = state.turn.text ? `${state.turn.text}\n${note}` : note;
			return next({
				turn: { ...state.turn, gotToken: true, firstTokenAt: state.turn.firstTokenAt ?? now, text: joined },
				messages: patchPending(ensurePending(state.messages, now, state.persona), (m) => ({
					...m,
					content: m.content ? `${m.content}\n${note}` : note
				}))
			});
		}

		case 'reasoning.delta': {
			const delta = str(p.text);
			if (!delta) return state;
			return next({
				turn: { ...state.turn, reasoning: state.turn.reasoning + delta },
				messages: patchPending(ensurePending(state.messages, now, state.persona), (m) => ({
					...m,
					thinking: m.thinking + delta
				}))
			});
		}

		case 'reasoning.available': {
			// The whole reasoning at once — only worth taking when nothing streamed.
			const text = str(p.text);
			if (!text || state.turn.reasoning) return state;
			return next({
				turn: { ...state.turn, reasoning: text },
				messages: patchPending(state.messages, (m) => (m.thinking ? m : { ...m, thinking: text }))
			});
		}

		case 'thinking.delta':
			// The TUI's spinner caption. It is a status, not reasoning.
			return turn({ status: str(p.text) });

		case 'tool.start': {
			const id = str(p.tool_id) || `${event.type}-${seq}`;
			const card: ToolCard = {
				id,
				name: str(p.name) || 'tool',
				context: str(p.context),
				args: (p.args as Record<string, unknown>) ?? {},
				state: 'running',
				progress: '',
				duration: null,
				error: null,
				result: null,
				startedAt: now,
				finishedAt: null
			};
			const tools = state.turn.tools.some((t) => t.id === id)
				? state.turn.tools.map((t) => (t.id === id ? card : t))
				: [...state.turn.tools, card];
			return next({
				turn: { ...state.turn, running: true, tools },
				messages: withToolCard(ensurePending(state.messages, now, state.persona), card)
			});
		}

		case 'tool.progress': {
			const id = str(p.tool_id);
			const line = str(p.text ?? p.message ?? p.progress ?? p.line);
			return turn({
				tools: state.turn.tools.map((t) => (t.id === id ? { ...t, progress: line || t.progress } : t))
			});
		}

		case 'tool.complete': {
			const id = str(p.tool_id) || `${event.type}-${seq}`;
			const error = p.error === undefined || p.error === null || p.error === '' ? null : str(p.error);
			const known = state.turn.tools.find((t) => t.id === id);
			const card: ToolCard = {
				id,
				name: str(p.name) || known?.name || 'tool',
				context: str(p.context) || known?.context || '',
				args: (p.args as Record<string, unknown>) ?? known?.args ?? {},
				state: error ? 'failed' : 'done',
				progress: known?.progress ?? '',
				// Hermes sends `duration_s` (tui_gateway/tool_progress.py); `duration` is older emitters'.
				duration: num(p.duration_s ?? p.duration),
				error,
				result: p.result === undefined || p.result === null ? null : str(p.result),
				startedAt: known?.startedAt ?? now,
				finishedAt: now
			};
			const tools = known
				? state.turn.tools.map((t) => (t.id === id ? card : t))
				: [...state.turn.tools, card];
			return next({
				turn: { ...state.turn, tools },
				messages: withToolCard(ensurePending(state.messages, now, state.persona), card)
			});
		}

		case 'message.complete': {
			const final = str(p.text);
			const status = str(p.status);
			const failed = Boolean(status) && !QUIET_ENDS.includes(status);
			const text = state.turn.text || final;
			// A call still open when the turn ends did not finish in it: after Stop, a card left
			// running held the core in its tool state (T-0732). A tool.complete that arrives
			// later still updates the card.
			const closed = state.turn.tools
				.filter((t) => t.state === 'running')
				.map((t): ToolCard => ({ ...t, state: 'failed', error: status === 'interrupted' ? 'stopped' : 'did not finish', finishedAt: now }));
			const tools = closed.length ? state.turn.tools.map((t) => closed.find((c) => c.id === t.id) ?? t) : state.turn.tools;
			return next({
				usage: (p.usage as Record<string, unknown>) ?? state.usage,
				turn: {
					...state.turn,
					running: false,
					finishedAt: now,
					text,
					reasoning: state.turn.reasoning || str(p.reasoning),
					error: failed ? status : state.turn.error,
					tools
				},
				messages: patchPending(closed.reduce(withToolCard, state.messages), (m) => ({
					...m,
					pending: false,
					content: m.content || final,
					thinking: m.thinking || str(p.reasoning),
					error: failed ? status : m.error
				}))
			});
		}

		case 'approval.request': {
			const requestId = str(p.request_id);
			if (!requestId) return state;
			const offered = Array.isArray(p.choices)
				? (p.choices.map(String).filter((c) => (APPROVAL_CHOICES as string[]).includes(c)) as ApprovalChoice[])
				: [];
			const request: ApprovalRequest = {
				requestId,
				sessionId: event.session_id ?? state.sessionId ?? '',
				// Not `p.tool`: Hermes emits no such field, so the card used to be
				// titled with the literal word "tool" on every approval. The name is
				// inside `command`/`pattern_key` and the parse is shared — see
				// `approvalEvent.ts`. It is legitimately empty for Hermes' own
				// dangerous-command gate, which asks about a command; `detail` below
				// carries that command, so the card still says what is being asked.
				tool: toolOfApproval(p) || 'tool',
				// `command` is the literal command for Hermes' own dangerous-command
				// gate and a rendered label for a plugin-gated call; only the first is
				// worth showing. A plugin-gated approval carries no `args` either, so
				// this is legitimately empty and the card leans on `reason`.
				detail: isPluginRuleLabel(p.command)
					? summariseToolArgs(p.args as Record<string, unknown>)
					: str(p.command) || summariseToolArgs(p.args as Record<string, unknown>),
				reason: reasonOfApproval(p),
				choices: offered.length ? offered : APPROVAL_CHOICES,
				...(p.confirm === 'phone' ? { onPhone: true } : {}),
				at: now
			};
			// Re-announced (a reconnect replays it): keep what the first copy said
			// that the second left out, and keep its place in the queue.
			const known = state.approvals.find((a) => a.requestId === requestId);
			const merged: ApprovalRequest = known
				? {
						...request,
						detail: request.detail || known.detail,
						reason: request.reason || known.reason,
						at: known.at
					}
				: request;
			return next({
				approvals: known
					? state.approvals.map((a) => (a.requestId === requestId ? merged : a))
					: [...state.approvals, merged]
			});
		}

		case 'approval.resolved':
		case 'approval.cancelled':
			return next({ approvals: state.approvals.filter((a) => a.requestId !== str(p.request_id)) });

		case 'clarify.request': {
			const requestId = str(p.request_id);
			if (!requestId) return state;
			const sessionId = event.session_id ?? state.sessionId ?? '';
			// The gateway's field is `choices`; `options` is the older name the
			// mock and this reducer grew up with, and both are honoured.
			const choicesOf = (q: Record<string, unknown>): string[] => {
				const raw = q.choices ?? q.options;
				return Array.isArray(raw) ? raw.map(String) : [];
			};
			// Two wire shapes (tui_gateway `_clarify_block`): one question, or a
			// batch under `questions`, each with a `qid` the answer is keyed by.
			// The batch was arriving as a request with no text at all -- the
			// console read `question` and the gateway had sent `questions` -- so
			// JARVIS asked and the panel showed an empty card (the owner,
			// 2026-09-09: "questions dont have text when it asks one").
			const questions = Array.isArray(p.questions) ? (p.questions as Record<string, unknown>[]) : null;
			const fresh: ClarifyRequest[] = questions
				? questions.map((q) => ({
						requestId,
						sessionId,
						question: str(q.question),
						options: choicesOf(q),
						at: now,
						questionId: str(q.qid)
					}))
				: [{ requestId, sessionId, question: str(p.question), options: choicesOf(p as Record<string, unknown>), at: now }];
			// A replay carries the answers already locked; those questions are done.
			const answered = p.answers && typeof p.answers === 'object' ? new Set(Object.keys(p.answers as object)) : new Set<string>();
			return next({
				clarifies: [
					...state.clarifies.filter((c) => c.requestId !== requestId),
					...fresh.filter((c) => !c.questionId || !answered.has(c.questionId))
				]
			});
		}

		case 'status.update': {
			if (str(p.kind) === 'goal') return next({ goal: parseGoalText(str(p.text), now) });
			return turn({ status: str(p.text) });
		}

		// The same id redraws in place; a new one joins at the end and the oldest past the cap goes.
		case 'hud.projection': {
			const frame = projectionFromEvent(p);
			if (!frame) return next({});
			const held = state.turn.visuals;
			const at = held.findIndex((v) => v.id === frame.id);
			const visuals = at === -1 ? [...held, frame].slice(-VISUALS_KEPT) : held.map((v, i) => (i === at ? frame : v));
			return turn({ visuals, visualsAt: now });
		}

		case 'hud.panel.show': {
			const panel = panelFromShow(p, now);
			return panel ? next({ panels: applyShow(state.panels, panel) }) : next({});
		}
		case 'hud.panel.update':
			return next({ panels: applyUpdate(state.panels, p, now) });
		case 'hud.panel.dismiss':
			return next({ panels: applyDismiss(state.panels, str(p.id)) });

		// The switch, from any surface: the plugin broadcasts it to every live
		// session, so a switch spoken to the phone re-tints this console too.
		case 'persona.switch': {
			const name = str(p.active).toLowerCase();
			return name ? next({ persona: name, theme: str(p.theme).toLowerCase() }) : state;
		}
		// A drafted theme (hermes/plugins/aura-personas/drafts.py), broadcast the same way.
		case 'theme.preview': {
			const pack = validPack(p.pack);
			return pack ? next({ themePreview: { id: pack.id, name: str(p.name) || pack.name, pack } }) : state;
		}
		case 'theme.apply': {
			const id = str(p.id).toLowerCase();
			const pack = p.pack == null ? null : validPack(p.pack);
			if (!id || (p.pack != null && (!pack || pack.id !== id))) return state;
			const preview = state.themePreview?.id === id ? null : state.themePreview;
			return next({ theme: id, themeApplied: { id, pack }, themePreview: preview });
		}

		case 'subagent.start': {
			const id = str(p.id ?? p.subagent_id ?? p.task_id) || `subagent-${seq}`;
			const parent = str(p.parent_id);
			const depth = num(p.depth);
			const session = str(p.child_session_id);
			const row: BackgroundRow = {
				id,
				kind: 'subagent',
				label: str(p.goal ?? p.task ?? p.name) || 'delegated task',
				state: 'running',
				detail: str(p.status),
				at: now,
				tools: num(p.tool_count) ?? 0,
				...(parent ? { parent } : {}),
				...(depth !== null ? { depth } : {}),
				...(session ? { session } : {})
			};
			return next({ background: upsertRow(state.background, row) });
		}
		case 'subagent.tool': {
			const id = str(p.id ?? p.subagent_id ?? p.task_id);
			if (!id) return state;
			// Hermes' `tool_count` already includes this call; an emitter without one is counted here.
			return next({
				background: patchSubagent(state.background, id, (r) => ({ ...r, tools: num(p.tool_count) ?? (r.tools ?? 0) + 1 }))
			});
		}
		case 'subagent.complete': {
			// By id only. A complete with no id used to mark every subagent row
			// done, which with a batch of children made the first finisher finish
			// them all on the console while the rest were still working.
			const id = str(p.id ?? p.subagent_id ?? p.task_id);
			if (!id) return state;
			const status = str(p.status).toLowerCase();
			const summary = str(p.summary);
			return next({
				background: patchSubagent(state.background, id, (r) => ({
					...r,
					state: /fail|error|timeout/.test(status) ? 'failed' : status.includes('interrupt') ? 'interrupted' : 'done',
					detail: summary || r.detail,
					tools: num(p.tool_count) ?? r.tools,
					...(summary ? { summary } : {})
				}))
			});
		}

		case 'error':
			return next({
				error: str(p.message) || 'error',
				turn: { ...state.turn, running: false, finishedAt: now, error: str(p.message) || 'error' },
				messages: patchPending(state.messages, (m) => ({
					...m,
					pending: false,
					error: str(p.message) || 'error'
				}))
			});
	}

	// `process.*`: background processes, whatever the suffix says happened to one.
	if (event.type.startsWith('process.')) {
		const verb = event.type.slice('process.'.length);
		const id = str(p.pid ?? p.id ?? p.process_id) || `process-${seq}`;
		const label = str(p.command ?? p.name ?? p.label) || `process ${id}`;
		const existing = state.background.find((r) => r.id === id && r.kind === 'process');
		const finished = /^(exit|exited|complete|completed|done|end|ended|stop|stopped|kill|killed)$/.test(verb);
		const failed = /^(fail|failed|error)$/.test(verb) || (finished && num(p.exit_code) !== null && num(p.exit_code) !== 0);
		const row: BackgroundRow = {
			id,
			kind: 'process',
			label: existing?.label ?? label,
			state: failed ? 'failed' : finished ? 'done' : 'running',
			detail: str(p.text ?? p.line ?? p.output ?? p.status) || existing?.detail || '',
			at: existing?.at ?? now
		};
		return next({ background: upsertRow(state.background, row) });
	}

	return { ...state, lastSeq: seq };
}

function patchSubagent(rows: BackgroundRow[], id: string, patch: (r: BackgroundRow) => BackgroundRow): BackgroundRow[] {
	return rows.map((r) => (r.kind === 'subagent' && r.id === id ? patch(r) : r));
}

function upsertRow(rows: BackgroundRow[], row: BackgroundRow): BackgroundRow[] {
	return rows.some((r) => r.id === row.id && r.kind === row.kind)
		? rows.map((r) => (r.id === row.id && r.kind === row.kind ? row : r))
		: [...rows, row];
}

/**
 * Seed the panel surface from a `hud list` reply, for the session it is for.
 *
 * Guarded by the session id: a list that comes back after the person has moved
 * to another conversation is dropped rather than drawn on the wrong one.
 */
export function seedPanels(
	state: HudState,
	sessionId: string,
	list: unknown,
	now = Date.now()
): HudState {
	if (state.sessionId !== sessionId) return state;
	return { ...state, panels: seedFromList(state.panels, list, now) };
}

/** The person took over the activity: stopped here, whatever the emitter's later `end` says. */
export function stopActivityLocally(state: HudState, now = Date.now()): HudState {
	const activity = stopActivity(state.activity, now);
	return activity === state.activity ? state : { ...state, activity };
}

/** Take a panel off the surface without telling the server — an optimistic dismiss or a ttl timeout. */
export function dismissPanelLocally(state: HudState, id: string): HudState {
	const panels = applyDismiss(state.panels, id);
	return panels === state.panels ? state : { ...state, panels };
}

/** Drop panels whose local ttl has passed; the emitter's dismiss is still the authority. */
export function prunePanels(state: HudState, now = Date.now()): HudState {
	const panels = pruneExpired(state.panels, now);
	return panels === state.panels ? state : { ...state, panels };
}

/** An approval answered: off the queue, whichever way it went. */
export function withoutApproval(state: HudState, requestId: string): HudState {
	return { ...state, approvals: state.approvals.filter((a) => a.requestId !== requestId) };
}

export function withoutClarify(state: HudState, requestId: string, questionId?: string): HudState {
	return {
		...state,
		clarifies: state.clarifies.filter((c) => c.requestId !== requestId || (questionId != null && c.questionId !== questionId))
	};
}

/** The rows that are still going, for the dock. */
export function runningRows(state: HudState): { tools: ToolCard[]; background: BackgroundRow[] } {
	return {
		tools: state.turn.tools.filter((t) => t.state === 'running'),
		background: state.background.filter((r) => r.state === 'running')
	};
}

/**
 * How long what a turn drew stays out of the core once nothing else is going on. A list is
 * worked through and a chart is read after the last word (and with speech off there is no
 * spoken answer to keep it out at all), but a forecast must not stand on a wall console all
 * day. A redraw of the same id starts it again.
 */
export const VISUAL_HOLD_MS = 300_000;

/**
 * Whether the turn's visuals are still held out at [now]: there is one, and the newest
 * arrived or was redrawn less than VISUAL_HOLD_MS ago. True for a frame that arrived while
 * no turn ran (another session's, shared with this screen: T-0785).
 * Does not cover: the next turn, which clears them in the reducer, or the person sending
 * them back, which is the shell's (it remembers the stamp it dismissed).
 */
export function visualsHeld(state: HudState | null | undefined, now = Date.now()): boolean {
	const turn = state?.turn;
	if (!turn || !turn.visuals.length || turn.visualsAt === null) return false;
	return now - turn.visualsAt < VISUAL_HOLD_MS;
}

/**
 * Finished background rows age out of the dock so a process that ended an
 * hour ago is not "running in the background" all afternoon.
 */
export const LINGER_MS = 30_000;

export function pruneBackground(state: HudState, now = Date.now()): HudState {
	const kept = state.background.filter((r) => r.state === 'running' || now - r.at < LINGER_MS);
	return kept.length === state.background.length ? state : { ...state, background: kept };
}
