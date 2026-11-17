// The feeds the panels read: one per thing a screen used to load for itself.
//
// Every old section dialled its own socket, loaded its own rows and kept its
// own `loading`/`err` pair — fourteen copies of the same forty lines, each
// with its own idea of what "offline" looked like. The HUD has one house
// socket (the layout's `ConsoleLink`) and one Hermes link, so it has one
// place that turns "the socket is up" into "these rows are loaded" and "the
// socket dropped" into every panel's offline state at once.
//
// A feed is loaded only while a panel that reads it is on the surface: a
// wall panel showing the Working layout does not pull the house's registries,
// the extensions catalogue and the environment on the off chance. `activate`
// is what the host calls with the types the current layout holds.
//
// Runes, so a body re-renders when its feed does; plain fields otherwise.

import { EVENT_TASK_ADDED, EVENT_TASK_REMOVED, EVENT_TASK_UPDATED, applyTaskEvent, type TaskRow } from '$lib/tasks';
import type { CodeResult } from '$lib/code';
import { describeError, type Connection } from '$lib/connection';
import type { SpeakerStatus } from '$lib/enrolment';
import type { HermesLink } from '$lib/hermes/link';
import type { CronJob, Skill as HermesSkill, Toolset } from '$lib/hermes/protocol';
import { asStringList, type SettingsListItem, type ToolsetLists } from '$lib/hermes/settings';
import type { AutomationRow, CompanionDevice, McpListing, PendingApproval, SettingRow, Skill, Subscription, ToolRow } from '$lib/jarvisClient';
import { parseCapabilities, type Capability } from './data/capabilities';
import { parseMessages, type Messages } from './data/messages';
import type { MomentRow, ReadingsPayload, SkySummary } from '$lib/dashboards/widgets';
import type { ScheduledJob } from '$lib/schedule';
import type { LogEntry } from '$lib/taskEvents';
import type { Status } from '$lib/ui/status';
import { EMPTY_MODELS, type ModelsData } from './data/models';
import { loadModels } from './models.svelte';
import { accountsView, tokensView, type AccountRow, type CatalogueRow, type ExtensionRow, type MemoryEntry, type NoteRow, type TokenRow } from './data';
import type { CatalogueProblem } from '$lib/glass';

/** What a feed's loader is handed: whichever links are up. */
export interface Sources {
	conn: Connection | null;
	hermes: HermesLink | null;
}

type Loader<T> = (sources: Sources) => Promise<T>;

export class Feed<T> {
	data = $state.raw<T>(undefined as unknown as T);
	status = $state<Status>('loading');
	error = $state('');
	/** Loaded at least once this session: an offline panel still shows the last rows. */
	loaded = $state(false);
	/** A load is in flight. Unlike `status`, it stays true over rows already on screen: a Retry shows it is working. */
	reloading = $state(false);
	private generation = 0;

	constructor(
		readonly name: string,
		private readonly empty: T,
		private readonly loader: Loader<T>,
		private readonly isEmpty: (data: T) => boolean = () => false,
		/** Which link the feed needs up: the house socket, Hermes, or neither (a plain fetch). */
		readonly needs: 'conn' | 'hermes' | 'none' = 'conn'
	) {
		this.data = empty;
	}

	async load(sources: Sources): Promise<void> {
		if (this.needs === 'conn' && !sources.conn) return this.dropped();
		if (this.needs === 'hermes' && !sources.hermes) return this.dropped();
		const mine = ++this.generation;
		if (!this.loaded) this.status = 'loading';
		this.reloading = true;
		const work = this.loader(sources);
		try {
			const data = await withDeadline(work, LOAD_DEADLINE_MS);
			// A load that answered after a newer one started is stale: the newer
			// one's rows are the ones on screen, and writing over them with these
			// would show a list that then snaps back a moment later.
			if (mine !== this.generation) return;
			this.take(data);
		} catch (e) {
			if (mine !== this.generation) return;
			this.error = describeError(e);
			this.status = 'error';
			// Past the deadline the panel says so, but the answer may still come: the models list
			// answered at 7 s on a loaded host and was dropped, so the chat's model select never
			// listed the models (T-0830). Taken unless a newer load started since.
			if (e instanceof TookTooLong) work.then((late) => mine === this.generation && this.take(late), () => {});
		} finally {
			// A newer load owns the flag: it clears it when it lands.
			if (mine === this.generation) this.reloading = false;
		}
	}

	private take(data: T): void {
		this.data = data;
		this.loaded = true;
		this.error = '';
		this.status = this.isEmpty(data) ? 'empty' : 'ready';
	}

	/**
	 * The link is not there. A feed that has rows is offline — what is on
	 * screen is the last state, and says so; one that never loaded stays
	 * loading, because before the first connection "offline" would be the
	 * skeleton's slot saying the house is down while the socket is still
	 * dialling. The host reads the link's own status for that case.
	 */
	dropped(): void {
		if (this.loaded) this.status = 'offline';
	}

	/** Replace the rows from an event without a round trip. */
	patch(change: (data: T) => T): void {
		if (!this.loaded) return;
		const next = change(this.data);
		if (next === this.data) return;
		this.data = next;
		this.status = this.isEmpty(next) ? 'empty' : 'ready';
	}

	reset(): void {
		this.generation++;
		this.reloading = false;
		this.data = this.empty;
		this.loaded = false;
		this.status = 'loading';
		this.error = '';
	}
}

const listEmpty = <T>(rows: readonly T[]) => rows.length === 0;

/** GET one of the adapter's JSON endpoints through the console's relay. */
async function hermesJson<T>(path: string): Promise<T> {
	const res = await fetch(path, { headers: { accept: 'application/json' } });
	if (!res.ok) {
		// 502 and 503 are the relay saying the adapter is not there — the
		// offline state's sentence, not the error state's.
		throw new Error(res.status === 502 || res.status === 503 ? 'The adapter is not reachable.' : `The adapter answered ${res.status}.`);
	}
	return (await res.json()) as T;
}

export interface IntegrationRows {
	tools: ToolRow[];
	skills: Skill[];
	hermesSkills: HermesSkill[];
	mcp: McpListing['servers'];
	extensions: ExtensionRow[];
	catalogue: CatalogueRow[];
	/** Null when every registry answered; otherwise why `catalogue` is short or empty. */
	catalogueProblem: CatalogueProblem | null;
	/** One row per sign-in provider from the house's `oauth.status`; null when it did not answer. */
	accounts: AccountRow[] | null;
}

export interface SettingsRows {
	core: SettingRow[];
	hermes: SettingsListItem[];
	/** jarvis-core's rows could not be read: `core` is empty because of that, not because there are none. */
	coreFailed: boolean;
}

export interface ModelRows {
	toolsets: Toolset[];
	lists: ToolsetLists;
}

export interface TaskDetail {
	task: TaskRow | null;
	log: LogEntry[];
	result: CodeResult | null;
}

/**
 * How long a panel waits for its rows before it says so. A house command that never answered
 * left Telemetry, House and Integrations on their skeletons for good (T-0321: one slow command
 * held the socket's queue); past this the panel is the error state, with its Retry.
 */
const LOAD_DEADLINE_MS = 5000;

class TookTooLong extends Error {}

function withDeadline<T>(work: Promise<T>, ms: number): Promise<T> {
	let timer: ReturnType<typeof setTimeout> | undefined;
	const late = new Promise<never>((_, reject) => {
		timer = setTimeout(() => reject(new TookTooLong(`No answer in ${Math.round(ms / 1000)} s. Retry, or check that jarvis-core is running.`)), ms);
	});
	return Promise.race([work, late]).finally(() => clearTimeout(timer));
}

/** A part of a panel it can do without: its fallback on failure, or once `ms` has passed. */
async function optional<T>(fn: () => Promise<T>, fallback: T, ms = LOAD_DEADLINE_MS): Promise<T> {
	try {
		return (await withDeadline(fn(), ms)) ?? fallback;
	} catch {
		return fallback;
	}
}

/**
 * The outside catalogue reads registries on the internet; a house with no route out should
 * not hold the installed tools back behind it, so it gets a short wait. Unlike `optional()`
 * it keeps why the rows are missing: an empty catalogue with nothing saying why was what a
 * house with no route out drew (T-0323).
 */
const CATALOGUE_WAIT_MS = 1500;
/**
 * A person's Retry waits longer: jarvis-core's registry client gives a connection 3 s
 * (`CONNECT_S`), so at the plain wait every Retry on a house whose egress is dropping ended in
 * "took too long" and never showed the registry errors it had asked for (T-0329). Under
 * LOAD_DEADLINE_MS, which bounds the whole load.
 */
const CATALOGUE_REFRESH_WAIT_MS = 4500;

type BrowseReply = { entries?: CatalogueRow[]; errors?: { source: string; error: string }[]; error?: string };

async function browseCatalogue(client: Connection['client'], shown: CatalogueRow[] = [], refresh = false): Promise<{ catalogue: CatalogueRow[]; catalogueProblem: CatalogueProblem | null }> {
	try {
		// `refresh`: jarvis-core remembers a registry failure for 30 s; a person's Retry must not get that back.
		const reply = await withDeadline(client.command<BrowseReply>({ type: 'jarvis/extensions/browse', ...(refresh ? { refresh: true } : {}) }), refresh ? CATALOGUE_REFRESH_WAIT_MS : CATALOGUE_WAIT_MS);
		const sources = reply?.errors ?? [];
		const error = reply?.error ?? '';
		return { catalogue: reply?.entries ?? [], catalogueProblem: sources.length || error ? { sources, timedOut: false, error } : null };
	} catch (e) {
		return { catalogue: shown, catalogueProblem: { sources: [], timedOut: e instanceof TookTooLong, error: describeError(e) } };
	}
}

export class Feeds {
	/**
	 * How long after the first of a burst of link events the active feeds are read again.
	 * Telemetry's readings, the sky, Jobs, automations, notes and a task's log have no bus event
	 * of their own: reloading on every event kept them fresh and cost six house round trips per
	 * panel update (T-0324); once per burst keeps them fresh within seconds at a fraction of it.
	 */
	static readonly REFRESH_MS = 4000;
	private refreshTimer: ReturnType<typeof setTimeout> | null = null;
	/** The next integrations load is a person's Retry: its browse skips jarvis-core's cached registry errors. */
	private refreshCatalogue = false;
	/** The Catalogue's Retry is out: its button says so. Not `integrations.reloading`, which is any load, the panel's own included. */
	retrying = $state(false);
	readonly tasks = new Feed<TaskRow[]>('tasks', [], async ({ conn }) => conn!.client.listTasks(), listEmpty);
	readonly automations = new Feed<AutomationRow[]>('automations', [], async ({ conn }) => conn!.client.listAutomations(), listEmpty);
	readonly scheduled = new Feed<ScheduledJob[]>('scheduled', [], async ({ conn }) => conn!.client.listScheduled(), listEmpty);
	readonly cron = new Feed<CronJob[]>(
		'cron',
		[],
		async () => {
			const body = await hermesJson<{ jobs?: CronJob[] } | CronJob[]>('/hermes/api/jobs');
			return Array.isArray(body) ? body : (body.jobs ?? []);
		},
		listEmpty,
		'none'
	);
	readonly memory = new Feed<MemoryEntry[]>(
		'memory',
		[],
		async ({ conn }) => (await conn!.client.command<{ entries?: MemoryEntry[] }>({ type: 'jarvis/memory/list', query: '' })).entries ?? [],
		listEmpty
	);
	/**
	 * Hermes' own memory, for the orb's mid field.
	 *
	 * Not the same store as `memory` above. That one is jarvis-core's memory
	 * integration; this is the SOUL/USER/MEMORY layers, the structured fact
	 * store, the skills library and recent session history, which is what the
	 * scene brief asks the graph to be built from. The adapter reads them off
	 * the Hermes home it already mounts, so this is one HTTP read rather than an
	 * agent turn.
	 */
	readonly memoryLayers = new Feed<MemoryEntry[]>(
		'memoryLayers',
		[],
		async () => (await hermesJson<{ entries?: MemoryEntry[] }>('/hermes/api/memory/layers')).entries ?? [],
		listEmpty,
		'none'
	);
	readonly notes = new Feed<NoteRow[]>(
		'notes',
		[],
		async ({ conn }) => (await conn!.client.command<{ notes?: NoteRow[] }>({ type: 'jarvis/notes/list', query: '' })).notes ?? [],
		listEmpty
	);
	readonly readings = new Feed<ReadingsPayload>('readings', { readings: [], configured: false }, async ({ conn }) => conn!.client.sensorReadings(), (p) => p.readings.length === 0);
	readonly sky = new Feed<SkySummary | null>('sky', null, async ({ conn }) => optional(() => conn!.client.skySummary(), null));
	readonly moments = new Feed<MomentRow[]>('moments', [], async ({ conn }) => conn!.client.listMoments(20), listEmpty);
	readonly pending = new Feed<PendingApproval[]>('pending', [], async ({ conn }) => {
		// A house that predates the phone relay answers the command with an error; its own
		// held requests still list.
		const [held, phones] = await Promise.all([conn!.client.pendingApprovals(), optional(() => conn!.client.phoneConsents(), [])]);
		return [...held, ...phones];
	});
	readonly integrations: Feed<IntegrationRows> = new Feed<IntegrationRows>(
		'integrations',
		{ tools: [], skills: [], hermesSkills: [], mcp: [], extensions: [], catalogue: [], catalogueProblem: null, accounts: null },
		async ({ conn }): Promise<IntegrationRows> => {
			const client = conn!.client;
			const refresh = this.refreshCatalogue;
			this.refreshCatalogue = false;
			const [tools, skills, mcp, extensions, browsed, hermesSkills, accounts] = await Promise.all([
				optional(() => client.listToolRows(), [] as ToolRow[]),
				optional(async () => (await client.listSkills()).skills, [] as Skill[]),
				optional(async () => (await client.listMcpServers()).servers, [] as McpListing['servers']),
				optional(async () => (await client.command<{ extensions?: ExtensionRow[] }>({ type: 'jarvis/extensions/list' })).extensions ?? [], [] as ExtensionRow[]),
				// What is on screen stays on screen when a later browse fails (after an install, say).
				browseCatalogue(client, this.integrations.data?.catalogue ?? [], refresh),
				optional(async () => (await hermesJson<{ data?: HermesSkill[] } | HermesSkill[]>('/hermes/v1/skills')), [] as HermesSkill[]).then((b) => (Array.isArray(b) ? b : (b.data ?? []))),
				// Read-only and tokenless (`Connectors.status`); a house without it costs the Accounts section only.
				optional(async () => accountsView(await client.callService('oauth', 'status', {}, { returnResponse: true })), null)
			]);
			return { tools, skills, hermesSkills, mcp, extensions, accounts, ...browsed };
		},
		// Not empty while the catalogue has a problem: "Nothing installed" would hide the line that says why.
		(r) => !r.tools.length && !r.skills.length && !r.mcp.length && !r.extensions.length && !r.catalogue.length && !r.hermesSkills.length && !r.catalogueProblem
	);
	readonly settings = new Feed<SettingsRows>(
		'settings',
		{ core: [], hermes: [], coreFailed: false },
		async ({ conn, hermes }) => {
			// The Hermes catalogue IS the menu, so its failure is the menu's: a
			// `jsettings list` that throws is the error state with its Retry, not
			// "ready, empty" — which is what `optional()` made of a plugin that was
			// missing from the image for a week. jarvis-core's own rows stay
			// optional: they are the assistant's name and the house's time zone,
			// and a house that is down or older than the command is a shorter
			// menu, not a broken one. But the miss is recorded, because a shorter
			// menu with nothing saying so is indistinguishable from a complete
			// one: `null` here is "could not read", never "read, and there are none".
			const [core, list] = await Promise.all([
				conn ? optional<SettingRow[] | null>(async () => (await conn.client.listSettings()).settings, null) : null,
				hermes!.settingsList()
			]);
			return { core: core ?? [], hermes: list, coreFailed: core === null };
		},
		(r) => r.hermes.length === 0 && r.core.length === 0,
		// Needs the Hermes link, not the house socket: with it down the rows on
		// screen are the last known state and the menu says offline.
		'hermes'
	);
	readonly model = new Feed<ModelRows>(
		'model',
		{ toolsets: [], lists: { enabled: [], disabled: [] } },
		async ({ hermes }) => {
			const body = await hermesJson<{ data?: Toolset[] } | Toolset[]>('/hermes/v1/toolsets');
			const toolsets = Array.isArray(body) ? body : (body.data ?? []);
			const lists: ToolsetLists = { enabled: [], disabled: [] };
			if (hermes) {
				// Not `optional()`: a `get` that fails would draw every switch at
				// the roster default, which is a wrong answer rather than a missing
				// one. Thrown, it is the panel's error state, which has a Retry.
				const [on, off] = await Promise.all([hermes.settingsGet('tools.enabled'), hermes.settingsGet('tools.disabled')]);
				lists.enabled = asStringList(on.value);
				lists.disabled = asStringList(off.value);
			}
			return { toolsets, lists };
		},
		(r) => r.toolsets.length === 0,
		'none'
	);
	// Never "empty": the panel is also where a model is found and downloaded, so no models
	// is its own line in the body, not a state that would hide the search.
	readonly models = new Feed<ModelsData>('models', EMPTY_MODELS, () => loadModels(), () => false, 'none');
	readonly speaker = new Feed<SpeakerStatus | null>(
		'speaker',
		null,
		async () => {
			const res = await fetch('/api/voice/speaker');
			if (!res.ok) throw new Error(`The console answered ${res.status}.`);
			return (await res.json()) as SpeakerStatus;
		},
		() => false,
		'none'
	);
	// The v2 screens' own reads: the owner's mirrored texts (a plain fetch through the
	// console's origin, like the speaker), the capability levels (`/hud capabilities`)
	// and the paired phones and desktops.
	// ponytail: the newest 100 texts across every conversation; a per-thread read when a thread outgrows it.
	readonly messages = new Feed<Messages>(
		'messages',
		{ conversations: [], messages: [] },
		async () => {
			const res = await fetch('/api/messages?limit=100');
			if (!res.ok) throw new Error(`The console answered ${res.status}.`);
			return parseMessages(await res.json());
		},
		(m) => !m.conversations.length && !m.messages.length,
		'none'
	);
	readonly capabilities = new Feed<Capability[]>(
		'capabilities',
		[],
		async ({ hermes }) => {
			const outcome = await hermes!.dispatch('hud', 'capabilities');
			if (outcome.kind === 'error') throw new Error(outcome.message);
			if (outcome.kind !== 'output') throw new Error('The capability levels did not come back.');
			return parseCapabilities(outcome.output);
		},
		listEmpty,
		'hermes'
	);
	readonly companions = new Feed<CompanionDevice[]>('companions', [], async ({ conn }) => conn!.client.listCompanions(), listEmpty);
	/**
	 * The house's stored tokens (T-1386), a feed of their own: folded into `integrations` as an
	 * optional read, a list that failed would draw as "no devices" over live credentials.
	 */
	readonly tokens = new Feed<TokenRow[]>(
		'tokens',
		[],
		async ({ conn }) => {
			const rows = tokensView(await conn!.client.listTokens());
			if (!rows) throw new Error('The house did not send its token list.');
			return rows;
		},
		listEmpty
	);
	/** One task, its log and — for a coding job — its result, keyed by the id the route names. */
	readonly task = new Feed<TaskDetail>('task', { task: null, log: [], result: null }, async ({ conn }) => {
		const client = conn!.client;
		const id = this.taskId;
		if (!id) return { task: null, log: [], result: null };
		const [task, log, result] = await Promise.all([client.getTask(id), optional(() => client.taskLog(id), [] as LogEntry[]), optional(() => client.getCodeResult(id), null)]);
		return { task, log, result };
	});
	/** The note open in the reader, by id; the list is `notes`. */
	readonly note = new Feed<NoteRow | null>('note', null, async ({ conn }) => {
		const id = this.noteId;
		if (!id) return null;
		return (await conn!.client.command<{ note?: NoteRow }>({ type: 'jarvis/notes/get', note_id: id })).note ?? null;
	});

	/** The task the task panel shows; a rune so the panel's title follows it. */
	taskId = $state('');
	/** The note the reader shows. */
	noteId = $state('');

	// `any`, not `unknown`: a feed's `patch` takes and returns its own T, which
	// makes the class invariant, and a registry of feeds of different shapes
	// can only be a registry of `Feed<any>`.
	private active = new Set<Feed<any>>();
	private subs: Subscription[] = [];
	private sources: Sources = { conn: null, hermes: null };

	/** Which feeds a panel type reads. */
	feedsFor(type: string): Feed<any>[] {
		const f = this as unknown as Record<string, Feed<any>>;
		switch (type) {
			case 'kanban':
				return [f.tasks];
			case 'task':
				return [f.task];
			case 'automations':
				return [f.automations];
			case 'jobs':
				return [f.scheduled, f.cron];
			case 'memory':
				return [f.memory, f.notes, f.memoryLayers];
			case 'document':
				return [f.notes, f.note];
			case 'telemetry':
				return [f.readings, f.sky];
			case 'notifications':
				return [f.moments];
			case 'approvals':
				return [f.pending];
			case 'integrations':
				return [f.integrations, f.tokens];
			case 'settings':
				return [f.settings];
			case 'model':
				return [f.model];
			case 'voice':
				return [f.speaker];
			case 'models':
				return [f.models];
			case 'messages':
				return [f.messages];
			case 'capabilities':
				return [f.capabilities];
			case 'devices':
				return [f.companions];
			default:
				return [];
		}
	}

	/** Load what the layout's panel types read, and drop what none of them do. */
	activate(types: readonly string[]): void {
		const wanted = new Set<Feed<any>>();
		for (const type of types) for (const feed of this.feedsFor(type)) wanted.add(feed);
		for (const feed of wanted) {
			if (!this.active.has(feed)) void feed.load(this.sources);
		}
		this.active = wanted;
	}

	/** Run a tool on the house socket, or null while it is down: the connection is the feeds' own, so a screen asks here. */
	toolCaller(): ((name: string, args: Record<string, unknown>) => Promise<unknown>) | null {
		const client = this.sources.conn?.client;
		return client ? (name, args) => client.callTool(name, args) : null;
	}

	/** Set whether a paired device may answer held requests. Rejects when the house refuses or is not connected. */
	setTokenApprover(id: string, approver: boolean): Promise<void> {
		return this.changeToken((client) => client.updateToken(id, approver));
	}

	/** Revoke one stored token; the house hangs up whatever holds it open. Cannot be undone: the screen asks first. */
	revokeToken(id: string): Promise<void> {
		return this.changeToken((client) => client.revokeToken(id));
	}

	/**
	 * One change to a token, then the list again whether it landed or not: the row shows what the
	 * house holds, so a refused or half-done change never leaves the switch saying what was asked.
	 */
	private async changeToken(change: (client: Connection['client']) => Promise<unknown>): Promise<void> {
		const client = this.sources.conn?.client;
		if (!client) throw new Error('The house is not connected.');
		try {
			await change(client);
		} finally {
			await this.tokens.load(this.sources);
		}
	}

	/** The links changed: reload every active feed on a link that came up, mark offline on one that went. */
	setSources(sources: Sources): void {
		const before = this.sources;
		// Only a link that came or went is news. The host calls this from an effect that re-runs
		// on every Hermes event (the link's snapshot is new each time), and reloading every active
		// feed on each was six house round trips per panel update and a re-render of every panel
		// per answer (T-0324: the hud-frametime regression since 3344eed8).
		if (before.conn === sources.conn && before.hermes === sources.hermes) {
			this.refreshSoon();
			return;
		}
		this.sources = sources;
		if (before.conn !== sources.conn) {
			this.unsubscribe();
			if (sources.conn) void this.subscribe(sources.conn);
		}
		for (const feed of this.active) {
			const up = feed.needs === 'conn' ? Boolean(sources.conn) : feed.needs === 'hermes' ? Boolean(sources.hermes) : true;
			if (!up) feed.dropped();
			else if (feed.needs !== 'none' || feed.status === 'offline') void feed.load(sources);
		}
	}

	/** One trailing reload of the active feeds whose link is up, REFRESH_MS after the first ask. */
	private refreshSoon(): void {
		if (this.refreshTimer) return;
		this.refreshTimer = setTimeout(() => {
			this.refreshTimer = null;
			for (const feed of this.active) {
				// Settings reload on their own change events (the house's `jarvis_setting_changed`,
				// Hermes's `settings.changed`): re-reading both whole lists here was most of the car
				// page's traffic (T-0746). One that never loaded, or whose last load failed (no session
				// yet, the deadline), is retried here like the rest: no event would ever reload it.
				if (feed === this.settings && feed.loaded && feed.status !== 'error') continue;
				const up = feed.needs === 'conn' ? Boolean(this.sources.conn) : feed.needs === 'hermes' ? Boolean(this.sources.hermes) : false;
				// A load already out is the read this one would be: starting another supersedes it
				// (a Retry's rows were dropped for a plain browse that got jarvis-core's cached error).
				if (up && !feed.reloading) void feed.load(this.sources);
			}
		}, Feeds.REFRESH_MS);
	}

	/** Retry: the error state's verb, and the offline state's once the link is back. */
	reload<T>(feed: Feed<T>): void {
		void feed.load(this.sources);
	}

	/** The Catalogue's Retry: a reload whose browse reads failed registries again. */
	async retryCatalogue(): Promise<void> {
		// No house to ask: `load` marks the feed offline. The flag stays down, or the reload that
		// follows the reconnect would carry a refresh nobody asked for.
		if (!this.sources.conn) return this.integrations.load(this.sources);
		this.refreshCatalogue = true;
		this.retrying = true;
		try {
			await this.integrations.load(this.sources);
		} finally {
			this.retrying = false;
		}
	}

	reloadAll(): void {
		for (const feed of this.active) void feed.load(this.sources);
	}

	/** Open one task in the task panel. */
	openTask(id: string): void {
		if (this.taskId === id) return;
		this.taskId = id;
		this.task.reset();
		if (this.active.has(this.task)) void this.task.load(this.sources);
	}

	openNote(id: string): void {
		if (this.noteId === id) return;
		this.noteId = id;
		this.note.reset();
		if (this.active.has(this.note)) void this.note.load(this.sources);
	}

	/**
	 * The bus events that change what a panel shows, folded in without a round
	 * trip where the event carries the row (tasks) and by a reload where it
	 * does not (approvals, moments, the schedule, memory). Registries and
	 * states are the `ConsoleLink`'s own subscription.
	 */
	private async subscribe(conn: Connection): Promise<void> {
		const client = conn.client;
		const add = async (name: string, handler: (event: { event_type: string; data: any }) => void) => {
			try {
				this.subs.push(await client.subscribeEvents(handler as never, name));
			} catch {
				// A backend without this event still gives a usable panel, one that
				// refreshes on Retry rather than by itself.
			}
		};
		for (const name of [EVENT_TASK_ADDED, EVENT_TASK_UPDATED, EVENT_TASK_REMOVED]) {
			await add(name, (event) => this.tasks.patch((rows) => applyTaskEvent(rows, event as never)));
		}
		await add('jarvis_approval_required', () => this.reload(this.pending));
		await add('jarvis_approval_expired', () => this.reload(this.pending));
		await add('jarvis_phone_consent', () => this.reload(this.pending));
		await add('jarvis_notification', () => this.reload(this.moments));
		await add('memory_changed', () => {
			this.reload(this.memory);
			// The layers change on the same event: an agent that writes a fact
			// writes it to the store the orb is drawing.
			this.reload(this.memoryLayers);
		});
		await add('jarvis_setting_changed', () => this.reload(this.settings));
	}

	private unsubscribe(): void {
		for (const sub of this.subs.splice(0)) void sub.unsubscribe();
	}

	dispose(): void {
		this.unsubscribe();
		if (this.refreshTimer) clearTimeout(this.refreshTimer);
		this.refreshTimer = null;
		this.active.clear();
	}
}
