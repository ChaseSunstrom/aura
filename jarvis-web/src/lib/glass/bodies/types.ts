// The shapes the panel bodies render (GLASS_HUD §9.1).
//
// One module rather than a `Props` interface per body, because the same row
// travels through several of them — a tool call is a timeline row and a task's
// timeline, a notice is a notification and an audit line — and two spellings of
// the same record is how a body ends up rendering a field the producer stopped
// sending.
//
// These are view shapes, not wire shapes. The adapter's payloads
// (`tests/contracts/hud-panels.json`) are mapped onto them by the window
// manager in wave 3; the bodies take what they draw and nothing else, so a
// contract change lands in one mapping instead of in fifteen components.

/** Everything that reports a run state uses this vocabulary. */
export type RunState = 'queued' | 'running' | 'held' | 'done' | 'failed';

export interface ChatTurn {
	id: string;
	role: 'you' | 'jarvis';
	/** Who spoke a reply, as the row is labelled: the persona's name, lower-case. Absent reads as the role. */
	who?: string;
	/** Markdown for Jarvis, plain text for a person. */
	text: string;
	/** The reasoning fold's contents; absent means there was none. */
	reasoning?: string;
	/** Still arriving: the last turn while tokens stream. */
	streaming?: boolean;
	at?: string;
}

export interface TimelineCall {
	id: string;
	name: string;
	args?: string;
	/** What the call does, in plain words (`stepOf`); '' for a call that only presents. */
	step?: string;
	state: 'running' | 'ok' | 'failed';
	error?: string;
	ms?: number | null;
	/** Absolute time, per §11: timeline rows carry the clock, copy carries "ago". */
	at?: string;
}

export interface ApprovalRequest {
	id: string;
	/** What is being asked for, in words a person can answer. */
	title: string;
	detail?: string;
	/** A clarify request asks a question; an approval asks for a decision. */
	kind: 'approval' | 'clarify';
	tool?: string;
	at?: string;
}

export interface GoalGate {
	id: string;
	label: string;
	state: 'open' | 'passed' | 'failed';
}

export interface Goal {
	id: string;
	/** The standing goal, in the person's words. */
	title: string;
	/** The contract it is being judged against. */
	contract?: string;
	turn?: number;
	turns?: number;
	subgoals?: { id: string; label: string; state: RunState }[];
	gates?: GoalGate[];
	/** Why the judge held or failed it. */
	reason?: string;
	state: RunState;
}

export interface KanbanTask {
	id: string;
	title: string;
	state: RunState;
	detail?: string;
}

export interface MemoryNote {
	id: string;
	title: string;
	body: string;
	/** Where it came from: a conversation, a reflection, a person. */
	source?: string;
	at?: string;
}

export interface Toolset {
	id: string;
	label: string;
	enabled: boolean;
	tools: number;
}

export interface AuditEntry {
	id: string;
	at: string;
	/** The internal name; the audit panel is the one place they appear (§11). */
	actor: string;
	action: string;
	detail?: string;
	outcome: 'ok' | 'refused' | 'failed';
}

export interface TelemetryReading {
	id: string;
	label: string;
	value: number | null;
	/** A state that is a word rather than a figure ("On", a model's name); shown in its place. */
	text?: string;
	unit?: string;
	/** 0–100 for a gauge; omit for a bare figure. */
	percent?: number | null;
	/** A chart series' points, oldest first: drawn as a trend line under the row. */
	points?: number[];
	/** A table's middle columns, "Header value · …": the row's second line. */
	sub?: string;
}

export interface Notice {
	id: string;
	title: string;
	body?: string;
	level: 'info' | 'ok' | 'attention' | 'danger';
	at?: string;
}

export interface Job {
	id: string;
	title: string;
	/** Relative in copy (§11): "in 4 minutes". */
	next?: string;
	last?: string;
	state: 'running' | 'paused' | 'idle';
}

export interface Device {
	id: string;
	name: string;
	area?: string;
	/** The state word a person understands: "on", "21 °C", "closed". */
	state: string;
	on?: boolean;
}

export interface Automation {
	id: string;
	title: string;
	/** What sets it off, in a sentence. */
	when: string;
	enabled: boolean;
	surface: 'house' | 'phone' | 'policy';
}

export interface Integration {
	id: string;
	name: string;
	kind: 'plugin' | 'skill' | 'mcp' | 'extension' | 'catalogue';
	detail?: string;
	installed: boolean;
}

/**
 * Why the catalogue is short or empty (`jarvis/extensions/browse`): the registries that could
 * not be read, a browse that gave no answer inside its wait, or a reason that names no
 * registry. Does not say whether the rows that did load are complete.
 */
export interface CatalogueProblem {
	sources: { source: string; error: string }[];
	timedOut: boolean;
	error: string;
}

export interface TaskStep {
	id: string;
	label: string;
	state: RunState;
}

/** One setting, exactly as `tests/contracts/hermes-settings.json` describes it. */
export interface SettingSpec {
	key: string;
	group: string;
	widget: string;
	description: string;
	secret?: boolean;
	restart_needed?: boolean;
	settable?: boolean;
	advanced?: boolean;
	/**
	 * Which tier of the settings UI this row belongs to (ADR-0035). It comes from
	 * the schema, so a setting cannot exist and be invisible in all three; a row
	 * with none is `advanced`, which is the schema's own default.
	 */
	tier?: 'basic' | 'advanced' | 'expert';
	/**
	 * The schema's own default, so reset-to-default cannot go stale: it is the
	 * value the generator emitted, not a copy somebody typed here (ADR-0035).
	 */
	fallback?: string | boolean;
	options?: string[];
}

export interface SettingGroup {
	id: string;
	title: string;
	description?: string;
}
