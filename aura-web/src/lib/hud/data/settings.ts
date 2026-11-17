// `panel.settings`, generated from the settings schema.
//
// The catalogue is `tests/contracts/hermes-settings.json` — aura-schema's
// output, the same file the phone and the `aura-settings` plugin read — so a
// key added to the schema appears here on the next build with no hand-written
// row. It is imported at build time: the console does not fetch contracts, and
// a settings screen that could not draw until a second request answered would
// be a screen with a loading state for a file that never changes at runtime.
//
// Values come from two stores and writes go back to the one each key lives
// in: every schema leaf is served by the `aura-settings` plugin (`jsettings`
// — which answers each row's value, where it comes from and when a write
// applies), and jarvis-core's own live settings (`jarvis/settings/*`), which
// are not in the schema and are appended as rows of their own group so the
// assistant's name and the house's time zone do not vanish with the screens
// that showed them. jarvis-core's environment store is no longer a target: it
// knows only its own `.env.example` names and refused 147 of the 162 env-backed
// leaves the old routing sent it.

// The in-package copy aura-schema generates beside the canonical
// tests/contracts/hermes-settings.json: the web image is built from aura-web/
// alone, and an import reaching up to tests/ builds on a checkout and fails in
// the image. `make schema-check` refuses the two drifting apart.
import { groups as contractGroups, settings as contractSettings } from './hermes-settings.json';
import type { SettingsApplies, SettingsListItem, SettingsSource } from '$lib/hermes/settings';
import type { SettingRow } from '$lib/jarvisClient';
import type { SettingGroup, SettingSpec } from '$lib/glass';
import { modelOptions } from '../chatScreen';
import type { ApiModel } from './models';

/**
 * One row of jarvis-core's `jarvis/environment/list`. The console no longer
 * writes there (see the header), but the mock house still serves it and
 * `mocks.test.ts` pins the shape.
 */
export interface EnvVariable {
	name: string;
	secret: boolean;
	/** The console's own override; null when there is none. Masked for a secret. */
	value: string | null;
	/** What the container runs with; null when unset. Masked for a secret. */
	live: string | null;
	pending?: boolean;
}

/** A schema row with the fields the panel routes writes and validates by. */
export interface CatalogueSpec extends SettingSpec {
	/** The schema's kind: `string`/`int`/`float`/`bool`/`enum`/`list`/`object`. */
	type?: string;
	min?: number;
	max?: number;
	/** When a write lands (from the schema; the live row may say the same). */
	applies?: SettingsApplies;
	/** jarvis-core's own setting, not in the schema. */
	coreKey?: string;
}

interface ContractSetting {
	key: string;
	type?: string;
	group: string;
	widget: string;
	description: string;
	secret?: boolean;
	restart_needed?: boolean;
	settable?: boolean;
	advanced?: boolean;
	tier?: 'basic' | 'advanced' | 'expert';
	applies?: SettingsApplies;
	default?: unknown;
	env?: string;
	hermes_key?: string;
	choices?: string[];
	min?: number;
	max?: number;
}

export const CORE_PREFIX = 'core:';

function fromContract(s: ContractSetting): CatalogueSpec {
	const spec: CatalogueSpec = {
		key: s.key,
		group: s.group,
		widget: s.widget,
		description: s.description,
		secret: s.secret === true,
		restart_needed: s.restart_needed === true,
		// Settable here means "the plugin will take a write": every leaf but the
		// ones the compose file fixes on the host (a port bind, an image tag).
		// The contract's own `settable` flag is the agent tool's allow-list — the
		// 14 contract keys — and reading it as the panel's would grey out the
		// hundred env-backed keys the plugin now writes through its override file.
		settable: s.applies !== 'compose',
		advanced: s.advanced === true,
		// The schema's own tier. A row without one is `advanced`, which is what the
		// schema defaults to — a setting is never in no tier at all.
		tier: s.tier ?? 'advanced'
	};
	if (s.type) spec.type = s.type;
	if (s.applies) spec.applies = s.applies;
	if (typeof s.min === 'number') spec.min = s.min;
	if (typeof s.max === 'number') spec.max = s.max;
	// The schema's default, as the widget would show it. An **empty string is a
	// real default** — an optional URL's default is nothing — and resetting to it
	// is correct; the control simply does not appear when the value already
	// matches. `null` and `undefined` mean the schema offers no default, and an
	// object default (a managed mapping) is not something a button can restore.
	// A list default is its comma-joined spelling, the value the `multi` widget edits.
	if (typeof s.default === 'boolean') spec.fallback = s.default;
	else if (Array.isArray(s.default)) spec.fallback = s.default.map(String).join(',');
	else if (s.default !== null && s.default !== undefined && typeof s.default !== 'object')
		spec.fallback = String(s.default);
	if (s.choices?.length) spec.options = s.choices;
	return spec;
}

function widgetOfCore(row: SettingRow): string {
	switch (row.type) {
		case 'boolean':
			return 'toggle';
		case 'choice':
			return 'select';
		case 'number':
		case 'integer':
			return 'number';
		default:
			return 'text';
	}
}

/** jarvis-core's live settings as rows the same body draws. */
export function coreSpecs(rows: readonly SettingRow[]): CatalogueSpec[] {
	return rows.map((row) => {
		const spec: CatalogueSpec = {
			key: `${CORE_PREFIX}${row.key}`,
			group: row.group,
			widget: widgetOfCore(row),
			description: row.note ? `${row.label}. ${row.note}` : row.label,
			restart_needed: row.apply === 'restart',
			settable: true,
			// jarvis-core's own rows are not in the v3 schema, so they have no tier
			// of their own: they are advanced, which is where an uncategorised
			// setting belongs rather than on the first screen.
			tier: 'advanced',
			applies: row.apply === 'restart' ? 'restart' : 'now',
			coreKey: row.key
		};
		if (row.choices?.length) spec.options = row.choices;
		return spec;
	});
}

export interface SettingsCatalogue {
	groups: SettingGroup[];
	specs: CatalogueSpec[];
}

/** The schema's groups and rows, plus jarvis-core's own, in one catalogue. */
export function settingsCatalogue(core: readonly SettingRow[] = []): SettingsCatalogue {
	const groups: SettingGroup[] = contractGroups.map((g) => ({ id: g.id, title: g.title, description: g.description }));
	const specs = [...(contractSettings as ContractSetting[]).map(fromContract), ...coreSpecs(core)];
	// A core group the schema does not name still needs a heading, or its rows
	// render under nothing and the body drops them.
	for (const spec of specs) {
		if (!groups.some((g) => g.id === spec.group)) groups.push({ id: spec.group, title: spec.group });
	}
	return { groups, specs };
}

const asText = (value: unknown): string =>
	value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);

const asBool = (value: unknown): boolean =>
	value === true || (typeof value === 'string' && /^(1|true|yes|on)$/i.test(value.trim()));

/**
 * The default model's row as a select of the models the manager serves (the owner, 2026-10-31:
 * "I still can't select models, only type them"). The row's own value stays a choice, so a model
 * the manager does not list is never silently swapped; with no list the row stays a text box.
 * Does not cover the `routing.*_model` rows, whose usual value is '' (use the default).
 */
export function withModelChoices(
	specs: readonly CatalogueSpec[],
	models: readonly Pick<ApiModel, 'id' | 'name' | 'state'>[],
	current: string
): CatalogueSpec[] {
	const ids = modelOptions(models, current).map((o) => o.value);
	if (!models.length || !ids.length) return [...specs];
	return specs.map((s) => (s.key === 'model.default' ? { ...s, widget: 'select', options: ids } : s));
}

export interface SettingsSources {
	hermes?: readonly SettingsListItem[];
	core?: readonly SettingRow[];
	/** jarvis-core's rows could not be read, so `core` is short of them. */
	coreFailed?: boolean;
}

/**
 * The line the menu prints above its rows when a source failed, or '' when
 * every source answered. jarvis-core's rows stay optional in the feed so a
 * house that is down does not blank the menu; this is what keeps that from
 * being a shorter menu that looks complete.
 */
export function settingsNotice(sources: SettingsSources): string {
	return sources.coreFailed ? "jarvis-core's own settings could not be read, so the rows it owns are missing here." : '';
}

/** The current value per key, from whichever store holds it. */
export function settingsValues(specs: readonly CatalogueSpec[], sources: SettingsSources): Record<string, string | boolean> {
	const hermes = new Map((sources.hermes ?? []).map((s) => [s.key, s.value]));
	const core = new Map((sources.core ?? []).map((r) => [r.key, r.value]));
	const values: Record<string, string | boolean> = {};
	for (const spec of specs) {
		const raw: unknown = spec.coreKey !== undefined ? core.get(spec.coreKey) : hermes.get(spec.key);
		if (raw === undefined) continue;
		// A list is edited as chips over its comma-joined spelling (the shape the
		// gateway itself splits); JSON-stringifying it drew `["web"]` in the box.
		if (spec.widget === 'multi') values[spec.key] = Array.isArray(raw) ? raw.map(String).join(',') : asText(raw);
		else values[spec.key] = spec.widget === 'toggle' ? asBool(raw) : asText(raw);
	}
	return values;
}

/** What a row says beside its value: where it comes from and when a write lands. */
export interface SettingMeta {
	source?: SettingsSource;
	applies?: SettingsApplies;
	/** The live row's answer; the catalogue's `settable` is the fallback before the list arrives. */
	settable?: boolean;
}

/** Per key, the live row's `source`/`applies`/`settable`; jarvis-core's rows know only `applies`. */
export function settingsMeta(specs: readonly CatalogueSpec[], sources: SettingsSources): Record<string, SettingMeta> {
	const hermes = new Map((sources.hermes ?? []).map((s) => [s.key, s]));
	const meta: Record<string, SettingMeta> = {};
	for (const spec of specs) {
		if (spec.coreKey !== undefined) {
			meta[spec.key] = { applies: spec.applies };
			continue;
		}
		const row = hermes.get(spec.key);
		if (!row) continue;
		meta[spec.key] = { source: row.source, applies: row.applies ?? spec.applies, settable: row.settable };
	}
	return meta;
}

/** The catalogue's words for a source and an `applies`, as the row prints them. */
export const SOURCE_WORDS: Readonly<Record<SettingsSource, string>> = {
	default: 'default',
	config: 'config.yaml',
	env: 'environment',
	override: 'override'
};
export const APPLIES_WORDS: Readonly<Record<SettingsApplies, string>> = {
	now: 'applies now',
	restart: 'after a restart',
	compose: 'compose file, read-only'
};

/** Where a write for a key goes. Null for a row nothing will take. */
export type SettingWrite =
	| { target: 'hermes'; key: string; value: unknown }
	| { target: 'core'; key: string; value: unknown };

/** The typed JSON value the plugin's strict `coerce` accepts for this row. */
export function typedValue(spec: CatalogueSpec, value: string | boolean): unknown {
	if (spec.widget === 'toggle' || spec.type === 'bool') return typeof value === 'boolean' ? value : asBool(value);
	if (typeof value !== 'string') return value;
	if (spec.type === 'int' || spec.type === 'float') return value.trim() === '' ? value : Number(value);
	if (spec.type === 'list' || spec.widget === 'multi') return value.split(',').map((v) => v.trim()).filter(Boolean);
	if (spec.type === 'object') {
		try {
			return JSON.parse(value);
		} catch {
			return value;
		}
	}
	return value;
}

export function settingWrite(spec: CatalogueSpec, value: string | boolean): SettingWrite | null {
	if (spec.coreKey !== undefined) return { target: 'core', key: spec.coreKey, value };
	// A leaf the compose file fixes has no write path from inside the container;
	// the plugin would answer "fixed outside the container", and the row is
	// drawn disabled so nobody types into it.
	if (spec.settable === false) return null;
	return { target: 'hermes', key: spec.key, value: typedValue(spec, value) };
}

const URL_SCHEMES = new Set(['http:', 'https:', 'ws:', 'wss:']);

/**
 * What the plugin would refuse, said before the request: a message, or null
 * when the value fits. The rules are the schema's own (`coerce`: kind, bounds,
 * choices) plus the two shapes the widgets promise — a `url` parses as one and
 * a `path` is absolute — so a typo lands as a red line under the box rather
 * than as a proposal that fails on apply. Not a substitute for the plugin's
 * check: the server validates again on `set` and on `apply`.
 */
export function validate(spec: CatalogueSpec, value: string | boolean): string | null {
	const text = typeof value === 'boolean' ? String(value) : value.trim();
	if (spec.widget === 'toggle' || spec.type === 'bool') {
		return typeof value === 'boolean' || /^(true|false)$/.test(text) ? null : `${spec.key} takes true or false`;
	}
	if (spec.type === 'int' || spec.type === 'float') {
		const whole = spec.type === 'int';
		if (!(whole ? /^-?\d+$/ : /^-?(\d+\.?\d*|\.\d+)$/).test(text)) {
			return `${spec.key} takes ${whole ? 'a whole number' : 'a number'}`;
		}
		const num = Number(text);
		const low = spec.min !== undefined && num < spec.min;
		const high = spec.max !== undefined && num > spec.max;
		if (low || high) {
			if (spec.min !== undefined && spec.max !== undefined) return `${spec.key} must be between ${spec.min} and ${spec.max}`;
			return low ? `${spec.key} must be at least ${spec.min}` : `${spec.key} must be at most ${spec.max}`;
		}
		return null;
	}
	if (spec.options?.length && !spec.options.includes(text)) return `${spec.key} must be one of ${spec.options.join(', ')}`;
	if (spec.widget === 'url' && text) {
		try {
			if (!URL_SCHEMES.has(new URL(text).protocol)) return `${spec.key} must start with http://, https://, ws:// or wss://`;
		} catch {
			return `${spec.key} must be a URL, like http://host:8080`;
		}
	}
	if (spec.widget === 'path' && text && !/^[/~]/.test(text)) return `${spec.key} must be an absolute path`;
	return null;
}
