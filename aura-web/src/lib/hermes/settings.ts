// The `aura-settings` plugin, as the console speaks it.
//
// The gateway's `config.set` knows only `prompt`, `personality` and `display.*`
// (everything else answers `4002 unknown config key`), so the apps read and
// write Hermes's own settings through the `jsettings` slash command:
// `command.dispatch {name:"jsettings", arg:"@<sid> <sub>"}`, answered as a
// `plugin` result whose `output` is a JSON string. This module is the args it
// builds and the output it parses — pure, so it runs under Vitest — while
// `HermesLink` owns the socket. The allow-list and each key's meaning are the
// plugin's table, mirrored in `tests/contracts/hermes-settings.json`.

/** The slash command; its sub-command and the session ride in the arg. */
export const SETTINGS_COMMAND = 'jsettings';

/** `jsettings get <key>`. */
export type SettingsGetResult = SettingsListItem;

/** Where a row's value comes from, in the plugin's words (`docs/hermes-hud-protocol.md`, jsettings). */
export type SettingsSource = 'default' | 'config' | 'env' | 'override';

/** When a written value takes effect: live, at the next container start, or never from here. */
export type SettingsApplies = 'now' | 'restart' | 'compose';

/** `jsettings set <key> <json>` or `apply <id>`, once something was written. */
export interface SettingsSetResult {
	key: string;
	value: unknown;
	previous?: unknown;
	/** Whether the gateway must restart before the value takes effect. */
	restart_needed?: boolean;
	applies?: SettingsApplies;
	source?: SettingsSource;
	/** The proposal `apply` consumed; absent on a write that never waited. */
	proposal?: string;
}

/**
 * A `set` the approval matrix would have asked about, waiting for the person's
 * yes (`jsettings apply <id>`) or no (`discard <id>`). A secret's `before` and
 * `after` are masked on the wire.
 */
export interface SettingsProposal {
	id: string;
	key: string;
	before: unknown;
	after: unknown;
	source: SettingsSource;
	applies: SettingsApplies;
	ts: string;
}

/**
 * One row of `jsettings history`: a change that was made. A secret's two values arrive
 * masked; `undoable` is set on the newest row of a key that `undo` can put back.
 */
export interface SettingsChange {
	ts: string;
	actor: string;
	key: string;
	before: unknown;
	after: unknown;
	undoable: boolean;
}

/** What `set` and `undo` answer: a write, or a proposal when the matrix asks. */
export type SettingsSetOutcome = SettingsSetResult | { status: 'proposed'; proposal: SettingsProposal };

/** One row of `jsettings list`, and what `get` answers. */
export interface SettingsListItem {
	key: string;
	value: unknown;
	type?: string;
	restart_needed?: boolean;
	doc?: string;
	source?: SettingsSource;
	/** False for a leaf the compose file fixes on the host: the plugin will not write it. */
	settable?: boolean;
	applies?: SettingsApplies;
}

/**
 * The plugin refusing a key, as a thrown thing.
 *
 * An unknown or disallowed key is not a transport failure — the socket
 * answered — so it is `{"error": "…"}` inside the `output`, not a JSON-RPC
 * error. The page shows the message rather than a "command failed" it made up.
 */
export class SettingsError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'SettingsError';
	}
}

/**
 * Parse a `plugin` result's `output`, throwing `SettingsError` on `{error}`.
 *
 * Read loosely: an output that is not JSON (an older plugin answering a plain
 * line) is itself the message, not a crash.
 */
export function parseSettingsOutput<T = unknown>(output: string | undefined | null): T {
	const text = String(output ?? '').trim();
	if (!text) throw new SettingsError('the settings plugin returned nothing');
	let parsed: unknown;
	try {
		parsed = JSON.parse(text);
	} catch {
		throw new SettingsError(text);
	}
	if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && 'error' in parsed) {
		throw new SettingsError(String((parsed as { error: unknown }).error ?? 'settings error'));
	}
	return parsed as T;
}

// --- toolsets -----------------------------------------------------------------
//
// A toolset is on for new sessions when it is in `tools.enabled`, off when it is
// in `tools.disabled`, and otherwise whatever the roster (`GET /v1/toolsets`)
// says is on for the current session. Toggling writes both lists so the intent
// is explicit and the two can never disagree about one name.

export interface ToolsetLists {
	enabled: string[];
	disabled: string[];
}

/** A config list value as strings; anything not a list reads as empty. */
export function asStringList(value: unknown): string[] {
	return Array.isArray(value) ? value.map((v) => String(v)) : [];
}

/** Whether a toolset is on: explicit `enabled` wins, then explicit `disabled`, then the roster default. */
export function toolsetOn(name: string, lists: ToolsetLists, rosterDefault: boolean): boolean {
	if (lists.enabled.includes(name)) return true;
	if (lists.disabled.includes(name)) return false;
	return rosterDefault;
}

/**
 * The `enabled`/`disabled` lists after flipping one toolset.
 *
 * A name lands in exactly one list (or neither is impossible once flipped): the
 * old lists are deduped and the opposite membership is dropped, so a name never
 * ends up both enabled and disabled — which the gateway would resolve
 * arbitrarily.
 */
export function withToolset(name: string, on: boolean, lists: ToolsetLists): ToolsetLists {
	const enabled = new Set(lists.enabled);
	const disabled = new Set(lists.disabled);
	if (on) {
		enabled.add(name);
		disabled.delete(name);
	} else {
		disabled.add(name);
		enabled.delete(name);
	}
	return { enabled: [...enabled], disabled: [...disabled] };
}
