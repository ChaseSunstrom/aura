// The settings menu draws no dead controls and says nothing twice.
//
// The owner's requirement for the menu was "all customizeable settings NO
// DUPLICATES OR FALSE IMPLEMENTATIONS". Auditing it found the panel already
// honest — 166 of the schema's 167 rows have somewhere to write and the one
// that does not says it is managed by the stack — so what this file does is
// keep it that way, because "true when someone last looked" is not a property.
//
// A row with no write target is the failure it exists to catch: `settingWrite`
// returns null, `changeSetting` returns early, and typing into the box does
// nothing at all with no error and no hint. That is indistinguishable from a
// setting that works until you restart and find it did not.

import { describe, expect, it } from 'vitest';

import { APPLIES_WORDS, settingsCatalogue, settingsMeta, settingsNotice, settingsValues, settingWrite, SOURCE_WORDS, validate, withModelChoices } from './settings';

const catalogue = settingsCatalogue();
const rows = catalogue.specs;

describe('the settings menu', () => {
	it('has rows to draw', () => {
		expect(rows.length).toBeGreaterThan(100);
	});

	it('gives every row it draws as a control somewhere to write', () => {
		const dead = rows.filter((s) => s.settable && settingWrite(s, 'x') === null);
		expect(dead.map((s) => s.key)).toEqual([]);
	});

	it('draws the rows it cannot write as not settable, so they read as managed', () => {
		// The inverse of the rule above: a row with no target must not be offered
		// as a control. Today that is `memory.provider`, whose widget is `managed`.
		const offered = rows.filter((s) => settingWrite(s, 'x') === null && s.settable);
		expect(offered).toEqual([]);
	});

	it('says nothing twice', () => {
		const keys = rows.map((s) => s.key);
		expect(keys.length).toBe(new Set(keys).size);
		const descriptions = rows.map((s) => s.description.trim().toLowerCase());
		const seen = new Map<string, string>();
		const twice: string[] = [];
		for (const [i, d] of descriptions.entries()) {
			const first = seen.get(d);
			if (first) twice.push(`${first} and ${keys[i]}: ${d}`);
			else seen.set(d, keys[i]);
		}
		expect(twice).toEqual([]);
	});

	it('keeps the advanced rows in the catalogue, for the menu s own switch to reveal', () => {
		// They used to be drawn only on `/settings/system`. With no tabs that is a
		// path nobody types, so the menu reveals them and they must be here to
		// reveal (ADR-0032).
		const advanced = rows.filter((s) => s.advanced);
		expect(advanced.length).toBeGreaterThan(0);
		expect(advanced.every((s) => settingWrite(s, 'x') !== null || !s.settable)).toBe(true);
	});

	it('puts every row under a heading the menu renders', () => {
		const groups = new Set(catalogue.groups.map((g) => g.id));
		const orphans = rows.filter((s) => !groups.has(s.group)).map((s) => s.key);
		expect(orphans).toEqual([]);
	});
});

describe('the three tiers come from the schema (ADR-0035)', () => {
	const specs = rows;

	it('gives every row a tier, so none is invisible in all three', () => {
		expect(specs.filter((s) => !s.tier).map((s) => s.key)).toEqual([]);
		for (const s of specs) expect(['basic', 'advanced', 'expert']).toContain(s.tier);
	});

	it('keeps Basic to the seven the schema names', () => {
		expect(specs.filter((s) => s.tier === 'basic').map((s) => s.key).sort()).toEqual([
			'approvals.mode',
			'hud.reduced_motion',
			'model.default',
			'proactive.quiet_hours_from',
			'tts.engine',
			'tts.voice',
			'voice.push_to_talk'
		]);
	});
});

describe('reset-to-default reads the schema (ADR-0035)', () => {
	it('carries a default for the rows that have one', () => {
		const withDefault = rows.filter((s) => s.fallback !== undefined);
		expect(withDefault.length).toBeGreaterThan(50);
		// `approvals.mode` is one a person changes and one that has an obvious
		// default; if it ever loses one, reset has quietly stopped working.
		expect(rows.find((s) => s.key === 'approvals.mode')?.fallback).toBe('manual');
	});

	it('never offers a default a button could not restore', () => {
		// An empty string is a real default — an optional URL's default is nothing
		// — and resetting to it is correct. An object is not: a managed mapping is
		// not something one control can put back.
		for (const s of rows) {
			if (s.fallback === undefined) continue;
			expect(typeof s.fallback === 'string' || typeof s.fallback === 'boolean').toBe(true);
		}
	});
});

describe('every schema leaf is written through the settings plugin (ADR-0044)', () => {
	// The old routing sent the 162 env-backed leaves to jarvis-core's environment
	// store, which knows only its own `.env.example` names and refused 147 of
	// them. Now the plugin serves every leaf; the compose file's are read-only.
	it('routes every settable schema leaf to jsettings under its schema key', () => {
		const schema = rows.filter((s) => s.coreKey === undefined && s.settable);
		expect(schema.length).toBeGreaterThan(120);
		for (const s of schema) {
			const write = settingWrite(s, 'x');
			expect(write?.target, s.key).toBe('hermes');
			expect(write && 'key' in write ? write.key : '', s.key).toBe(s.key);
		}
	});

	it('offers no write for a leaf the compose file fixes on the host', () => {
		const compose = rows.filter((s) => s.applies === 'compose');
		expect(compose.length).toBeGreaterThan(20);
		for (const s of compose) {
			expect(s.settable, s.key).toBe(false);
			expect(settingWrite(s, 'x'), s.key).toBeNull();
		}
	});

	it('types the value the way the plugin’s strict coerce wants it', () => {
		const spec = (key: string) => rows.find((s) => s.key === key)!;
		expect(settingWrite(spec('goals.max_turns'), '30')).toEqual({ target: 'hermes', key: 'goals.max_turns', value: 30 });
		expect(settingWrite(spec('comfyui.cfg'), '4.5')).toEqual({ target: 'hermes', key: 'comfyui.cfg', value: 4.5 });
		expect(settingWrite(spec('tools.enabled'), 'web, browser')).toEqual({ target: 'hermes', key: 'tools.enabled', value: ['web', 'browser'] });
		expect(settingWrite(spec('hud.reduced_motion'), 'on')).toEqual({ target: 'hermes', key: 'hud.reduced_motion', value: 'on' });
		expect(settingWrite(spec('tts.voice'), 'jarvis')).toEqual({ target: 'hermes', key: 'tts.voice', value: 'jarvis' });
	});

	it('keeps jarvis-core’s own rows on their own path', () => {
		const core = settingsCatalogue([{ key: 'assistant_name', group: 'Assistant', label: 'Name', value: 'Jarvis', type: 'string', apply: 'now' } as never]).specs;
		const row = core.find((s) => s.key === 'core:assistant_name')!;
		expect(settingWrite(row, 'Vera')).toEqual({ target: 'core', key: 'assistant_name', value: 'Vera' });
	});
});

describe('each row knows where its value comes from and when a write lands', () => {
	const specs = rows;
	const hermes = [
		{ key: 'goals.max_turns', value: 20, source: 'config', settable: true, applies: 'now' },
		{ key: 'stt.model', value: 'base', source: 'env', settable: true, applies: 'restart' },
		{ key: 'adapter.port', value: 8643, source: 'default', settable: false, applies: 'compose' },
		{ key: 'tools.enabled', value: ['web', 'browser'], source: 'override', settable: true, applies: 'now' },
		{ key: 'n8n.api_key', value: '[redacted]', source: 'env', settable: true, applies: 'restart' }
	] as const;

	it('reads source, applies and settable off the live row', () => {
		const meta = settingsMeta(specs, { hermes: [...hermes] });
		expect(meta['goals.max_turns']).toEqual({ source: 'config', applies: 'now', settable: true });
		expect(meta['stt.model']).toEqual({ source: 'env', applies: 'restart', settable: true });
		expect(meta['adapter.port']).toEqual({ source: 'default', applies: 'compose', settable: false });
		// A row the list has not answered says nothing rather than guessing.
		expect(meta['tts.voice']).toBeUndefined();
	});

	it('reads the value by the schema key, a list as its comma-joined spelling, a secret masked', () => {
		const values = settingsValues(specs, { hermes: [...hermes] });
		expect(values['goals.max_turns']).toBe('20');
		expect(values['stt.model']).toBe('base');
		expect(values['tools.enabled']).toBe('web,browser');
		expect(values['n8n.api_key']).toBe('[redacted]');
	});

	it('spells every source and applies the row prints', () => {
		expect(Object.keys(SOURCE_WORDS).sort()).toEqual(['config', 'default', 'env', 'override']);
		expect(Object.keys(APPLIES_WORDS).sort()).toEqual(['compose', 'now', 'restart']);
	});
});

describe('a value is checked against the catalogue before it is sent', () => {
	const spec = (key: string) => rows.find((s) => s.key === key)!;

	it('holds an int to its bounds and to whole numbers', () => {
		expect(validate(spec('goals.max_turns'), '30')).toBeNull();
		expect(validate(spec('goals.max_turns'), '999')).toBe('goals.max_turns must be between 1 and 200');
		expect(validate(spec('goals.max_turns'), '0')).toBe('goals.max_turns must be between 1 and 200');
		expect(validate(spec('goals.max_turns'), '1.5')).toBe('goals.max_turns takes a whole number');
		expect(validate(spec('goals.max_turns'), 'many')).toBe('goals.max_turns takes a whole number');
		// A one-sided bound says which side.
		expect(validate(spec('model.context_length'), '0')).toBe('model.context_length must be at least 1');
	});

	it('takes a float with a fraction, within its bounds', () => {
		expect(validate(spec('comfyui.cfg'), '4.5')).toBeNull();
		expect(validate(spec('comfyui.cfg'), '31')).toBe('comfyui.cfg must be between 0 and 30');
		expect(validate(spec('comfyui.cfg'), 'x')).toBe('comfyui.cfg takes a number');
	});

	it('holds an enum to its choices and a bool to true/false', () => {
		expect(validate(spec('approvals.mode'), 'smart')).toBeNull();
		expect(validate(spec('approvals.mode'), 'maybe')).toBe('approvals.mode must be one of manual, smart, off');
		expect(validate(spec('hud.lock_layout'), true)).toBeNull();
		expect(validate(spec('hud.lock_layout'), 'yes')).toBe('hud.lock_layout takes true or false');
	});

	it('wants a URL to parse as one and a path to be absolute, both may be empty', () => {
		expect(validate(spec('model.base_url'), '')).toBeNull();
		expect(validate(spec('model.base_url'), 'http://llama:8080/v1')).toBeNull();
		expect(validate(spec('model.base_url'), 'not a url')).toBe('model.base_url must be a URL, like http://host:8080');
		// `llama:8080` parses, with `llama:` as its scheme: the scheme rule is the one that says so.
		expect(validate(spec('model.base_url'), 'llama:8080')).toBe('model.base_url must start with http://, https://, ws:// or wss://');
		expect(validate(spec('model.base_url'), 'ftp://x')).toBe('model.base_url must start with http://, https://, ws:// or wss://');
		expect(validate(spec('approvals.work_root'), '/opt/data/work')).toBeNull();
		expect(validate(spec('approvals.work_root'), 'work')).toBe('approvals.work_root must be an absolute path');
	});

	it('lets a list, a text and a secret through untouched', () => {
		expect(validate(spec('tools.enabled'), 'web,browser')).toBeNull();
		expect(validate(spec('tts.voice'), 'jarvis')).toBeNull();
		expect(validate(spec('n8n.api_key'), 'sk-anything')).toBeNull();
	});
});

describe('a source that failed is said, not drawn as a shorter list', () => {
	it('says so when jarvis-core\'s own rows could not be read', () => {
		// The feed keeps jarvis-core optional so a house that is down does not
		// blank the menu; the line is what stops "short" from reading as "complete".
		expect(settingsNotice({ hermes: [], core: [], coreFailed: true })).toContain("jarvis-core's own settings could not be read");
		// Every source answered, even with nothing in them: no line.
		expect(settingsNotice({ hermes: [], core: [] })).toBe('');
		expect(settingsNotice({ hermes: [], core: [], coreFailed: false })).toBe('');
	});
});

describe('the default model is picked from a list, not typed', () => {
	const served = [
		{ id: 'qwen3.8-27b', name: 'Qwen3.8 27B', state: 'loaded' as const },
		{ id: 'qwen3.8-27b-uncensored', name: 'Uncensored', state: 'unloaded' as const },
		{ id: 'big', name: 'Big', state: 'downloading' as const }
	];
	const specs = settingsCatalogue().specs;
	const row = (out: ReturnType<typeof withModelChoices>) => out.find((s) => s.key === 'model.default');

	it('offers the served models, keeping the row on its own value', () => {
		expect(row(withModelChoices(specs, served, 'qwen3.8-27b'))).toMatchObject({ widget: 'select', options: ['qwen3.8-27b', 'qwen3.8-27b-uncensored'] });
		expect(row(withModelChoices(specs, served, 'elsewhere/model'))?.options).toEqual(['elsewhere/model', 'qwen3.8-27b', 'qwen3.8-27b-uncensored']);
	});

	it('stays a text box when the manager lists nothing, and leaves every other row alone', () => {
		expect(row(withModelChoices(specs, [], 'qwen3.8-27b'))?.widget).toBe(row(specs as ReturnType<typeof withModelChoices>)?.widget);
		const out = withModelChoices(specs, served, 'qwen3.8-27b');
		expect(out.filter((s) => s.key !== 'model.default')).toEqual(specs.filter((s) => s.key !== 'model.default'));
	});
});
