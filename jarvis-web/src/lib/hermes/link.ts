// The console's link to Hermes, as one thing the layout owns.
//
// One socket for the whole app: the approvals banner on every route, the
// voice screen, chat mode, the goals panel and the settings pages all read
// from here, and a page that opened its own socket would get its own session
// and its own copy of every event. The layout constructs one of these, starts
// it once, and hands it down through context.
//
// What it holds is a snapshot — the link's status, the HUD state the reducer
// built, the adapter's status report — and what it offers is the handful of
// verbs the pages need. Everything Hermes-shaped is delegated: the socket to
// `HermesClient`, the state to `hud.ts`, the clock to `StatusPoller`.

import { HermesClient, SURFACE, type HermesStatus } from './client';
import {
	attachSession,
	beginTurn,
	detachSession,
	dismissPanelLocally,
	failTurn,
	initialHud,
	pruneBackground,
	prunePanels,
	reduce,
	seedPanels,
	stopActivityLocally,
	withoutApproval,
	withoutClarify,
	type HudState
} from './hud';
import { MEMORY_COMMAND } from './memory-proposals';
import { parsePanelList } from './panels';
import { NO_GOAL } from './goal';
import { noticesFor, notifyBrowser } from './notify';
import {
	PLUGIN_COMMAND,
	isSessionNotFound,
	pluginArg,
	type ApprovalChoice,
	type DispatchOutcome,
	type SessionCreated,
	type SessionHistory,
	type SessionList,
	type SessionResumed,
	type SessionSummary,
	type StatusReport
} from './protocol';
import { StatusPoller, isWarmingUp, modelOf } from './status';
import {
	SETTINGS_COMMAND,
	parseSettingsOutput,
	type SettingsChange,
	type SettingsGetResult,
	type SettingsListItem,
	type SettingsProposal,
	type SettingsSetOutcome,
	type SettingsSetResult
} from './settings';
import { goalFromPhrase, isGoalPhrase } from './voice';
import { wornName } from '$lib/ds/theme.svelte';

export const SESSION_KEY = 'jarvis.hermes.session';
/** The `jarvis-personas` plugin's command; the name rides in the arg (`@<sid> AURA`). */
export const PERSONA_COMMAND = 'persona';

export interface HermesSnapshot {
	status: HermesStatus;
	/** The link has been up at least once this page: the first paint is over. */
	everConnected: boolean;
	/** A session is attached and can take a prompt. */
	ready: boolean;
	hud: HudState;
	report: StatusReport | null;
	statusError: string | null;
	warmingUp: boolean;
	/** The model, from the session when there is one and the status otherwise. */
	model: string;
	/** Why the last session could not be opened, in words. Empty when it could. */
	sessionError: string;
}

export type HermesListener = (snapshot: HermesSnapshot) => void;

/** The two things the link keeps in the browser: injectable for tests. */
export interface StorageLike {
	getItem(key: string): string | null;
	setItem(key: string, value: string): void;
	removeItem(key: string): void;
}

export interface HermesLinkOptions {
	client?: HermesClient;
	poller?: StatusPoller;
	storage?: StorageLike | null;
	now?: () => number;
}

export class HermesLink {
	readonly client: HermesClient;
	readonly poller: StatusPoller;
	private readonly storage: StorageLike | null;
	private readonly now: () => number;
	private listeners = new Set<HermesListener>();
	private snapshot: HermesSnapshot;
	private hud: HudState = initialHud();
	private unsubs: Array<() => void> = [];
	/** The session a page asked for before the link was up, or `null` for "a new one". */
	private wanted: string | null | undefined = undefined;
	private attaching: Promise<void> | null = null;
	/** This socket has a session attached: false from any status change until attach lands one. */
	private live = false;
	private started = false;

	constructor(opts: HermesLinkOptions = {}) {
		this.client = opts.client ?? new HermesClient();
		this.poller = opts.poller ?? new StatusPoller();
		this.storage = opts.storage === undefined ? defaultStorage() : opts.storage;
		this.now = opts.now ?? Date.now;
		this.snapshot = this.build('idle', false);
	}

	get current(): HermesSnapshot {
		return this.snapshot;
	}

	get sessionId(): string | null {
		return this.hud.sessionId;
	}

	subscribe(listener: HermesListener): () => void {
		this.listeners.add(listener);
		listener(this.snapshot);
		return () => this.listeners.delete(listener);
	}

	start(): void {
		if (this.started) return;
		this.started = true;
		this.unsubs.push(
			this.client.onStatus((status) => {
				// The kept hud.sessionId is the old socket's after a drop; it is not live until re-attached.
				this.live = false;
				if (status === 'connected') void this.attach();
				else if (status !== 'idle') this.emit(status);
			}),
			this.client.onEvent((event) => {
				const before = this.hud;
				this.hud = prunePanels(
					pruneBackground(reduce(this.hud, event, this.now()), this.now()),
					this.now()
				);
				// An event that changed nothing -- another session's (the reducer drops
				// those), a heartbeat -- notifies nobody. Every one used to, and each
				// re-derived the whole HUD: a page beside twenty others' sessions spent
				// 80-100 ms a frame on events that were not its own (2026-09-23).
				if (this.hud === before) return;
				if (event.type === 'session.info' && !before.storedSessionId && this.hud.storedSessionId) {
					this.remember(this.hud.storedSessionId);
				}
				// A backgrounded tab: an approval or a goal that stopped reaches the person
				// through the browser too (no-op without the API or permission).
				notifyBrowser(noticesFor(before, this.hud));
				this.emit();
			}),
			this.poller.subscribe(() => this.emit())
		);
		this.client.start();
		this.poller.start();
	}

	stop(): void {
		this.started = false;
		for (const unsub of this.unsubs) unsub();
		this.unsubs = [];
		this.poller.stop();
		this.client.stop();
		this.hud = detachSession(this.hud);
		this.emit('idle');
	}

	/** RECONNECT NOW: dial again without waiting out the backoff. */
	reconnectNow(): void {
		this.client.reconnectNow();
		void this.poller.refresh();
	}

	// --- sessions ----------------------------------------------------------------

	/**
	 * Attach a session on this socket: the one asked for, the one remembered,
	 * or a new one. Runs on every (re)connect, because a reconnected socket
	 * has no session until it resumes one.
	 */
	private attach(): Promise<void> {
		if (this.attaching) return this.attaching;
		this.attaching = this.doAttach().finally(() => (this.attaching = null));
		return this.attaching;
	}

	private async doAttach(): Promise<void> {
		const asked = this.wanted;
		this.wanted = undefined;
		const remembered = asked === undefined ? this.remembered() : asked;
		let sessionError = '';
		if (remembered) {
			try {
				const resumed = await this.client.request<SessionResumed>('session.resume', {
					session_id: remembered
				});
				this.hud = attachSession(
					this.hud,
					{
						sessionId: resumed.session_id,
						storedSessionId: remembered,
						messages: resumed.messages,
						info: resumed.info,
						running: resumed.running
					},
					this.now()
				);
				this.live = true;
				this.remember(remembered);
				this.emit(undefined, sessionError);
				this.syncPending();
				void this.seedPanelsFor(resumed.session_id);
				return;
			} catch (e) {
				// Not there any more (or never was: a link to a thread the backend
				// does not have). Start fresh rather than sit sessionless, and say so
				// only for one that was asked for: Hermes writes a session at its first
				// message, so the remembered one is gone whenever the last open said
				// nothing, and every open said it was lost (T-0886).
				sessionError = isSessionNotFound(e)
					? asked
						? `That conversation is not on this ${wornName()} any more (${remembered}); this is a new one.`
						: ''
					: e instanceof Error
						? e.message
						: String(e);
				this.forget();
			}
		}
		try {
			const created = await this.client.request<SessionCreated>('session.create', {});
			this.hud = attachSession(
				this.hud,
				{
					sessionId: created.session_id,
					storedSessionId: created.stored_session_id ?? null,
					messages: created.messages,
					info: created.info
				},
				this.now()
			);
			this.live = true;
			if (created.stored_session_id) this.remember(created.stored_session_id);
			this.emit(undefined, sessionError);
			this.syncPending();
			void this.seedPanelsFor(created.session_id);
			return;
		} catch (e) {
			sessionError = e instanceof Error ? e.message : String(e);
		}
		this.emit(undefined, sessionError);
		this.syncPending();
	}

	/**
	 * Catch a late-joining client up on the panels already on the surface.
	 *
	 * `hud list` is a plugin command (its arg carries the session, as the goal
	 * plugin's do). A backend without the HUD plugin answers an error, which is
	 * not a failure here — there are simply no panels to seed.
	 */
	private async seedPanelsFor(sessionId: string): Promise<void> {
		try {
			const result = await this.client.request<{ output?: string }>('command.dispatch', {
				session_id: sessionId,
				name: 'hud',
				arg: pluginArg(sessionId, 'list')
			});
			const output = result && typeof result === 'object' ? result.output : undefined;
			this.hud = seedPanels(this.hud, sessionId, parsePanelList(output), this.now());
			this.emit();
		} catch {
			// No HUD plugin, or the socket went away mid-request: no panels to seed.
		}
	}

	/**
	 * Dismiss a panel: gone from the surface at once (optimistic), and the
	 * `hud dismiss <id>` command told to the plugin so it agrees and every other
	 * screen follows. The emitter's own `hud.panel.dismiss` reconciles.
	 */
	async dismissPanel(id: string): Promise<void> {
		const sessionId = this.hud.sessionId;
		if (!sessionId) return;
		this.hud = dismissPanelLocally(this.hud, id);
		this.emit();
		try {
			await this.client.request('command.dispatch', {
				session_id: sessionId,
				name: 'hud',
				arg: pluginArg(sessionId, `dismiss ${id}`)
			});
		} catch {
			// The panel is already off this screen; the plugin's own dismiss, when
			// it comes, is a no-op. A backend without the plugin simply keeps it off.
		}
	}

	/** A panel whose ttl passed with no dismiss: take it off locally, the fallback timer. */
	expirePanel(id: string): void {
		const before = this.hud;
		this.hud = dismissPanelLocally(this.hud, id);
		if (this.hud !== before) this.emit();
	}

	/** Open a stored session by id — the sidebar, or a `?session=` link. */
	async openSession(storedId: string): Promise<void> {
		this.wanted = storedId;
		if (this.client.connected) await this.attach();
	}

	/** A new conversation. */
	async newSession(): Promise<void> {
		this.forget();
		this.wanted = null;
		this.hud = detachSession(this.hud);
		this.emit();
		if (this.client.connected) await this.attach();
	}

	listSessions(): Promise<SessionSummary[]> {
		return this.client
			.request<SessionList | SessionSummary[]>('session.list', {})
			.then((r) => (Array.isArray(r) ? r : (r?.sessions ?? [])));
	}

	async deleteSession(storedId: string): Promise<void> {
		await this.client.request('session.delete', { session_id: storedId });
		if (this.hud.storedSessionId === storedId) await this.newSession();
	}

	/** The transcript again, from the backend, for a session that is open. */
	async reloadHistory(): Promise<void> {
		const sessionId = this.hud.sessionId;
		if (!sessionId) return;
		const history = await this.client.request<SessionHistory>('session.history', {
			session_id: sessionId
		});
		this.hud = attachSession(
			this.hud,
			{
				sessionId,
				storedSessionId: this.hud.storedSessionId,
				messages: history.messages,
				info: { model: this.hud.model, provider: this.hud.provider, title: this.hud.title }
			},
			this.now()
		);
		this.emit();
	}

	// --- turns -------------------------------------------------------------------

	/**
	 * Say something. A goal phrase dispatches `goal`; anything else is
	 * `prompt.submit`. The transcript shows the words at once.
	 *
	 * `photo` (base64 JPEG) is attached to the session first: Hermes queues it for
	 * the next `prompt.submit` and has no detach, so it goes only with these words.
	 * The adapter relays the prompt after an attach even when it would answer it itself.
	 * Not covered: a `prompt.submit` that fails after the attach leaves the photo queued,
	 * so the next prompt on the session (usually the retry) carries it.
	 */
	async submit(text: string, speakerTicket?: string | null, photo?: string | null, voice = false): Promise<DispatchOutcome | null> {
		const trimmed = String(text ?? '').trim();
		if (!trimmed) return null;
		const sessionId = await this.requireSession();
		if (!photo && isGoalPhrase(trimmed)) return this.dispatch('goal', goalFromPhrase(trimmed));
		this.hud = beginTurn(this.hud, trimmed, this.now());
		this.emit();
		this.syncPending();
		try {
			if (photo) await this.client.request('image.attach_bytes', { session_id: sessionId, content_base64: photo, ext: 'jpg' });
			// The ticket is the speaker gate's word that a recognised voice said
			// this (`/hermes/api/audio/transcribe`); the adapter reads it off the
			// prompt, credits the session to that person, and strips it before
			// Hermes sees the frame. A typed line has none and is the key holder's.
			// `surface` says what this screen can show; the adapter keeps it per
			// session and strips it too, so a video asked for here is shown here.
			// `voice: true` says the words were spoken, whatever the gate did (a
			// refused or switched-off gate leaves no ticket); the adapter runs a
			// spoken turn at the voice reasoning effort and strips it
			// (`tests/contracts/voice-flag.json`). A typed line never sends it.
			await this.client.request('prompt.submit', {
				session_id: sessionId,
				text: trimmed,
				surface: SURFACE,
				...(speakerTicket ? { speaker_ticket: speakerTicket } : {}),
				...(voice ? { voice: true } : {})
			});
		} catch (e) {
			this.hud = failTurn(this.hud, e instanceof Error ? e.message : String(e), this.now());
			this.emit();
			this.syncPending();
			return { kind: 'error', message: e instanceof Error ? e.message : String(e) };
		}
		return null;
	}

	/** A slash command without the slash; a `send` result has already been submitted. */
	async dispatch(name: string, arg = ''): Promise<DispatchOutcome> {
		let sessionId: string;
		try {
			sessionId = await this.requireSession();
		} catch (e) {
			return { kind: 'error', message: e instanceof Error ? e.message : String(e) };
		}
		const outcome = await this.client.dispatchCommand(sessionId, name, arg);
		if (outcome.kind === 'sent') {
			// The client submitted the message; the transcript shows it as the
			// turn's question, exactly as the TUI echoes it.
			this.hud = beginTurn(this.hud, outcome.message, this.now());
			this.emit();
			this.syncPending();
		}
		return outcome;
	}

	/**
	 * A `jgoal` sub-command — `draft <text>`, `gate add <cmd>`, `subgoal <text>` —
	 * with the session prefixed, because plugin handlers are not handed one. `name`
	 * picks another plugin command that reads the same `@<sid>` prefix (`guard`). It waits
	 * for the attach as `dispatch` does: a screen that asks on open asked before the session
	 * existed and got "no session" (T-0570).
	 */
	async dispatchPlugin(arg: string, name = PLUGIN_COMMAND): Promise<DispatchOutcome> {
		let sessionId: string;
		try {
			sessionId = await this.requireSession();
		} catch (e) {
			return { kind: 'error', message: e instanceof Error ? e.message : String(e) };
		}
		return this.dispatch(name, pluginArg(sessionId, arg));
	}

	async interrupt(): Promise<void> {
		const sessionId = this.hud.sessionId;
		if (!sessionId) return;
		await this.client.request('session.interrupt', { session_id: sessionId });
	}

	/**
	 * The person takes over what the browser or the desktop was doing: the
	 * activity reads `stopped` here at once (the contract's client-only status),
	 * and the turn driving it is interrupted so the agent does not fight the
	 * person for the page. Opening the page itself is the caller's, because a
	 * new tab must come from the click's own user gesture.
	 */
	async takeOver(): Promise<void> {
		const before = this.hud;
		this.hud = stopActivityLocally(this.hud);
		if (this.hud !== before) this.emit();
		await this.interrupt();
	}

	async steer(text: string): Promise<void> {
		const sessionId = await this.requireSession();
		await this.client.request('session.steer', { session_id: sessionId, text });
	}

	/**
	 * Stop one delegated subagent; the turn and its other children go on.
	 * Hermes' own method, keyed on the child's id rather than a session: the
	 * `subagent.complete` for it follows on the parent session as usual.
	 */
	interruptSubagent(subagentId: string): Promise<{ found: boolean; subagent_id: string }> {
		return this.client.request('subagent.interrupt', { subagent_id: subagentId });
	}

	async respondApproval(requestId: string, choice: ApprovalChoice): Promise<void> {
		const request = this.hud.approvals.find((a) => a.requestId === requestId);
		const sessionId = request?.sessionId || this.hud.sessionId;
		if (!sessionId) throw new Error('no session');
		await this.client.request('approval.respond', {
			session_id: sessionId,
			request_id: requestId,
			choice
		});
		this.hud = withoutApproval(this.hud, requestId);
		this.emit();
	}

	async respondClarify(requestId: string, answer: string, questionId?: string): Promise<void> {
		const request = this.hud.clarifies.find((c) => c.requestId === requestId && (!questionId || c.questionId === questionId));
		const sessionId = request?.sessionId || this.hud.sessionId;
		if (!sessionId) throw new Error('no session');
		// One question of a batch is answered by its id and the gateway keeps
		// the request open until every one is; a single question closes it.
		await this.client.request('clarify.respond', {
			session_id: sessionId,
			request_id: requestId,
			answer,
			...(questionId ? { question_id: questionId } : {})
		});
		this.hud = withoutClarify(this.hud, requestId, questionId);
		this.emit();
	}

	configGet<T = unknown>(key: string): Promise<T> {
		return this.client.request<T>('config.get', { key });
	}

	configSet(key: string, value: unknown): Promise<unknown> {
		return this.client.request('config.set', { key, value });
	}

	/**
	 * This conversation's model, through Hermes' session-scoped switch (the path `/model` takes);
	 * the session.info it emits is what moves `hud.model`. Mid-turn, Hermes holds it until the
	 * turn ends. Does not load the model: an unloaded one is a cold start on its next turn.
	 */
	async setModel(model: string): Promise<void> {
		const sessionId = await this.requireSession();
		await this.client.request('config.set', { key: 'model', value: model, session_id: sessionId });
	}

	/**
	 * One `jsettings` sub-command through the `jarvis-settings` plugin.
	 *
	 * Not `config.get`/`config.set`: the gateway's config only knows prompt/
	 * personality/display keys and answers `4002` for anything else (which is
	 * exactly the error the operator saw). Every schema leaf lives behind the
	 * `jsettings` command, whose reply is a `plugin` result carrying a JSON
	 * string; `{"error": …}` inside it is thrown as `SettingsError`.
	 */
	private settingsCommand<T>(sub: string): Promise<T> {
		return this.jsonCommand<T>(SETTINGS_COMMAND, sub);
	}

	/** A plugin command that answers a JSON string (`jsettings`, `jmemory`): the session prefixed, `{error}` thrown. */
	private async jsonCommand<T>(name: string, sub: string): Promise<T> {
		const sessionId = await this.requireSession();
		const result = await this.client.request<{ output?: string }>('command.dispatch', {
			session_id: sessionId,
			name,
			arg: pluginArg(sessionId, sub)
		});
		return parseSettingsOutput<T>(result?.output);
	}

	/** One row: its value, where it comes from and when a write would apply. */
	settingsGet(key: string): Promise<SettingsGetResult> {
		return this.settingsCommand(`get ${key}`);
	}

	/**
	 * Write one setting; the value is JSON so a list or a number keeps its type.
	 * From here (the command path) the plugin asks the approval matrix first: only a
	 * security key becomes a proposal (`{status: 'proposed'}`, `settingsApply` is the
	 * yes); any other key is written at once and the answer is the write.
	 */
	settingsSet(key: string, value: unknown): Promise<SettingsSetOutcome> {
		return this.settingsCommand(`set ${key} ${JSON.stringify(value)}`);
	}

	/** Every leaf the plugin serves, with its current value, source and `applies`. */
	settingsList(): Promise<SettingsListItem[]> {
		return this.settingsCommand('list');
	}

	/** The person's yes to a proposal: the plugin validates again and writes. */
	settingsApply(id: string): Promise<SettingsSetResult> {
		return this.settingsCommand(`apply ${id}`);
	}

	settingsDiscard(id: string): Promise<{ status: 'discarded'; id: string }> {
		return this.settingsCommand(`discard ${id}`);
	}

	/** What is waiting: a proposal survives a reload and a restart, so the menu lists them on open. */
	settingsProposals(): Promise<SettingsProposal[]> {
		return this.settingsCommand('proposals');
	}

	/** The changes made, newest first (the plugin lists 20 and keeps a secret masked). A read. */
	settingsHistory(): Promise<SettingsChange[]> {
		return this.settingsCommand('history');
	}

	/**
	 * Put the last change (of `key`, when given) back. One level, and never a secret. A
	 * security setting is not written from here: the answer is a proposal, as `settingsSet`'s is.
	 */
	settingsUndo(key = ''): Promise<SettingsSetOutcome> {
		return this.settingsCommand(key ? `undo ${key}` : 'undo');
	}

	/**
	 * What waits to be remembered (the jarvis-hud plugin's `jmemory`, T-1503): each proposal whole,
	 * with who proposed it and what was read. It reaches the screen that asked, never the model.
	 */
	memoryProposals(): Promise<unknown> {
		return this.jsonCommand(MEMORY_COMMAND, 'proposals');
	}

	/** Drop one proposal. There is no keep here: a keep is asked of the assistant, so its approval card is drawn. */
	memoryProposalDrop(id: string): Promise<unknown> {
		return this.jsonCommand(MEMORY_COMMAND, `drop ${id}`);
	}

	/**
	 * Make another persona primary (ADR-0043), through the `jarvis-personas`
	 * plugin's `/persona` command: the switch is global, and the plugin tells
	 * every live session with `persona.switch`, this one included, which is what
	 * changes `hud.persona` -- nothing is assumed locally before the plugin says
	 * so, because a switch that happened on one screen and not another is the
	 * defect the acceptance test names.
	 */
	async switchPersona(name: string): Promise<DispatchOutcome> {
		const sessionId = await this.requireSession();
		return this.dispatch(PERSONA_COMMAND, pluginArg(sessionId, name));
	}

	/** Forget the goal shown, locally, when the loop said it is over. */
	clearGoalLocally(): void {
		this.hud = { ...this.hud, goal: NO_GOAL };
		this.emit();
	}

	// --- plumbing ----------------------------------------------------------------

	private async requireSession(): Promise<string> {
		// A redial is seconds: wait it out, then for the re-attach, whose live id is
		// not the old one (AUDIT B5). Offline or stopped still fails at once.
		await this.redialed();
		if (this.attaching) await this.attaching;
		// On an open socket only while live: after a re-attach that failed, the kept id is the
		// dead socket's (T-1192). Offline it is returned as before, and the send fails by itself.
		if (this.hud.sessionId && (this.live || !this.client.connected)) return this.hud.sessionId;
		if (!this.client.connected) throw new Error('no link to Hermes');
		await this.attach();
		if (!this.live || !this.hud.sessionId) throw new Error(this.snapshot.sessionError || 'no session');
		return this.hud.sessionId;
	}

	/**
	 * Resolves once the client is neither connecting nor reconnecting. Not covered: it does
	 * not bound a dial the browser never fails; the socket's own close does.
	 */
	private redialed(): Promise<void> {
		return new Promise((resolve) => {
			let off: (() => void) | null = null;
			let done = false;
			off = this.client.onStatus((status) => {
				if (done || status === 'connecting' || status === 'reconnecting') return;
				done = true;
				off?.();
				resolve();
			});
			if (done) off();
		});
	}

	private syncPending(): void {
		this.poller.setPending(this.hud.turn.running, this.hud.turn.gotToken);
	}

	private remembered(): string | null {
		try {
			return this.storage?.getItem(SESSION_KEY) ?? null;
		} catch {
			return null;
		}
	}

	private remember(storedId: string): void {
		try {
			this.storage?.setItem(SESSION_KEY, storedId);
		} catch {
			/* private mode: the session is remembered for this page only */
		}
	}

	private forget(): void {
		try {
			this.storage?.removeItem(SESSION_KEY);
		} catch {
			/* nothing to forget */
		}
	}

	private build(status: HermesStatus, everConnected: boolean, sessionError = ''): HermesSnapshot {
		const report = this.poller.report;
		return {
			status,
			everConnected,
			ready: status === 'connected' && this.live && this.hud.sessionId !== null,
			hud: this.hud,
			report,
			statusError: this.poller.error,
			warmingUp: isWarmingUp(report),
			model: this.hud.model || modelOf(report),
			sessionError
		};
	}

	private emit(status?: HermesStatus, sessionError?: string): void {
		const next = status ?? this.client.status;
		this.snapshot = this.build(
			next,
			this.snapshot.everConnected || next === 'connected',
			sessionError ?? this.snapshot.sessionError
		);
		this.syncPending();
		for (const listener of this.listeners) listener(this.snapshot);
	}
}

function defaultStorage(): StorageLike | null {
	try {
		return typeof localStorage === 'undefined' ? null : localStorage;
	} catch {
		return null;
	}
}
