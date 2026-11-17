// Mock jarvis-adapter for jarvis-web tests.
//
// The console's Hermes surfaces talk to the adapter and nothing else, so one
// mock of the adapter covers all of them. It speaks the HUD protocol as
// `docs/hermes-hud-protocol.md` writes it down, verified against Hermes
// v0.21.0 on 2026-09-02:
//   - `/ws`: JSON-RPC 2.0 text frames, `gateway.ready` first; bearer key on
//     the upgrade or close 4401
//   - session.create / list / resume / history / status / interrupt / delete,
//     image.attach_bytes, prompt.submit, command.dispatch (goal …, the jgoal/subgoal plugin commands
//     only when the plugin is "installed", and jsettings — the jarvis-settings
//     plugin, `@<sid> list | get <key> | set <key> <json> | apply <id> |
//     discard <id> | proposals | history [n] | undo [key]` over every leaf of
//     tests/contracts/hermes-settings.json; a `set` proposes, `apply` writes
//     and announces `settings.changed`, `undo` puts the last change back),
//     jmemory — the jarvis-hud plugin's `@<sid> proposals | drop <id ...|all>` over what
//     waits to be remembered, per tests/contracts/memory-proposals.json, announcing
//     `memory.proposals {open}`; a keep is a prompt, and raises the gate's card),
//     commands.catalog, approval.respond,
//     clarify.respond, config.get / config.set
//   - a scripted turn per prompt: reasoning, a tool card, streamed text; a
//     prompt about approval raises `approval.request`; one about a question
//     raises `clarify.request`; a goal runs continue → achieved
//   - the `hud` command (`@<sid> list | dismiss <id|*> | show <json>`), and
//     `hud.panel.show/update/dismiss` events, per tests/contracts/hud-panels.json;
//     panels are kept per stored session, so `hud list` replays them after a resume
//   - `GET /api/status`, `POST /api/audio/transcribe`, `POST /api/audio/speak`,
//     `GET /api/jobs` (+ run / pause / resume), `GET /v1/toolsets`, `GET /v1/skills`,
//     `GET /api/pair` (which the console must refuse to relay), `GET /health`
//   - the model manager under `/api/models` (tests/contracts/models-api.json): the list and
//     GPUs serve the contract's examples, a download advances a third of the way on every
//     list read, and create / PATCH / load / unload / DELETE are accepted within the contract's
//     limits and refused with its sentences outside them (a llama.cpp model needs its one
//     .gguf, a vLLM one refuses a file); `POST /api/test/models` resets it, and its `mode`
//     picks ok, empty (no models), error (the manager answers 500) or gone (503)
//   - `GET /api/board`, the Board's three sources; `POST /api/test/board` ({mode}) picks
//     ok, stale, missing, models-down, error or gone
//   - test hooks: `test.emit` on the socket pushes any events; `POST /api/test/status`
//     sets what `/api/status` says (`{state}` for the model, `{kill: true}` for the
//     kill switch); `POST /api/test/plugin` installs the goal plugin;
//     `POST /api/test/panel` ({op:show|update|dismiss, id, kind, title, payload, ttl_s})
//     summons a panel on every live session; `POST /api/test/projection` ({id, type, data})
//     sends one `hud.projection` frame to every client, as hud_project would (unvalidated,
//     so a spec can send the malformed payload the console must draw as an error);
//     `POST /api/test/theme` ({op: preview|apply, id, pack}) sends `theme.preview` /
//     `theme.apply` as jarvis-personas' drafts do, and `GET /api/themes/<id>` serves the pack.
//
// Usage:  node mock-hermes.mjs [port]         (standalone)
//         import { startMockHermes } from './mock-hermes.mjs'
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { makeWav } from './mock-ha.mjs';
import { MEDIA_BY_ID } from './media-fixtures.mjs';

const require = createRequire(new URL('../../jarvis-web/package.json', import.meta.url));
const { WebSocketServer } = require('ws');

// The `jarvis-settings` plugin's catalogue — every schema leaf, with its
// `applies` — read from the contract both sides share so the mock can never
// serve a key the plugin would not, nor validate one differently.
const SETTINGS_CATALOGUE = JSON.parse(
	readFileSync(new URL('../contracts/hermes-settings.json', import.meta.url), 'utf8')
).settings;
const PERSONAS = JSON.parse(readFileSync(new URL('../contracts/persona.json', import.meta.url), 'utf8')).personas;
const SETTINGS_BY_KEY = Object.fromEntries(SETTINGS_CATALOGUE.map((s) => [s.key, s]));

// The model manager's contract: the examples are what the mock serves, the limits are what it enforces.
const MODELS_API = JSON.parse(readFileSync(new URL('../contracts/models-api.json', import.meta.url), 'utf8'));
const MODEL_LIMITS = MODELS_API.limits;
const GIB = 1024 ** 3;
// What a search finds besides the contract's own row: one the owner must accept a licence for
// and one that cannot fit, so the console's two refusals are reachable.
const SEARCH_CATALOGUE = [
	...MODELS_API.endpoints.search.example.results,
	{ repo: 'meta-llama/Llama-3.1-8B', downloads: 900000, likes: 5000, params_b: 8, size_bytes: 16 * GIB, gated: true, quantization: null, local: false },
	{ repo: 'big/Model-405B-AWQ', downloads: 1000, likes: 10, params_b: 405, size_bytes: 500 * GIB, gated: false, quantization: 'awq', local: false },
	// A made-up GGUF repo, so the add form's llama.cpp path has something to download and add.
	{ repo: 'made-up/Pebble-7B-GGUF', downloads: 1200, likes: 12, params_b: 7, size_bytes: 4 * GIB, gated: false, quantization: 'gguf', local: false }
];
const GGUF = MODELS_API.create_body.gguf_file;
/** The contract's one-.gguf rule, as the manager applies it to create and to a download's `file`. */
function ggufFileError(file) {
	if (typeof file !== 'string' || !new RegExp(`^(?:${GGUF.pattern})$`).test(file)) return GGUF.error;
	return new RegExp(`${GGUF.shard_pattern}$`).test(file) ? GGUF.shard_error : '';
}
/** A create's or a download's refusal as the manager words it: contract `refusals`, its {slots} filled. */
function refusal(key, slots = {}) {
	const r = MODELS_API.refusals[key];
	return [r.status, r.message.replace(/\{(\w+)\}/g, (_, name) => String(slots[name]))];
}
function freshModels() {
	const ex = MODELS_API.endpoints.list.example;
	// `files` is what the manager reads from its own config, never from the list: a llama.cpp model's one .gguf.
	return { models: structuredClone(ex.models), downloads: [], disk: { ...ex.disk }, local: new Set(ex.models.map((m) => m.repo)), files: {} };
}
/** A made-up llama.cpp row whose .gguf is gone (`missing_llama` on the reset), for "Download again". */
const MISSING_LLAMA = {
	row: {
		id: 'pebble-gguf', name: 'Pebble 7B (GGUF)', repo: 'made-up/Pebble-7B-GGUF', state: 'missing', on_disk_bytes: 0,
		settings: { engine: 'llama.cpp', max_model_len: 32768, gpu_memory_utilization: null, tensor_parallel_size: null, kv_cache_dtype: null, quantization: null, keep_loaded: false, idle_unload_s: 600 }
	},
	file: 'pebble-7b-q4.gguf'
};
/** The manager's validation, as sentences. */
function modelSettingsError(settings) {
	for (const [key, value] of Object.entries(settings)) {
		const range = MODEL_LIMITS[key];
		if (key === 'keep_loaded') {
			if (typeof value !== 'boolean') return 'keep_loaded must be true or false.';
		} else if (!range) return `${key} is not a setting.`;
		else if (key === 'kv_cache_dtype' || key === 'quantization') {
			if (!range.includes(value)) return `${key} must be one of ${range.join(', ')}.`;
		} else if (typeof value !== 'number' || value < range[0] || value > range[1]) return `${key} must be between ${range[0]} and ${range[1]}.`;
	}
	return '';
}

/** The plugin's own validation (the schema's `coerce`, strict), returning an error message or `''`. */
function validateSetting(spec, value) {
	if (spec.type === 'int') {
		if (!Number.isInteger(value)) return `${spec.key} takes an integer, not ${typeof value}`;
		if (spec.min != null && spec.max != null && (value < spec.min || value > spec.max)) return `${spec.key} must be between ${spec.min} and ${spec.max}, got ${value}`;
		if (spec.min != null && value < spec.min) return `${spec.key} must be at least ${spec.min}`;
		if (spec.max != null && value > spec.max) return `${spec.key} must be at most ${spec.max}`;
	} else if (spec.type === 'enum') {
		if (!spec.choices.includes(value)) return `${spec.key} must be one of ${spec.choices.join(', ')}, got ${JSON.stringify(value)}`;
	} else if (spec.type === 'list') {
		if (!Array.isArray(value) || !value.every((v) => typeof v === 'string')) return 'must be a list of toolset names';
	} else if (spec.type === 'string') {
		if (typeof value !== 'string') return 'must be a string';
	} else if (spec.type === 'bool') {
		if (typeof value !== 'boolean') return 'must be true or false';
	} else if (spec.type === 'float') {
		if (typeof value !== 'number' || !Number.isFinite(value)) return `${spec.key} takes a number, not ${typeof value}`;
		if (spec.min != null && value < spec.min) return `${spec.key} must be at least ${spec.min}`;
		if (spec.max != null && value > spec.max) return `${spec.key} must be at most ${spec.max}`;
	} else if (spec.type === 'object') {
		// The D30 HUD layouts: the plugin checks the document against its
		// `shape`; the mock only insists on an object, so a layout the console
		// sends round-trips and a string or a list is refused.
		if (value === null || typeof value !== 'object' || Array.isArray(value)) return 'must be an object';
	}
	return '';
}

// What `/hud capabilities` prints (`_shared/_levels.py` describe()): one set, one left to the matrix.
const CAPABILITY_LEVELS = {
	levels: ['off', 'read', 'ask', 'allowed'],
	capabilities: [
		{ id: 'mail', level: 'ask', tools: [{ name: 'mail_search', write: false }, { name: 'mail_send', write: true }] },
		{ id: 'calendar', level: 'read', tools: [{ name: 'calendar_list', write: false }, { name: 'calendar_create', write: true }] },
		{ id: 'house', level: null, tools: [{ name: 'ha_call_service', write: true }] },
		{ id: 'web', level: 'allowed', tools: [{ name: 'web_search', write: false }] }
	]
};

export const MOCK_KEY = 'mock-adapter-key-0123456789';
export const MODEL = 'qwen3.8-27b';
export const TRANSCRIPT = 'turn on the lab lights';
export const DELTAS = ['Turning ', 'on the ', 'lab lights.'];
export const RESPONSE = DELTAS.join('');
export const DELTAS_MD = ['## Lights\n\n', 'The **lab lights** are on.\n\n', '- lab: on\n', '- bed: off'];
// The owner's NUFI reply (T-0727), as Hermes streamed it: a few characters at a time, so the
// console draws its Markdown mid-stream as well as whole. tests/contracts/markdown_text.json
// holds the text both renderers are held to.
export const NUFI_MD = JSON.parse(readFileSync(new URL('../contracts/markdown_text.json', import.meta.url), 'utf8')).cases.find((c) => c.md.includes('NUFI')).md;
export const DELTAS_NUFI = NUFI_MD.match(/[\s\S]{1,6}/g);
export const REASONING = 'the lab strip is the light they mean';
export const GOAL_REASON_CONTINUE = 'the first file is written; the second does not exist yet';
export const GOAL_REASON_DONE = 'both files exist with the right contents';
export const GOAL_BUDGET = 20;
// A shell command long enough that the activity box must truncate it: the row
// shows the head with an ellipsis, the full text lives in the row's title.
export const LONG_COMMAND =
	'for url in "https://www.coindesk.com/markets/bitcoin" "https://cointelegraph.com/bitcoin-price-index" "https://www.reuters.com/markets/currencies"; do curl -s "$url" >> /tmp/news.json; done';
// Deltas with markdown (a heading, bold, a list) and blank-line noise — empty
// and whitespace-only chunks and a run of blank lines — so the console proves
// it renders the markdown and collapses the gaps between streamed segments
// instead of showing raw `**` and several blank lines.
export const RESEARCH_DELTAS = [
	'## Bitcoin\n\n',
	'The latest on Bitcoin: it is trading **a little higher** today.\n\n\n\n',
	'Sources I pulled:\n',
	'   \n',
	'- CoinDesk\n',
	'- Cointelegraph\n',
	'- Reuters\n\n',
	'I cross-checked the price across all three.'
];
/** A 160x100 drawing of a page: the browser's frame in the browse turn. */
const PAGE_JPEG =
	'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABQODxIPDRQSEBIXFRQYHjIhHhwcHj0sLiQySUBMS0dARkVQWnNiUFVtVkVGZIhlbXd7gYKBTmCNl4x9lnN+gXz/2wBDARUXFx4aHjshITt8U0ZTfHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHz/wAARCABkAKADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDn6Ktf2bff8+Vz/wB+m/wo/s2+/wCfK5/79N/hW9zIq0Va/s2+/wCfK5/79N/hR/Zt9/z5XP8A36b/AAouBVoq1/Zt9/z5XP8A36b/AAo/s2+/58rn/v03+FFwKtFWv7Nvv+fK5/79N/hR/Zt9/wA+Vz/36b/Ci4FWirX9m33/AD5XP/fpv8KP7Nvv+fK5/wC/Tf4UXAq0Va/s2+/58rn/AL9N/hR/Zt9/z5XP/fpv8KLgVaKtf2bff8+Vz/36b/Cj+zb7/nyuf+/Tf4UXAq0Va/s2+/58rn/v03+FH9m33/Plc/8Afpv8KLgVaKtf2bff8+Vz/wB+m/wo/s2+/wCfK5/79N/hRcCrRVr+zb7/AJ8rn/v03+FH9m33/Plc/wDfpv8ACi4Ho1FFcD4m/wCQ7c/8B/8AQRWJqd9RXllFAHqdFeWUUAep0V5ZRQB6nRXllFAHqdFeWVv+Dv8AkKy/9cD/AOhLQB2lFFFIAooooAKKKKACiiigAooqpPZedKz+ZjPbbTAt0VQ/s3/pr/47/wDXo/s3/pr/AOO//XoEX6Kof2b/ANNf/Hf/AK9H9m/9Nf8Ax3/69AF+iqH9m/8ATX/x3/69H9m/9Nf/AB3/AOvQBfoqh/Zv/TX/AMd/+vR/Zv8A01/8d/8Ar0AX6Kof2b/01/8AHf8A69H9m/8ATX/x3/69AF+iqH9m/wDTX/x3/wCvR/Zv/TX/AMd/+vQBfoqh/Zv/AE1/8d/+vR/Zv/TX/wAd/wDr0AX6Kof2b/01/wDHf/r1ZtoPIjK7t2TnOMUATUUUUhhVSey86Vn8zGe22rdVJ7LzpWfzMZ7baYiP+zf+mv8A47/9ej+zf+mv/jv/ANej+zf+mv8A47/9ej+zf+mv/jv/ANegA/s3/pr/AOO//Xo/s3/pr/47/wDXo/s3/pr/AOO//Xo/s3/pr/47/wDXoAP7N/6a/wDjv/16P7N/6a/+O/8A16P7N/6a/wDjv/16P7N/6a/+O/8A16AD+zf+mv8A47/9ej+zf+mv/jv/ANej+zf+mv8A47/9ej+zf+mv/jv/ANegA/s3/pr/AOO//Xo/s3/pr/47/wDXo/s3/pr/AOO//Xo/s3/pr/47/wDXoAP7N/6a/wDjv/16P7N/6a/+O/8A16P7N/6a/wDjv/16P7N/6a/+O/8A16AD+zf+mv8A47/9ej+zf+mv/jv/ANej+zf+mv8A47/9enR2GyRW83O0g42//XoAb/Zv/TX/AMd/+vR/Zv8A01/8d/8Ar1fooApR2GyRW83O0g42/wD16u0UUDCqk9l50rP5mM9ttW6yb3Wvsl08H2fftx82/GcjPpTjFydkS2luT/2b/wBNf/Hf/r0f2b/01/8AHf8A69Uf+Ej/AOnX/wAif/Wo/wCEj/6df/In/wBatPY1OxPtI9y9/Zv/AE1/8d/+vR/Zv/TX/wAd/wDr1R/4SP8A6df/ACJ/9aj/AISP/p1/8if/AFqPY1Owe0j3L39m/wDTX/x3/wCvR/Zv/TX/AMd/+vVH/hI/+nX/AMif/Wq3p2rfb52i8ny8Luzvz3Ht70nTmldoanF6D/7N/wCmv/jv/wBej+zf+mv/AI7/APXq/RWZRQ/s3/pr/wCO/wD16P7N/wCmv/jv/wBer9FAFD+zf+mv/jv/ANej+zf+mv8A47/9er9FAFD+zf8Apr/47/8AXo/s3/pr/wCO/wD16v0UAUP7N/6a/wDjv/16s20HkRld27JznGKmooAKKKKQwrmdXtbiTUpnjgldTtwVQkH5RXTUVpCfI7kSjzKxxn2K6/59pv8Av2aPsV1/z7Tf9+zXZ0Vr9YfYj2S7nGfYrr/n2m/79mj7Fdf8+03/AH7NdnRR9YfYPZLucZ9iuv8An2m/79mtPQbeeG9dpYZEUxkZZSB1FdBRSlXclaw1TSdwooornNQooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigD/2Q==';
export const WAV_DATA_URL = `data:audio/wav;base64,${makeWav(0.15).toString('base64')}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** A stored session the sidebar lists, and its transcript. */
function seedSessions() {
	return new Map([
		[
			'sess-back-door',
			{
				id: 'sess-back-door',
				title: 'is the back door shut?',
				preview: 'is the back door shut?',
				started_at: Date.now() / 1000 - 3600,
				message_count: 3,
				source: 'tui',
				messages: [
					{ role: 'user', text: 'is the back door shut?', timestamp: Date.now() / 1000 - 3600 },
					{ role: 'tool', name: 'get_state', context: 'lock.back_door', args: { entity_id: 'lock.back_door' } },
					{ role: 'assistant', text: 'It is, Sir.', timestamp: Date.now() / 1000 - 3590, reasoning: 'checked the lock' }
				]
			}
		]
	]);
}

function seedJobs() {
	return [
		{
			id: 'job-morning',
			name: 'Morning briefing',
			prompt: 'Prepare the morning briefing.',
			schedule: { kind: 'cron', expr: '0 7 * * *', display: '0 7 * * *' },
			schedule_display: '0 7 * * *',
			enabled: true,
			state: 'scheduled',
			paused_at: null,
			next_run_at: new Date(Date.now() + 3600_000).toISOString(),
			last_run_at: null,
			last_status: null,
			last_error: null,
			model_snapshot: MODEL,
			deliver: 'local'
		},
		{
			id: 'job-evening',
			name: 'Evening summary',
			prompt: 'Prepare the evening summary.',
			schedule: { kind: 'cron', expr: '0 22 * * *', display: '0 22 * * *' },
			schedule_display: '0 22 * * *',
			enabled: true,
			state: 'scheduled',
			paused_at: null,
			next_run_at: new Date(Date.now() + 7200_000).toISOString(),
			last_run_at: new Date(Date.now() - 86_400_000).toISOString(),
			last_status: 'ok',
			last_error: null,
			model_snapshot: MODEL,
			deliver: 'local'
		}
	];
}

const TOOLSETS = [
	{ name: 'web', label: '🔍 Web Search & Scraping', description: 'web_search, web_extract', enabled: true, configured: true, tools: ['web_extract', 'web_search'] },
	{ name: 'terminal', label: '💻 Terminal & Processes', description: 'terminal, process', enabled: true, configured: true, tools: ['process', 'terminal'] },
	{ name: 'homeassistant', label: '🏠 Home Assistant', description: 'states and services', enabled: true, configured: true, tools: ['ha_call_service', 'ha_get_state'] },
	{ name: 'video', label: '🎬 Video Analysis', description: 'video_analyze', enabled: false, configured: true, tools: ['video_analyze'] }
];

const SKILLS = [
	{ name: 'hermes-agent', description: 'Use, configure, theme, extend, and orchestrate Hermes Agent.', category: 'autonomous-ai-agents' },
	{ name: 'obsidian', description: 'Read, search, create, and edit notes in the Obsidian vault.', category: 'note-taking' },
	{ name: 'systematic-debugging', description: 'Find the cause before the fix.', category: 'software-development' }
];

const CATALOG = {
	pairs: [
		['/goal', 'Set a standing goal Hermes works on across turns until achieved'],
		['/new', 'Start a new session'],
		['/steer', 'Inject a message after the next tool call']
	]
};

/** The settable config, at defaults; `test.reset` puts it back between tests. */
function seedConfig() {
	return {
		'goals.max_turns': GOAL_BUDGET,
		'tts.piper.voice': 'en_GB-alan-medium',
		'approvals.mode': 'smart',
		'tools.enabled': [],
		'tools.disabled': [],
		'model.default': MODEL,
		'image_gen.provider': 'comfyui',
		'tool_loop_guardrails.hard_stop_enabled': true,
		'tool_loop_guardrails.loop_caps.max_web_searches': 5,
		// The D30 HUD keys. Seeded because the window manager reads all three on
		// mount and a key the mock does not hold answers `undefined`, which is
		// indistinguishable from a backend that has no settings plugin at all.
		'hud.mode': 'tiled',
		'hud.lock_layout': false,
		'hud.layouts': {}
	};
}

// The adapter's GET /api/board (jarvis-adapter board.py over the jarvis-board plugin's file),
// shaped as the real one: three sources, each ok / stale / unavailable with its reason.
// What the deploy's smoke last proved (jarvis-adapter verified.py, T-0580): the last run failed
// at its third check, so two are lit, one failed, the rest passed on the deploy before.
function verifiedFixture(now) {
	const names = [['health', 'health'], ['matrix', 'approval matrix'], ['chat', 'streamed reply'], ['context', 'context window'], ['tool', 'tool call'], ['goal', 'goal loop'], ['gate', 'failing gate'], ['panel', 'panel'], ['layout', 'layout'], ['approval', 'approval'], ['speech', 'speech round trip'], ['voice', 'persona voice']];
	return {
		run: { tag: 'mock-2', at: now - 1800, ok: false, failed: 'SSE chunks=0', failed_check: 'chat' },
		checks: names.map(([id, words], i) => (i < 2 ? { id, words, passed_at: now - 1800, tag: 'mock-2', current: true } : { id, words, passed_at: now - 90000, tag: 'mock-1', current: false })),
		surfaces: [
			{ id: 'console', words: 'console', ok: true, at: now - 1500, passed_at: now - 1500, why: '' },
			{ id: 'car', words: 'car screen', ok: true, at: now - 1500, passed_at: now - 1500, why: '' },
			{ id: 'phone', words: 'phone app', ok: null, at: now - 1500, passed_at: null, why: 'no phone attached (adb)' },
			{ id: 'desktop', words: 'desktop app', ok: null, at: now - 1500, passed_at: null, why: 'nothing here can drive a desktop session' }
		]
	};
}

function boardFixture(mode, now) {
	const made = boardSources(mode, now);
	return mode === 'error' || mode === 'gone' ? made : { ...made, verified: verifiedFixture(now) };
}

function boardSources(mode, now) {
	const ok = (age) => ({ status: 'ok', age_s: age, why: null });
	const down = { status: 'unavailable', age_s: null, why: '/running: ConnectError' };
	const hermes = {
		schema: 1,
		at: now - 4,
		window_s: 86400,
		models: [
			{ model: 'qwen-local', turns: 41, input_tokens: 182400, output_tokens: 9100, calls: 96 },
			{ model: 'qwen-small', turns: 6, input_tokens: 8800, output_tokens: 700, calls: 6 }
		],
		hours: Array.from({ length: 24 }, (_, i) => ({
			at: now - (23 - i) * 3600,
			turns: i % 5 === 0 ? {} : { 'qwen-local': (i * 7) % 5, ...(i % 4 === 0 ? { 'qwen-small': 1 } : {}) }
		})),
		flows: [
			{ kind: 'tui', model: 'qwen-local', turns: 28 },
			{ kind: 'cron', model: 'qwen-local', turns: 13 },
			{ kind: 'api_server', model: 'qwen-small', turns: 6 }
		],
		kinds: [
			{ kind: 'tui', turns: 28, answered: 23, cut_off: 1, no_reply: 3, running: 1 },
			{ kind: 'cron', turns: 13, answered: 13, cut_off: 0, no_reply: 0, running: 0 },
			{ kind: 'api_server', turns: 6, answered: 5, cut_off: 0, no_reply: 1, running: 0 }
		],
		latency: { turns: 41, p50_ms: 3800, p95_ms: 17200 },
		tools: {
			per_minute: Array.from({ length: 60 }, (_, i) => (i % 7 === 0 ? 0 : (i * 3) % 5)),
			top: [
				{ name: 'web_search', calls: 31 },
				{ name: 'ha_call_service', calls: 18 },
				{ name: 'hud_say', calls: 12 }
			]
		},
		cost: { usd: null, why: 'Hermes recorded no cost for these models' },
		errors: {}
	};
	const gpus = [
		{ index: 0, name: 'RTX 3090', total_mib: 24576, used_mib: 21650, users: [{ who: 'models', mib: 20890 }, { who: 'voice', mib: 760 }] },
		{ index: 1, name: 'RTX 3060', total_mib: 12288, used_mib: 3100, users: [{ who: 'whisper', mib: 3100 }] }
	];
	const models = [{ model: 'qwen-local', state: 'ready' }];
	if (mode === 'missing') {
		return {
			at: now,
			sources: { hermes: { status: 'unavailable', age_s: null, why: 'not written yet: the jarvis-board plugin writes it after a turn' }, models: ok(0), gpus: ok(0) },
			hermes: null,
			models,
			gpus
		};
	}
	if (mode === 'models-down') {
		return { at: now, sources: { hermes: ok(4), models: down, gpus: down }, hermes, models: null, gpus: null };
	}
	const age = mode === 'stale' ? 3600 : 4;
	return {
		at: now,
		sources: { hermes: mode === 'stale' ? { status: 'stale', age_s: age, why: null } : ok(age), models: ok(0), gpus: ok(0) },
		hermes,
		models,
		gpus
	};
}

export async function startMockHermes({ port = 0, log = () => {}, house = null } = {}) {
	const options = { house };
/**
 * One row per Hermes memory layer, so the orb's mid field has something with
 * real shape to lay out: the identity documents, a durable entry, a fact from
 * the structured store, a skill and a session. The kinds matter more than the
 * volume -- the graph colours by kind and draws `shared_session` edges from
 * `conversation_id`, and a fixture of twenty identical rows would exercise
 * neither.
 */
function seedMemoryLayers() {
	const now = Math.floor(Date.now() / 1000);
	return [
		{ id: 'soul:SOUL.md', text: '# SOUL.md — JARVIS\nYou are JARVIS.', tags: ['identity', 'soul'], created: now - 900, source: 'hermes-layer' },
		{ id: 'user:USER.md', text: '# USER.md — who JARVIS works for\nName: Casey.', tags: ['identity', 'user'], created: now - 900, source: 'hermes-layer' },
		{ id: 'memory:0', text: 'Backups must run between 02:00 and 04:00 local time.', tags: ['durable'], created: now - 800, source: 'hermes-layer' },
		{ id: 'fact:3', text: "The greenhouse target temperature is 24°C.", tags: ['fact', 'user_pref', 'greenhouse'], created: now - 700, source: 'hermes-facts' },
		{ id: 'skill:homelab-runbook', text: 'homelab-runbook — Homelab runbook', tags: ['skill'], created: now - 600, source: 'hermes-skills', uses: 33 },
		{ id: 'session:s1', text: 'Morning brief', tags: ['session'], created: now - 500, source: 'hermes-sessions', conversation_id: 's1', messages: 4 }
	];
}

/** The per-source tally the real endpoint returns beside the rows. */
function countBySource(rows) {
	const out = {};
	for (const r of rows) out[r.source] = (out[r.source] ?? 0) + 1;
	return out;
}

	const state = {
		status: { model: MODEL, state: 'ready', warming_up: false },
		// Released by default; `POST /api/test/status` flips it the way it flips
		// the model state, so an e2e run can drive the reactor's `kill` row.
		killSwitch: { engaged: false, actor: '', reason: '', at: '' },
		pluginInstalled: false,
		// What GET /api/board answers: ok, stale, missing (the plugin has not written yet),
		// models-down, error (a 500) or gone (a 503, the adapter out of reach).
		// `POST /api/test/board` ({mode}) sets it.
		board: 'ok',
		sessions: seedSessions(),
		// The jarvis-hud plugin's routines view (`/hud @sid routines view|enable|remove|...`).
		routines: seedRoutines(),
		jobs: seedJobs(),
		memoryLayers: seedMemoryLayers(),
		config: seedConfig(),
		// jarvis-guard's standing "always" answers (`/guard @sid grants|revoke <base_key>`, T-0570).
		grants: seedGrants(),
		deviceKeys: seedDeviceKeys(),
		// `jsettings set` from the apps proposes; these wait for `apply`/`discard`.
		proposals: [],
		// What waits to be remembered (the memory_propose tool's queue): `test.memory_propose` adds,
		// `jmemory drop` and an approved keep take away.
		memoryProposals: [],
		memoryNext: 1,
		// Every settings write, oldest first: what `jsettings history` lists and `undo` reads.
		changes: [],
		live: new Map(), // live id -> { stored, goal, turn, awaiting }
		panels: new Map(), // stored id -> Map(panel id -> panel), so panels survive a resume
		nextLive: 1,
		nextStored: 1,
		transcripts: 0,
		spoken: [],
		voices: [],
		streamed: [],
		noStream: false,
		persona: 'jarvis',
		// jarvis-personas' drafts (drafts.py): the worn theme's id when one was applied, and
		// every drafted pack by id, which `GET /api/themes/<id>` serves. `POST /api/test/theme`.
		theme: '',
		themes: new Map(),
		speechSeconds: 0
	};

	const clients = new Set();
	const send = (ws, frame) => {
		if (ws.readyState === 1) ws.send(JSON.stringify(frame));
	};
	const emit = (sessionId, type, payload = {}) => {
		const live = state.live.get(sessionId);
		const seq = live ? ++live.seq : 0;
		const frame = { jsonrpc: '2.0', method: 'event', params: { type, session_id: sessionId, seq, payload } };
		// To the socket that owns the session, as Hermes does. Every client heard
		// every session's events, and a live session outlives its page (a resume
		// carries its turn), so after twenty page loads each panel event reached
		// a page twenty-one times (the frame budget after the gallery, 2026-09-23).
		for (const ws of live?.ws ? [live.ws] : clients) send(ws, frame);
	};
	const sessionInfo = (sessionId) => {
		const live = state.live.get(sessionId);
		const stored = live ? state.sessions.get(live.stored) : null;
		return {
			model: live?.model ?? MODEL,
			provider: 'custom',
			approval_mode: 'smart',
			running: Boolean(live?.turn),
			title: stored?.title ?? '',
			stored_session_id: live?.stored ?? '',
			tools: { terminal: ['terminal', 'process'], homeassistant: ['ha_call_service', 'ha_get_state'] },
			version: '0.21.0'
		};
	};

	function newStored(title = '') {
		const id = `sess-${String(state.nextStored++).padStart(3, '0')}`;
		state.sessions.set(id, { id, title, preview: '', started_at: Date.now() / 1000, message_count: 0, source: 'hud', messages: [] });
		return id;
	}
	function attach(storedId, ws) {
		const liveId = `live-${state.nextLive++}`;
		state.live.set(liveId, { stored: storedId, goal: null, turn: null, awaiting: null, seq: 0, ws });
		return liveId;
	}
	// Panels are kept per STORED session, so a `hud list` after a resume replays
	// what was on the surface before the reconnect — the late-join path.
	const panelStore = (storedId) => {
		if (!state.panels.has(storedId)) state.panels.set(storedId, new Map());
		return state.panels.get(storedId);
	};
	/** Emit a panel event to every live session attached to a stored session. */
	/**
	 * One settings write, as the plugin makes it: the value under both spellings, a line in the
	 * change log (`jsettings history`) and `settings.changed {key}` to every live session.
	 * Answers what `apply` and `undo` answer, a secret masked.
	 */
	const writeSetting = (spec, value, actor) => {
		const mask = (v) => (spec.secret && v ? '[redacted]' : v);
		const previous = spec.key in state.config ? state.config[spec.key] : spec.hermes_key && spec.hermes_key in state.config ? state.config[spec.hermes_key] : (spec.default ?? null);
		state.config[spec.key] = value;
		// The seed and the goal budget read the Hermes key: keep both spellings agreeing.
		if (spec.hermes_key && spec.hermes_key !== spec.key) state.config[spec.hermes_key] = value;
		state.changes.push({ ts: new Date().toISOString(), actor, key: spec.key, before: previous, after: value });
		for (const sid of state.live.keys()) emit(sid, 'settings.changed', { key: spec.key });
		return { key: spec.key, value: mask(value), previous: mask(previous), restart_needed: spec.restart_needed === true, applies: spec.applies, source: spec.hermes_key ? 'config' : 'override' };
	};
	/** The proposals file changed: `settings.proposals {waiting}` to every live session, a count and nothing else (T-1322). */
	const proposalsChanged = () => {
		for (const sid of state.live.keys()) emit(sid, 'settings.proposals', { waiting: state.proposals.length });
	};
	/** The memory proposals queue changed: `memory.proposals {open}` to every live session, a count and nothing else (T-1503). */
	const memoryChanged = () => {
		for (const sid of state.live.keys()) emit(sid, 'memory.proposals', { open: state.memoryProposals.length });
	};
	const emitPanel = (storedId, type, payload) => {
		for (const [liveId, live] of state.live) if (live.stored === storedId) emit(liveId, type, payload);
	};
	const record = (sessionId, row) => {
		const live = state.live.get(sessionId);
		const stored = live && state.sessions.get(live.stored);
		if (!stored) return;
		stored.messages.push({ timestamp: Date.now() / 1000, ...row });
		stored.message_count = stored.messages.length;
		if (!stored.title && row.role === 'user') {
			stored.title = row.text.slice(0, 40);
			stored.preview = row.text.slice(0, 60);
			emit(sessionId, 'session.title', { session_id: live.stored, title: stored.title });
		}
	};

	// --- the scripted turns ------------------------------------------------------
	async function runTurn(sessionId, text) {
		const live = state.live.get(sessionId);
		if (!live) return;
		live.turn = { text };
		record(sessionId, { role: 'user', text });
		emit(sessionId, 'message.start');
		await sleep(30);
		const lower = text.toLowerCase();
		const goalTurn = text.startsWith('[Continuing toward your standing goal]');
		if (goalTurn) return goalStep(sessionId, live);
		// The sentence the Approvals screen's Keep sends (jarvis-web memory-proposals.ts): the model
		// calls memory_proposals keep, and the gate asks on a card that shows the fact whole.
		const keep = /^Keep memory proposals? (p\d+(?:, p\d+)*):/.exec(text);
		if (keep) return memoryKeepTurn(sessionId, live, keep[1].split(', '));
		if (lower.includes('approv') || lower.includes('delete the')) return approvalTurn(sessionId, live);
		if (lower.includes('ask me') || lower.includes('which door')) return clarifyTurn(sessionId, live);
		if (lower.includes('bitcoin') || lower.includes('research')) return researchTurn(sessionId, live);
		if (lower.includes('browse')) return browseTurn(sessionId, live);
		if (live.photos) {
			// Hermes hands a queued image to the next prompt only; the reply says so,
			// so a spec can tell the photo went with these words (T-0497).
			live.photos = 0;
			const seen = 'I can see the photo you attached.';
			emit(sessionId, 'message.delta', { text: seen });
			return finish(sessionId, live, seen, '');
		}
		if (lower.includes('slowly')) {
			// A turn with no token for a while: the warming-up window.
			await sleep(1500);
		}
		const deltas = lower.includes('nufi') ? DELTAS_NUFI : lower.includes('markdown') ? DELTAS_MD : DELTAS;
		for (const word of REASONING.split(' ')) {
			emit(sessionId, 'reasoning.delta', { text: word + ' ' });
			await sleep(10);
		}
		// "porch" calls the house by Hermes' own tool name, so the house panel pulls up
		// (`hud/pullUp.ts`); every other turn keeps a tool that pulls nothing up.
		const tool = lower.includes('porch') ? 'ha_call_service' : 'turn_on';
		emit(sessionId, 'tool.start', { tool_id: 'call-1', name: tool, context: 'lab lights', args: { name: 'lab lights', brightness: 80 } });
		await sleep(40);
		emit(sessionId, 'tool.progress', { tool_id: 'call-1', text: 'calling light.turn_on' });
		await sleep(40);
		emit(sessionId, 'tool.complete', { tool_id: 'call-1', name: tool, args: { name: 'lab lights', brightness: 80 }, duration: 0.012, result: 'ok' });
		record(sessionId, { role: 'tool', name: tool, context: 'lab lights', args: { name: 'lab lights', brightness: 80 } });
		await sleep(30);
		for (const delta of deltas) {
			emit(sessionId, 'message.delta', { text: delta });
			await sleep(40);
		}
		finish(sessionId, live, deltas.join(''), REASONING);
	}

	// The turn from the owner's report: several web_search and terminal calls,
	// one with a long shell command, then a multi-part reply. It exercises the
	// activity box — many rows, long args to truncate — and gives the reply
	// enough deltas that a test can watch the box hold still while it streams.
	async function researchTurn(sessionId, live) {
		const calls = [
			{ id: 'r-s1', name: 'web_search', args: { query: 'latest Bitcoin news', max_results: 5 } },
			{ id: 'r-s2', name: 'web_search', args: { query: 'Bitcoin price today USD' } },
			{ id: 'r-t1', name: 'terminal', args: { command: LONG_COMMAND, timeout: 90 } },
			{ id: 'r-s3', name: 'web_search', args: { query: 'Bitcoin ETF inflows this week' } },
			{ id: 'r-t2', name: 'terminal', args: { command: 'jq -r ".articles[] | .title" /tmp/news.json | head -20', timeout: 30 } }
		];
		for (const call of calls) {
			emit(sessionId, 'tool.start', { tool_id: call.id, name: call.name, context: call.name === 'terminal' ? 'shell' : 'web', args: call.args });
			await sleep(25);
			emit(sessionId, 'tool.complete', { tool_id: call.id, name: call.name, args: call.args, duration: 0.4, result: 'ok' });
			record(sessionId, { role: 'tool', name: call.name, args: call.args });
			await sleep(25);
		}
		for (const delta of RESEARCH_DELTAS) {
			emit(sessionId, 'message.delta', { text: delta });
			await sleep(80);
		}
		finish(sessionId, live, RESEARCH_DELTAS.join(''));
	}

	// A browse turn in Hermes's real shapes (T-0732; scripts/hermes-probes/activity_probe.py
	// recorded them on production): `duration_s`, `result` as the parsed object, and the
	// jarvis-activity plugin's start/step/frame with a JPEG. The page stays open for a while
	// so a spec can watch the live step and stop it; a stopped turn emits nothing more.
	async function browseTurn(sessionId, live) {
		const search = { tool_id: 'b-s1', name: 'web_search', args: { query: 'tide times', limit: 5 } };
		emit(sessionId, 'tool.start', search);
		await sleep(60);
		const web = [
			{ title: 'Tide tables', url: 'https://tides.example/a', description: 'Today', position: 1 },
			{ title: 'Harbour times', url: 'https://harbour.example/b', description: 'Week', position: 2 }
		];
		emit(sessionId, 'tool.complete', { ...search, duration_s: 1.2, result: { success: true, data: { web } } });
		const nav = { tool_id: 'b-n1', name: 'browser_navigate', args: { url: 'https://tides.example/a' } };
		emit(sessionId, 'tool.generating', { name: nav.name });
		emit(sessionId, 'activity.start', { id: 'a_mock01', surface: 'browser', tool: nav.name, intent: 'open https://tides.example/a', started_at: new Date().toISOString() });
		emit(sessionId, 'tool.start', nav);
		await sleep(60);
		emit(sessionId, 'activity.step', { id: 'a_mock01', seq: 1, tool: nav.name, action: 'open https://tides.example/a', ok: true, at: new Date().toISOString() });
		emit(sessionId, 'activity.frame', { id: 'a_mock01', seq: 2, url: 'https://tides.example/a', title: 'Tide tables', text: '- heading "Tide tables" [ref=e1]', image: PAGE_JPEG, at: new Date().toISOString() });
		for (let i = 0; i < 40 && live.turn; i++) await sleep(100);
		if (!live.turn) return;
		emit(sessionId, 'tool.complete', { ...nav, duration_s: 2.8, result: { success: true, url: 'https://tides.example/a', title: 'Tide tables', snapshot: '- heading "Tide tables" [ref=e1]', element_count: 1 } });
		emit(sessionId, 'activity.end', { id: 'a_mock01', steps: 1, status: 'done', ended_at: new Date().toISOString() });
		const text = 'High tide is at 14:20.';
		emit(sessionId, 'message.delta', { text });
		finish(sessionId, live, text);
	}

	function finish(sessionId, live, text, reasoning = '') {
		record(sessionId, { role: 'assistant', text, reasoning });
		emit(sessionId, 'message.complete', { text, status: 'complete', reasoning, usage: { total: 12, calls: 1 } });
		live.turn = null;
		emit(sessionId, 'session.info', sessionInfo(sessionId));
	}

	async function approvalTurn(sessionId, live) {
		emit(sessionId, 'tool.start', { tool_id: 'call-rm', name: 'terminal', context: 'rm -rf /tmp/scratch', args: { command: 'rm -rf /tmp/scratch' } });
		await sleep(30);
		live.awaiting = { kind: 'approval', request_id: 'req-rm-1' };
		// The payload shape is Hermes', not a convenient one: there is no `tool`
		// and no `reason` field. The tool's name is rendered into `command` as
		// `<tool> (plugin approval rule)` and the gate's sentence is `description`.
		// A mock that invented a `tool` field is a mock the console passes against
		// and the real gateway does not — which is how the console came to title
		// every approval card with the literal word "tool".
		emit(sessionId, 'approval.request', {
			request_id: 'req-rm-1',
			command: '<terminal> (plugin approval rule)',
			pattern_key: 'plugin_rule:terminal:destructive',
			description: 'terminal rm deletes files and the shell has no undo — command: rm -rf /tmp/scratch',
			allow_permanent: true,
			allow_session: true,
			choices: ['once', 'session', 'always', 'deny']
		});
	}

	async function memoryKeepTurn(sessionId, live, ids) {
		// Several ids are one call and one card that shows each (the plugin's card_facts), T-1505.
		const rows = ids.map((id) => state.memoryProposals.find((p) => p.id === id));
		const id = ids.join(',');
		const args = { action: 'keep', ids };
		emit(sessionId, 'tool.start', { tool_id: 'call-keep', name: 'memory_proposals', context: id, args });
		await sleep(30);
		live.awaiting = { kind: 'approval', request_id: `req-keep-${id}`, keep: ids };
		// The gate's five lines, as hermes/plugins/_shared/_messages.py draws them for a keep: the last
		// five of the reason, after the rule's sentence (a reason that starts with them is not a card).
		const fact = (row, i) =>
			`${i + 1}) [proposed by a routine${row.read?.length ? `, after reading ${row.read.join(', ')}` : ''}, for ${row.store === 'memory' ? 'the profile' : 'the fact store'}] “${row.text}”`;
		const what = rows.every(Boolean)
			? `Remember what was proposed: ${rows.length === 1 ? '1 fact' : `${rows.length} facts`}. ${rows.map(fact).join('; ')}?`
			: 'Keep proposed facts that are not waiting (nothing would be stored)?';
		emit(sessionId, 'approval.request', {
			request_id: `req-keep-${id}`,
			command: '<memory_proposals> (plugin approval rule)',
			pattern_key: `plugin_rule:memory_proposals:memory:proposals:${id}`,
			description: `memory_proposals keep stores what was proposed, so it is asked every time. ids: ${id}.\nWhat: ${what}\nWhy: the owner asked to keep it\nImpact: Jarvis stores each as written and reads it back as your own in every later conversation.\nUndoable: yes\nHome: no`,
			allow_permanent: false,
			allow_session: false,
			choices: ['once', 'deny']
		});
	}

	async function afterKeep(sessionId, live, ids, choice) {
		await sleep(30);
		const waiting = ids.every((id) => state.memoryProposals.some((p) => p.id === id));
		const args = { action: 'keep', ids };
		if (choice === 'deny' || !waiting) {
			emit(sessionId, 'tool.complete', { tool_id: 'call-keep', name: 'memory_proposals', args, error: choice === 'deny' ? 'denied by the user' : 'not waiting', duration: 0.1 });
			return finish(sessionId, live, 'Understood, Sir. Nothing was kept.');
		}
		state.memoryProposals = state.memoryProposals.filter((p) => !ids.includes(p.id));
		memoryChanged();
		emit(sessionId, 'tool.complete', { tool_id: 'call-keep', name: 'memory_proposals', args, duration: 0.1, result: 'ok' });
		await sleep(20);
		finish(sessionId, live, 'Stored, Sir, exactly as written.');
	}

	async function afterApproval(sessionId, live, choice) {
		const keep = live.awaiting?.keep;
		live.awaiting = null;
		if (keep) return afterKeep(sessionId, live, keep, choice);
		await sleep(30);
		if (choice === 'deny') {
			emit(sessionId, 'tool.complete', { tool_id: 'call-rm', name: 'terminal', error: 'denied by the user', duration: 0.5 });
			await sleep(20);
			emit(sessionId, 'message.delta', { text: 'Understood, Sir. Not running it.' });
			finish(sessionId, live, 'Understood, Sir. Not running it.');
			return;
		}
		emit(sessionId, 'tool.complete', { tool_id: 'call-rm', name: 'terminal', args: { command: 'rm -rf /tmp/scratch' }, duration: 0.2, result: '' });
		await sleep(20);
		emit(sessionId, 'message.delta', { text: `Done, Sir (approved ${choice}).` });
		finish(sessionId, live, `Done, Sir (approved ${choice}).`);
	}

	async function clarifyTurn(sessionId, live) {
		live.awaiting = { kind: 'clarify', request_id: 'q-door-1' };
		emit(sessionId, 'clarify.request', { request_id: 'q-door-1', question: 'Front door or garage door?', options: ['front', 'garage'] });
		// The turn goes on changing the HUD while the question waits, as a real one does (T-0823:
		// each change once wiped what the owner had typed on the card). Late enough to land after
		// the test has typed its answer.
		await sleep(1200);
		if (live.awaiting?.request_id === 'q-door-1') emit(sessionId, 'message.delta', { text: 'Which door, Sir? ' });
	}

	async function afterClarify(sessionId, live, answer) {
		live.awaiting = null;
		await sleep(30);
		emit(sessionId, 'message.delta', { text: `The ${answer} door it is, Sir.` });
		finish(sessionId, live, `The ${answer} door it is, Sir.`);
	}

	async function goalStep(sessionId, live) {
		const goal = live.goal ?? { text: '', turns: 0, budget: state.config['goals.max_turns'] };
		live.goal = goal;
		goal.turns += 1;
		emit(sessionId, 'message.delta', { text: `Working on it (turn ${goal.turns}).` });
		await sleep(30);
		if (goal.turns < 2) {
			finish(sessionId, live, `Working on it (turn ${goal.turns}).`);
			await sleep(40);
			emit(sessionId, 'status.update', { kind: 'goal', text: `↻ Continuing toward goal (${goal.turns}/${goal.budget}): ${GOAL_REASON_CONTINUE}` });
			await sleep(60);
			if (!goal.paused) void runTurn(sessionId, `[Continuing toward your standing goal]\nGoal: ${goal.text}\n\nContinue working toward this goal.`);
		} else {
			finish(sessionId, live, `Both files are in place, Sir (turn ${goal.turns}).`);
			await sleep(40);
			emit(sessionId, 'status.update', { kind: 'goal', text: `✓ Goal achieved: ${GOAL_REASON_DONE}` });
			live.goal = null;
		}
	}

	// --- JSON-RPC ------------------------------------------------------------------
	const err = (code, message) => ({ error: { code, message } });
	function seedRoutines() {
		return {
			routines: [
				{
					id: 'r1',
					name: 'Car playlist',
					said: 'Every time the phone connects to the car: Play Rocky 3 on shuffle',
					enabled: true,
					do: 'Play Rocky 3 on shuffle',
					cooldown_min: 10,
					once: false,
					when: { event: 'car_connected' }
				},
				{
					id: 'r2',
					name: 'Home safe',
					said: "Every time you arrive at Home: Text Cameron I'm home safe",
					enabled: true,
					do: "Text Cameron I'm home safe",
					cooldown_min: 30,
					once: false,
					place: 'Home',
					when: { event: 'arrive', place: 'p1', place_label: 'Home' }
				},
				{
					id: 'r3',
					name: 'Porch light',
					said: 'Every day at 19:30: turn the porch light on',
					enabled: true,
					do: 'turn the porch light on',
					cooldown_min: 0,
					once: false,
					runs_in: 'house',
					when: { event: 'time', at: '19:30' }
				}
			],
			people: [{ name: 'Cameron', relations: ['girlfriend'], phone: '+15550100' }],
			places: [{ id: 'p1', label: 'Home', address: '12 Oak St, Ames' }],
			// The phone's place watch (T-0170, tests/contracts/routine-armed.json), heard two minutes ago.
			place_watch: { device_id: 'pixel-8', name: 'Pixel 8', at: Date.now() / 1000 - 120, state: 'not_armed', reason: 'no_background_location', fix_age_s: 60 }
		};
	}

	function seedGrants() {
		const now = Date.now() / 1000;
		const row = (tool, base_key, scopes, days) => ({ tool, base_key, scopes, granted_at: now - days * 86400, expires_at: now + (30 - days) * 86400 });
		return [row('smart_home_control', 'smart_home_control:light.turn_on', ['at-home'], 3), row('mail_send', 'mail_send:to:a@example.test', ['after-outside'], 1)];
	}

	// The adapter's per-device keys (T-0569). `key` is the mock's own: the real adapter keeps
	// only a hash and shows a key once, to the device it was minted for.
	function seedDeviceKeys() {
		const now = Date.now() / 1000;
		return [
			{ id: 'k-pixel', name: 'Pixel 8', kind: 'phone', created: now - 5 * 86400, last_seen: now - 180, house_token: 'tok_pixel', key: 'mock-device-key-pixel' }, // pragma: allowlist secret
			{ id: 'k-tablet', name: 'Kitchen tablet', kind: 'phone', created: now - 20 * 86400, last_seen: null, house_token: null, key: 'mock-device-key-tablet' }
		];
	}
	const publicKey = ({ key, ...rest }) => (void key, rest);

	function dispatch(sessionId, name, arg) {
		const live = state.live.get(sessionId);
		if (!live) return err(4001, 'session not found');
		const text = String(arg ?? '').trim();
		if (name === 'goal') {
			if (text === 'status') {
				return {
					result: {
						type: 'exec',
						output: live.goal
							? `⊙ Goal (${live.goal.turns}/${live.goal.budget} turns${live.goal.paused ? ', paused' : ''}): ${live.goal.text}`
							: 'No active goal. Set one with /goal <text>.'
					}
				};
			}
			if (text === 'pause') {
				if (!live.goal) return { result: { type: 'exec', output: 'No active goal.' } };
				live.goal.paused = true;
				emit(sessionId, 'status.update', { kind: 'goal', text: `⏸ Goal paused — ${live.goal.turns}/${live.goal.budget} turns used · /goal resume to continue` });
				return { result: { type: 'exec', output: '⏸ Goal paused.' } };
			}
			if (text === 'resume') {
				if (!live.goal) return { result: { type: 'exec', output: 'No active goal.' } };
				live.goal.paused = false;
				emit(sessionId, 'status.update', { kind: 'goal', text: `↻ Continuing toward goal (${live.goal.turns}/${live.goal.budget}): resumed by the user` });
				return { result: { type: 'exec', output: '▶ Goal resumed.' } };
			}
			if (text === 'clear') {
				live.goal = null;
				emit(sessionId, 'status.update', { kind: 'goal', text: 'Goal cleared' });
				return { result: { type: 'exec', output: '✓ Goal cleared.' } };
			}
			if (!text) return { result: { type: 'exec', output: 'No active goal. Set one with /goal <text>.' } };
			live.goal = { text, turns: 0, budget: state.config['goals.max_turns'], paused: false };
			emit(sessionId, 'status.update', { kind: 'goal', text: `⊙ Goal set (${live.goal.budget}-turn budget): ${text}` });
			return {
				result: {
					type: 'send',
					notice: `⊙ Goal set (${live.goal.budget}-turn budget): ${text}`,
					message: `[Continuing toward your standing goal]\nGoal: ${text}\n\nContinue working toward this goal.`
				}
			};
		}
		if (name === 'guard') {
			// The base is the whole remainder, as jarvis-guard reads it.
			const [, verb = '', base = ''] = text.replace(/^@\S+\s*/, '').match(/^(\S*)\s*([\s\S]*)$/) ?? [];
			if (verb === 'grants') return { result: { type: 'plugin', output: JSON.stringify(state.grants) } };
			if (verb === 'revoke') {
				const before = state.grants.length;
				state.grants = state.grants.filter((g) => g.base_key !== base);
				return { result: { type: 'plugin', output: state.grants.length < before ? `revoked: ${base}` : `No standing approval for ${base}.` } };
			}
		}
		if (name === 'persona') {
			// jarvis-personas: a switch is global, so the session prefix is
			// accepted and ignored, and every live session hears the event
			// (tests/contracts/persona.json).
			const words = text.replace(/^@\S+\s*/, '').trim().toLowerCase();
			// The contract's five, AURA first: a hand list here had lost her (T-0861).
			const names = PERSONAS;
			const display = (n) => (n === 'jarvis' || n === 'aura' ? n.toUpperCase() : n[0].toUpperCase() + n.slice(1));
			if (!words || words === 'status') return { result: { type: 'plugin', output: `${display(state.persona)} is primary; pool: ${names.filter((p) => p !== state.persona).map(display).join(', ')}.` } };
			if (!names.includes(words)) return { result: { type: 'plugin', output: `not a persona: '${words}' (one of ${PERSONAS.map(display).join(', ')})` } };
			if (words === state.persona) return { result: { type: 'plugin', output: `${display(words)} is already primary.` } };
			const previous = state.persona;
			state.persona = words;
			const payload = { active: words, display: display(words), voice: words, wake_word: words, pool: names.filter((p) => p !== words), previous, changed: true, at: new Date().toISOString() };
			for (const sid of state.live.keys()) emit(sid, 'persona.switch', payload);
			return { result: { type: 'plugin', output: `${display(words)} is primary now (was ${display(previous)}); ${state.live.size} HUD client(s) told.` } };
		}
		if (name === 'jgoal') {
			// A plugin command: not routed until the plugin is installed, and
			// its handler is not handed the session — the app prefixes `@<sid> `.
			if (!state.pluginInstalled) return err(4018, `not a quick/plugin/bundle/skill command: ${name}`);
			const at = /^@(\S+)\s*(.*)$/s.exec(text);
			if (!at) return { result: { type: 'plugin', output: 'jgoal: which session? Prefix the arg with @<session_id>.' } };
			const target = state.live.get(at[1]);
			if (!target) return { result: { type: 'plugin', output: `jgoal: no session ${at[1]}` } };
			const words = at[2].trim();
			if (words.startsWith('subgoal')) {
				const rest = words.slice(7).trim();
				target.subgoals = target.subgoals ?? [];
				if (!rest || rest === 'list') {
					return { result: { type: 'plugin', output: target.subgoals.length ? target.subgoals.map((s, i) => `${i + 1}. ${s}`).join('\n') : 'No subgoals.' } };
				}
				if (rest === 'clear') {
					target.subgoals = [];
					return { result: { type: 'plugin', output: '✓ Subgoals cleared.' } };
				}
				if (rest.startsWith('remove ')) {
					target.subgoals.splice(Number(rest.slice(7)) - 1, 1);
					return { result: { type: 'plugin', output: `✓ Subgoal ${rest.slice(7)} removed.` } };
				}
				target.subgoals.push(rest);
				return { result: { type: 'plugin', output: `✓ Subgoal ${target.subgoals.length} added: ${rest}` } };
			}
			if (words.startsWith('draft ')) {
				const goal = words.slice(6).trim();
				target.goal = { text: goal, turns: 0, budget: state.config['goals.max_turns'], paused: false };
				emit(at[1], 'status.update', { kind: 'goal', text: `⊙ Goal set (${target.goal.budget}-turn budget): ${goal}` });
				// The plugin sets the goal and starts its first turn itself.
				setTimeout(() => void runTurn(at[1], `[Continuing toward your standing goal]\nGoal: ${goal}\n\nContinue working toward this goal.`), 20);
				return { result: { type: 'plugin', output: `⊙ Goal drafted (${target.goal.budget}-turn budget): ${goal} · verify: files exist` } };
			}
			if (words.startsWith('gate add ')) {
				target.gates = [...(target.gates ?? []), words.slice(9).trim()];
				return { result: { type: 'plugin', output: `✓ Gate ${target.gates.length} added: ${words.slice(9).trim()}` } };
			}
			if (words === 'gate list') return { result: { type: 'plugin', output: (target.gates ?? []).map((g, i) => `${i + 1}. ${g}`).join('\n') || 'No gates.' } };
			if (words === 'gate clear') {
				target.gates = [];
				return { result: { type: 'plugin', output: '✓ Gates cleared.' } };
			}
			// The goals panel's Stop all (T-0291); the plugin's words. The mock keeps no
			// crew, so it never has helpers running to stop.
			if (words === 'crew stop') return { result: { type: 'plugin', output: 'This goal has no helpers running.' } };
			if (words === 'show') return { result: { type: 'plugin', output: target.goal ? `outcome: ${target.goal.text}` : 'No active goal.' } };
			return { result: { type: 'plugin', output: `jgoal ${words}: ok` } };
		}
		if (name === 'hud') {
			// The HUD plugin command: `@<sid> list | dismiss <id|*> | show <json>`.
			// Like the goal plugin's, the handler is not handed a session, so the
			// app prefixes the arg with @<live id>.
			const at = /^@(\S+)\s*(.*)$/s.exec(text);
			const targetId = at ? at[1] : sessionId;
			const target = state.live.get(targetId);
			if (!target) return { result: { type: 'plugin', output: '[]' } };
			const words = (at ? at[2] : text).trim();
			// `/hud capabilities`: the levels per capability (tests/contracts/capability-levels.json),
			// which the v2 Integrations and Settings screens read. capability_set is a tool, never this.
			if (words === 'capabilities') {
				return { result: { type: 'plugin', output: JSON.stringify(CAPABILITY_LEVELS) } };
			}
			if (words === 'routines' || words.startsWith('routines ')) {
				const [sub = 'view', ...rest] = words.slice('routines'.length).trim().split(/\s+/).filter(Boolean);
				const r = state.routines;
				const ref = rest.join(' ');
				const refuse = (error) => ({ result: { type: 'plugin', output: JSON.stringify({ ok: false, error }) } });
				if (sub === 'add' || sub === 'update') {
					// The console's builder (T-0549): one JSON object, unsplit, the plugin's console keys only.
					let args = null;
					try {
						args = JSON.parse(words.slice('routines'.length).trim().slice(sub.length));
					} catch {
						args = null;
					}
					if (!args || typeof args !== 'object' || Array.isArray(args)) return refuse(`routines ${sub} takes one JSON object`);
					const allowed = sub === 'add' ? ['when', 'do', 'name', 'once', 'cooldown_minutes'] : ['routine', 'enabled', 'do', 'when', 'once', 'cooldown_minutes'];
					const extra = Object.keys(args).filter((k) => !allowed.includes(k));
					if (extra.length) return refuse(`${extra.sort().join(', ')}: not set from the console; ask Jarvis for it`);
					const row =
						sub === 'add'
							? { id: `r${Math.max(0, ...r.routines.map((x) => Number(x.id.slice(1)) || 0)) + 1}`, name: args.name || String(args.do ?? '').slice(0, 60), enabled: true, cooldown_min: 10, once: false }
							: r.routines.find((x) => x.id === args.routine);
					if (!row) return refuse(`no routine matches '${args.routine}'`);
					if (row.runs_in === 'house') return refuse('this routine runs in the house: remove it and add it again to change it');
					let when = row.when;
					if (args.when) {
						when = { ...args.when };
						if ('place' in when) {
							const p = r.places.find((x) => x.label === when.place);
							if (!p) return refuse(`no remembered place called '${when.place}'`);
							when = { ...when, place: p.id, place_label: p.label };
						}
					}
					const instruction = args.do || row.do;
					if (!instruction || !when) return refuse('do and when are required');
					Object.assign(row, { when, do: instruction, place: when.place_label });
					if (args.cooldown_minutes !== undefined) row.cooldown_min = args.cooldown_minutes;
					if ('once' in args) row.once = Boolean(args.once);
					if ('enabled' in args) row.enabled = Boolean(args.enabled);
					const trigger = {
						car_connected: 'the phone connects to the car',
						car_disconnected: 'the car disconnects',
						arrive: `you arrive at ${when.place_label}`,
						leave: `you leave ${when.place_label}`,
						time: `it is ${when.at}`,
						pc_time: `it is ${when.at} on the PC`,
						asked: `you say "${when.phrase}"`,
						message: `${when.from} messages you`
					}[when.event] ?? String(when.event).replace('_', ' ');
					const on = when.days?.length ? ` on ${when.days.join(', ')}` : '';
					row.said = `${row.once ? 'Next time' : 'Every time'} ${trigger}${on}: ${instruction}`;
					if (sub === 'add') r.routines.push(row);
				} else if (sub === 'enable') {
					const on = rest[rest.length - 1] !== 'off';
					const row = r.routines.find((x) => x.id === rest[0]);
					if (row) row.enabled = on;
				} else if (sub === 'remove') r.routines = r.routines.filter((x) => x.id !== ref);
				else if (sub === 'forget-person') r.people = r.people.filter((x) => x.name !== ref);
				else if (sub === 'forget-place') {
					if (r.routines.some((x) => x.place === ref)) {
						return { result: { type: 'plugin', output: JSON.stringify({ ok: false, error: `${ref} is used by a routine; remove it first` }) } };
					}
					r.places = r.places.filter((x) => x.label !== ref);
				}
				return { result: { type: 'plugin', output: JSON.stringify(r) } };
			}
			const store = panelStore(target.stored);
			if (words === '' || words === 'list') {
				return { result: { type: 'plugin', output: JSON.stringify([...store.values()]) } };
			}
			if (words.startsWith('dismiss')) {
				const pid = words.slice('dismiss'.length).trim();
				if (pid === '*') store.clear();
				else store.delete(pid);
				emitPanel(target.stored, 'hud.panel.dismiss', { id: pid });
				return { result: { type: 'plugin', output: `dismissed ${pid}` } };
			}
			if (words.startsWith('show ')) {
				try {
					const spec = JSON.parse(words.slice(5).trim());
					const panel = {
						id: String(spec.id),
						kind: spec.kind ?? 'text',
						title: spec.title ?? '',
						payload: spec.payload ?? {},
						origin: spec.origin ?? 'agent',
						created_at: spec.created_at ?? new Date().toISOString(),
						rev: spec.rev ?? 1,
						ttl_s: spec.ttl_s ?? null
					};
					store.set(panel.id, panel);
					emitPanel(target.stored, 'hud.panel.show', panel);
					return { result: { type: 'plugin', output: `shown ${panel.id}` } };
				} catch {
					return { result: { type: 'plugin', output: 'hud show: bad json' } };
				}
			}
			return { result: { type: 'plugin', output: 'hud: list | dismiss <id|*> | show <json>' } };
		}
		if (name === 'hud_layout') {
			// The D30 layout plugin: `@<sid> get | arrange <preset> | undo | lock |
			// unlock | user <surface> <activity> <json> | forget`. Only the three the
			// window manager sends are answered here — the plugin's own tests cover
			// the rest, and a mock that reimplemented the split tree would be a second
			// authority on it.
			const at = /^@(\S+)\s*([\s\S]*)$/.exec(text);
			const words = (at ? at[2] : text).trim();
			const hout = (obj) => ({ result: { type: 'plugin', output: JSON.stringify(obj) } });
			if (words === 'lock' || words === 'unlock') {
				state.config['hud.lock_layout'] = words === 'lock';
				return hout({ ok: true, locked: state.config['hud.lock_layout'] });
			}
			if (words.startsWith('user')) {
				// `user <surface> <activity> <json>`: the layout begins at the first '{',
				// which is why the plugin refuses an activity name containing one.
				const brace = words.indexOf('{');
				if (brace === -1) return hout({ error: 'usage: user <surface> <activity> <json layout>' });
				const parts = words.slice(0, brace).split(/\s+/).filter(Boolean);
				const surface = parts[1] ?? 'web';
				const activity = parts[2] ?? 'default';
				let layout;
				try {
					layout = JSON.parse(words.slice(brace));
				} catch (e) {
					return hout({ error: `layout is not valid JSON (${e.message})` });
				}
				const saved = { ...(state.config['hud.layouts'] ?? {}) };
				saved[surface] = { ...(saved[surface] ?? {}), [activity]: layout };
				state.config['hud.layouts'] = saved;
				return hout({ ok: true, op: 'user', surface, activity, layout, saved: `hud.layouts.${surface}.${activity}` });
			}
			if (words === '' || words === 'get') {
				return hout({ ok: true, surface: 'web', layouts: state.config['hud.layouts'] ?? {}, locked: state.config['hud.lock_layout'] === true });
			}
			return hout({ error: 'hud_layout: get | arrange <preset> | undo | lock | unlock | user <surface> <activity> <json>' });
		}
		if (name === 'jmemory') {
			// The jarvis-hud plugin's side of the memory proposals queue, per
			// tests/contracts/memory-proposals.json: `@<sid> proposals | drop <id ...|all>`. No keep:
			// the real command has none (a keep is the model's call, so the gate draws its card).
			const mout = (obj) => ({ result: { type: 'plugin', output: typeof obj === 'string' ? obj : JSON.stringify(obj) } });
			const usage = 'usage: /jmemory @<session_id> proposals | drop <id ...|all>';
			const at = /^@(\S+)\s*([\s\S]*)$/.exec(text);
			if (!at) return mout(`${usage} (prefix the argument with @<session_id>)`);
			const words = at[2].trim();
			if (words === '' || words === 'proposals') return mout({ ok: true, open: state.memoryProposals });
			const mDrop = /^drop(?:\s+(.+))?$/.exec(words);
			if (!mDrop) return mout(usage);
			const named = (mDrop[1] ?? '').split(/[\s,]+/).filter(Boolean);
			if (!named.length) return mout({ ok: false, error: 'drop takes ids from the list, or "all"' });
			if (state.memoryDropBroken) return mout({ ok: false, error: 'the kill switch is on: nothing is changed until it is released' });
			const gone = state.memoryProposals.filter((p) => named[0] === 'all' || named.includes(p.id)).map((p) => p.id);
			state.memoryProposals = state.memoryProposals.filter((p) => !gone.includes(p.id));
			if (gone.length) memoryChanged();
			return mout({ ok: true, dropped: gone, open: state.memoryProposals.length });
		}
		if (name === 'jsettings') {
			// The jarvis-settings plugin, per docs/hermes-hud-protocol.md (jsettings):
			// `@<sid> list | get <key> | set <key> <json> | apply <id> | discard <id> |
			// proposals | history [n] | undo [key]`. Like the other plugin commands its handler is not handed a
			// session, so the app prefixes the arg with @<live id>. Every schema
			// leaf is served; a `set` from here proposes (the matrix asks about
			// `settings_set` on every key) and `apply` is what writes; a leaf the
			// compose file fixes is refused with the plugin's sentence; an unknown
			// or invalid one is `{"error": …}` in the output.
			const at = /^@(\S+)\s*([\s\S]*)$/.exec(text);
			const words = (at ? at[2] : text).trim();
			const jout = (obj) => ({ result: { type: 'plugin', output: JSON.stringify(obj) } });
			// Written values live under the schema key; the seed and the older
			// commands (`config.*`, the goal budget) use the Hermes key, so both are read.
			const current = (spec) => {
				if (spec.key in state.config) return [state.config[spec.key], 'config'];
				if (spec.hermes_key && spec.hermes_key in state.config) return [state.config[spec.hermes_key], 'config'];
				return [spec.default ?? null, 'default'];
			};
			const shown = (spec, value) => (spec.secret && value ? '[redacted]' : value);
			const row = (spec) => {
				const [value, source] = current(spec);
				return {
					key: spec.key,
					value: shown(spec, value),
					type: spec.type,
					restart_needed: spec.restart_needed === true,
					doc: spec.description,
					source,
					settable: spec.applies !== 'compose',
					applies: spec.applies
				};
			};
			const proposalShown = (p) => ({ ...p, before: shown(SETTINGS_BY_KEY[p.key], p.before), after: shown(SETTINGS_BY_KEY[p.key], p.after) });
			if (words === '' || words === 'list') return jout(SETTINGS_CATALOGUE.map(row));
			if (words === 'proposals') return jout(state.proposals.map(proposalShown));
			// `history [n]`: the changes made, newest first (20, at most 50), a secret masked on both
			// sides; `undoable` only on the newest row of a key, and never on a secret (T-0700).
			const mHistory = /^history(?:\s+(\d+))?\s*$/.exec(words);
			if (mHistory && state.historyBroken) return jout({ error: 'the change log could not be read' });
			if (mHistory) {
				const seen = new Set();
				return jout(
					state.changes
						.slice(-Math.max(1, Math.min(Number(mHistory[1]) || 20, 50)))
						.reverse()
						.map((c) => {
							const spec = SETTINGS_BY_KEY[c.key];
							const undoable = !seen.has(c.key) && !spec.secret && spec.applies !== 'compose';
							seen.add(c.key);
							return { ts: c.ts, actor: c.actor, key: c.key, before: shown(spec, c.before), after: shown(spec, c.after), undoable };
						})
				);
			}
			// `undo [key]`: the last change (of that key) goes back. The plugin holds a security
			// setting for the person's yes; the contract carries no such flag, so the Approvals
			// group stands in for those here.
			const mUndo = /^undo(?:\s+(\S+))?\s*$/.exec(words);
			if (mUndo) {
				const last = state.changes.findLast((c) => !mUndo[1] || c.key === mUndo[1]);
				if (!last) return jout({ error: mUndo[1] ? `no recorded change of ${mUndo[1]} to undo` : 'no recorded change to undo' });
				const spec = SETTINGS_BY_KEY[last.key];
				if (spec.secret) return jout({ error: `${spec.key} is a secret: the old value was not kept, so it cannot be put back` });
				if (spec.group === 'Approvals') {
					const proposal = { id: Math.random().toString(16).slice(2, 14), key: spec.key, before: last.after, after: last.before, source: 'config', applies: spec.applies, ts: new Date().toISOString() };
					state.proposals.push(proposal);
					proposalsChanged();
					return jout({ status: 'proposed', proposal });
				}
				return jout(writeSetting(spec, last.before, 'command'));
			}
			const mGet = /^get\s+(\S+)\s*$/.exec(words);
			if (mGet) {
				const spec = SETTINGS_BY_KEY[mGet[1]];
				if (!spec) return jout({ error: `unknown setting '${mGet[1]}'; jsettings list names the ${SETTINGS_CATALOGUE.length} it serves` });
				return jout(row(spec));
			}
			const mSet = /^set\s+(\S+)\s+([\s\S]+)$/.exec(words);
			if (mSet) {
				const key = mSet[1];
				const spec = SETTINGS_BY_KEY[key];
				if (!spec) return jout({ error: `unknown setting '${key}'; jsettings list names the ${SETTINGS_CATALOGUE.length} it serves` });
				if (spec.applies === 'compose') {
					return jout({ error: `${key} is fixed outside the container (${spec.env} in the host .env / compose file); edit .env and recreate the stack` });
				}
				let value;
				try {
					value = JSON.parse(mSet[2]);
				} catch (e) {
					return jout({ error: `value is not valid JSON: ${e.message}` });
				}
				const bad = validateSetting(spec, value);
				if (bad) return jout({ error: bad });
				const [before, source] = current(spec);
				const proposal = { id: Math.random().toString(16).slice(2, 14), key, before, after: value, source, applies: spec.applies, ts: new Date().toISOString() };
				state.proposals.push(proposal);
				proposalsChanged();
				return jout({ status: 'proposed', proposal: proposalShown(proposal) });
			}
			const mAct = /^(apply|discard)\s+(\S+)\s*$/.exec(words);
			if (mAct) {
				const [, verb, id] = mAct;
				const index = state.proposals.findIndex((p) => p.id === id);
				if (index < 0) return jout({ error: `no proposal '${id}'` });
				const [proposal] = state.proposals.splice(index, 1);
				proposalsChanged();
				if (verb === 'discard') return jout({ status: 'discarded', id });
				return jout({ ...writeSetting(SETTINGS_BY_KEY[proposal.key], proposal.after, 'command'), proposal: id });
			}
			return jout({ error: 'usage: /jsettings @<sid> get <key> | set <key> <json> | list | apply <id> | discard <id> | proposals | history [n] | undo [key]' });
		}
		// `/subgoal` is a reserved built-in name no plugin may register: never routed here.
		if (name === 'subgoal') return err(4018, `not a quick/plugin/bundle/skill command: ${name}`);
		if (name === 'prompt') return { result: { type: 'prefill', message: text } };
		return err(4018, `not a quick/plugin/bundle/skill command: ${name}`);
	}

	async function handle(ws, frame) {
		const { id, method, params = {} } = frame;
		const reply = (body) => send(ws, { jsonrpc: '2.0', id, ...body });
		switch (method) {
			case 'session.create': {
				const stored = newStored();
				const live = attach(stored, ws);
				reply({ result: { session_id: live, stored_session_id: stored, message_count: 0, messages: [], info: sessionInfo(live) } });
				emit(live, 'session.info', sessionInfo(live));
				return;
			}
			case 'session.list':
				reply({
					result: {
						sessions: [...state.sessions.values()]
							.filter((s) => s.messages.length)
							.sort((a, b) => b.started_at - a.started_at)
							.map(({ messages: _m, ...row }) => row)
					}
				});
				return;
			case 'session.resume': {
				const stored = state.sessions.get(String(params.session_id));
				if (!stored) return reply(err(4001, 'session not found'));
				const live = attach(stored.id, ws);
				// A session mid-turn carries over: the turn, the goal and what it is
				// waiting on move to the new live id, and the wait is re-announced.
				const previous = [...state.live.entries()].find(([, l]) => l.stored === stored.id && (l.awaiting || l.turn || l.goal));
				const carried = previous?.[1];
				if (carried) {
					const mine = state.live.get(live);
					Object.assign(mine, { goal: carried.goal, turn: carried.turn, awaiting: carried.awaiting, gates: carried.gates, subgoals: carried.subgoals });
					state.live.delete(previous[0]);
				}
				reply({ result: { session_id: live, resumed: true, message_count: stored.messages.length, messages: stored.messages, info: sessionInfo(live), running: Boolean(carried?.turn) } });
				emit(live, 'session.info', sessionInfo(live));
				if (carried?.awaiting?.kind === 'approval') {
					emit(live, 'approval.request', { request_id: carried.awaiting.request_id, command: '<terminal> (plugin approval rule)', pattern_key: 'plugin_rule:terminal:destructive', description: 'terminal rm deletes files and the shell has no undo — command: rm -rf /tmp/scratch', allow_permanent: true, allow_session: true, choices: ['once', 'session', 'always', 'deny'] });
				} else if (carried?.awaiting?.kind === 'clarify') {
					emit(live, 'clarify.request', { request_id: carried.awaiting.request_id, question: 'Front door or garage door?', options: ['front', 'garage'] });
				}
				return;
			}
			case 'session.history': {
				const live = state.live.get(String(params.session_id));
				const stored = live && state.sessions.get(live.stored);
				if (!stored) return reply(err(4001, 'session not found'));
				return reply({ result: { count: stored.messages.length, messages: stored.messages } });
			}
			case 'session.status': {
				const live = state.live.get(String(params.session_id));
				return reply(live ? { result: { output: `Model: ${MODEL}\nAgent Running: ${live.turn ? 'Yes' : 'No'}` } } : err(4001, 'session not found'));
			}
			case 'session.delete': {
				const stored = String(params.session_id);
				if (!state.sessions.has(stored)) return reply(err(4007, 'session not found'));
				state.sessions.delete(stored);
				reply({ result: { deleted: true } });
				emit('', 'sessions.changed', {});
				return;
			}
			case 'session.interrupt': {
				const live = state.live.get(String(params.session_id));
				if (!live) return reply(err(4001, 'session not found'));
				if (live.turn) {
					live.turn = null;
					emit(String(params.session_id), 'message.complete', { text: '', status: 'interrupted' });
				}
				return reply({ result: { status: live.turn ? 'stopping' : 'idle' } });
			}
			case 'session.steer':
				return reply({ result: { queued: true } });
			case 'subagent.interrupt': {
				const subagentId = String(params.subagent_id ?? '');
				if (!subagentId) return reply(err(4000, 'subagent_id required'));
				// The gateway answers first; the child's own complete follows on the
				// parent session. The mock does not track which session a child is
				// on, so every live one hears it — a spec asserts the row it started.
				reply({ result: { found: true, subagent_id: subagentId } });
				for (const sid of state.live.keys()) emit(sid, 'subagent.complete', { subagent_id: subagentId, status: 'interrupted', summary: 'stopped from the console' });
				return;
			}
			case 'image.attach_bytes': {
				// Hermes' shape: queued for the next prompt.submit on that session.
				const live = state.live.get(String(params.session_id));
				if (!live) return reply(err(4001, 'session not found'));
				const data = String(params.content_base64 ?? params.data ?? '');
				if (!data) return reply(err(4015, 'content_base64 required'));
				live.photos = (live.photos ?? 0) + 1;
				return reply({ result: { attached: true, path: `/images/upload_${live.photos}.jpg`, count: live.photos, remainder: '', text: '[User attached image: upload.jpg]', bytes: Math.floor((data.length * 3) / 4) } });
			}
			case 'prompt.submit': {
				const sessionId = String(params.session_id);
				if (!state.live.has(sessionId)) return reply(err(4001, 'session not found'));
				reply({ result: { status: 'streaming' } });
				void runTurn(sessionId, String(params.text ?? ''));
				return;
			}
			case 'command.dispatch':
				return reply(dispatch(String(params.session_id), String(params.name ?? ''), params.arg));
			case 'commands.catalog':
				return reply({ result: CATALOG });
			case 'approval.respond': {
				const sessionId = String(params.session_id);
				const live = state.live.get(sessionId);
				if (!live) return reply(err(4001, 'session not found'));
				const choice = String(params.choice);
				if (!['once', 'session', 'always', 'deny'].includes(choice)) return reply(err(4003, `bad choice: ${choice}`));
				reply({ result: { ok: true } });
				if (live.awaiting?.kind === 'approval' && live.awaiting.request_id === params.request_id) void afterApproval(sessionId, live, choice);
				return;
			}
			case 'clarify.respond': {
				const sessionId = String(params.session_id);
				const live = state.live.get(sessionId);
				if (!live) return reply(err(4001, 'session not found'));
				reply({ result: { ok: true } });
				if (live.awaiting?.kind === 'clarify') void afterClarify(sessionId, live, String(params.answer ?? ''));
				return;
			}
			case 'config.get': {
				const key = String(params.key);
				if (!(key in state.config)) return reply(err(4002, `unknown config key: ${key}`));
				return reply({ result: { key, value: state.config[key] } });
			}
			case 'config.set': {
				const key = String(params.key);
				// Hermes' session-scoped switch (methods_config_set._set_model): this conversation
				// answers on the new model from its next turn, and session.info says which.
				const live = state.live.get(String(params.session_id ?? ''));
				if (key === 'model' && live) {
					live.model = String(params.value);
					emit(String(params.session_id), 'session.info', sessionInfo(String(params.session_id)));
					return reply({ result: { value: live.model, warning: '', confirm_required: false, scope: 'session' } });
				}
				if (!(key in state.config)) return reply(err(4002, `unknown config key: ${key}`));
				state.config[key] = params.value;
				return reply({ result: { key, value: params.value } });
			}
			// --- test hooks ----------------------------------------------------------
			case 'test.emit': {
				for (const ev of params.events ?? []) emit(ev.session_id ?? String(params.session_id ?? ''), ev.type, ev.payload ?? {});
				return reply({ result: { emitted: (params.events ?? []).length } });
			}
			// A setting written the way the model's `settings_set` writes it (the command's `set`
			// only proposes here): `{key, value, actor?}`. `history: false` fails the next read of it.
			case 'test.setting': {
				if ('history' in params) state.historyBroken = params.history === false;
				const spec = SETTINGS_BY_KEY[String(params.key ?? '')];
				return reply({ result: spec ? writeSetting(spec, params.value, String(params.actor ?? 'tool')) : {} });
			}
			// What the memory_propose tool does in a conversation: one more waits, and every console
			// hears the count. `{text, store?, by?, read?, at?, instruction?}`; `quiet: true` is a
			// routine's own process, which reaches no console. `drop: false` refuses the next drops.
			case 'test.memory_propose': {
				if ('drop' in params) state.memoryDropBroken = params.drop === false;
				if (typeof params.text !== 'string') return reply({ result: {} });
				const id = `p${state.memoryNext++}`;
				state.memoryProposals.push({
					id,
					text: params.text,
					store: params.store === 'memory' ? 'memory' : 'fact',
					by: String(params.by ?? 'cron'),
					read: Array.isArray(params.read) ? params.read.map(String) : [],
					at: String(params.at ?? new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC'),
					worded_as_instruction: params.instruction === true
				});
				if (params.quiet !== true) memoryChanged();
				return reply({ result: { id } });
			}
			case 'test.memory_announce': {
				// The gateway's look at the queue file (T-1506): a quiet proposal is heard now.
				memoryChanged();
				return reply({ result: {} });
			}
			case 'test.reset':
				state.memoryProposals = [];
				state.memoryNext = 1;
				state.memoryDropBroken = false;
				state.sessions = seedSessions();
				state.routines = seedRoutines();
				state.jobs = seedJobs();
				state.config = seedConfig();
				state.proposals = [];
				state.changes = [];
				state.historyBroken = false;
				state.grants = seedGrants();
				state.deviceKeys = seedDeviceKeys();
				state.panels = new Map();
				state.board = 'ok';
				state.status = { model: MODEL, state: 'ready', warming_up: false };
				state.pluginInstalled = false;
				state.persona = 'jarvis';
				state.theme = '';
				state.themes = new Map();
				return reply({ result: { ok: true } });
			default:
				return reply(err(-32601, `unknown method: ${method}`));
		}
	}

	// --- HTTP ------------------------------------------------------------------------
	const readJson = (req) =>
		new Promise((resolve) => {
			const chunks = [];
			req.on('data', (c) => chunks.push(c));
			req.on('end', () => {
				try {
					resolve(JSON.parse(Buffer.concat(chunks).toString() || '{}'));
				} catch {
					resolve({});
				}
			});
		});
	const json = (res, status, body) => {
		res.writeHead(status, { 'content-type': 'application/json' });
		res.end(JSON.stringify(body));
	};

	const server = http.createServer(async (req, res) => {
		const url = new URL(req.url ?? '/', 'http://mock');
		const path = url.pathname;
		if (path === '/health') return json(res, 200, { status: 'ok', service: 'mock-hermes' });
		// A device's own key reads the probe, which names the device and carries no key (T-0569).
		const holder = state.deviceKeys.find((k) => req.headers.authorization === `Bearer ${k.key}`);
		if (holder && path === '/api/pair' && req.method === 'GET') {
			return json(res, 200, { url: 'http://mock', model: MODEL, kind: 'hermes-adapter', device: publicKey(holder) });
		}
		if (req.headers.authorization !== `Bearer ${MOCK_KEY}`) return json(res, 401, { detail: 'Unauthorized' });
		log(`${req.method} ${path}`);
		if (path === '/api/status') {
			return json(res, 200, {
				ok: !state.killSwitch.engaged,
				// The adapter's one word (T-0583, tests/contracts/status-words.json): the two states this
				// mock can be put in, worded as status_word words them.
				status: state.killSwitch.engaged
					? { word: 'degraded', reason: 'kill switch engaged' }
					: ['starting', 'loading'].includes(state.status.state)
						? { word: 'degraded', reason: 'model warming' }
						: { word: 'ready', reason: '' },
				// The adapter reads this off the Hermes volume (jarvis-adapter's
				// status.py). It is here because a server payload key the mock does
				// not answer is a key the console tests pass without and the real
				// console breaks on.
				kill_switch: state.killSwitch,
				// The same volume: which persona is primary (tests/contracts/persona.json).
				persona: { active: state.persona, since: '', previous: '', ...(state.theme ? { theme: state.theme } : {}) },
				model: { ...state.status, loaded: [MODEL] },
				hermes: { api_server: { ok: true, version: '0.21.0' }, backend: { ok: true, version: '0.21.0' } },
				llama_swap: { ok: true, url: 'http://jarvis-models:8000' },
				latency_ms: 3
			});
		}
		if (path === '/api/pair' && req.method === 'POST') {
			const body = await readJson(req);
			const n = state.deviceKeys.length + 1;
			const minted = {
				id: `k-minted-${n}`,
				name: String(body.name || 'device').slice(0, 64),
				kind: ['phone', 'desktop'].includes(body.kind) ? body.kind : 'other',
				created: Date.now() / 1000,
				last_seen: null,
				house_token: typeof body.house_token === 'string' ? body.house_token : null,
				key: `mock-device-key-minted-${n}`
			};
			state.deviceKeys.push(minted);
			return json(res, 200, { url: 'http://mock', key: minted.key, model: MODEL, kind: 'hermes-adapter', device: publicKey(minted) });
		}
		if (path === '/api/pair') return json(res, 200, { url: 'http://mock', key: MOCK_KEY, model: MODEL, kind: 'hermes-adapter' });
		if (path === '/api/devices/keys' && req.method === 'GET') return json(res, 200, { keys: state.deviceKeys.map(publicKey) });
		const revoking = /^\/api\/devices\/keys\/([^/]+)$/.exec(path);
		if (revoking && req.method === 'DELETE') {
			const id = decodeURIComponent(revoking[1]);
			if (!state.deviceKeys.some((k) => k.id === id)) return json(res, 404, { detail: 'no such device key' });
			state.deviceKeys = state.deviceKeys.filter((k) => k.id !== id);
			return json(res, 200, { ok: true, id });
		}
		if (path.startsWith('/api/artifacts/') && req.method === 'GET') {
			// The media fixtures, served the way the adapter serves an artifact
			// (`GET /api/artifacts/<id>`: inline, `accept-ranges`, a 206 for a
			// range). A `<video>` asks for `bytes=0-` first and seeks with more of
			// them; a mock that answered every request with the whole file would
			// pass a test the real adapter's 206 fails, or the other way round.
			const art = MEDIA_BY_ID[path.slice('/api/artifacts/'.length)];
			if (!art) return json(res, 404, { detail: 'no such artifact' });
			const bytes = readFileSync(art.file);
			const headers = { 'content-type': art.mime, 'content-disposition': 'inline', 'accept-ranges': 'bytes', 'cache-control': 'no-store' };
			const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? '');
			if (range && (range[1] || range[2])) {
				const start = range[1] ? Number(range[1]) : Math.max(0, bytes.length - Number(range[2]));
				const end = range[1] && range[2] ? Math.min(Number(range[2]), bytes.length - 1) : bytes.length - 1;
				if (start > end || start >= bytes.length) {
					res.writeHead(416, { 'content-range': `bytes */${bytes.length}` });
					return res.end();
				}
				res.writeHead(206, { ...headers, 'content-range': `bytes ${start}-${end}/${bytes.length}`, 'content-length': end - start + 1 });
				return res.end(bytes.subarray(start, end + 1));
			}
			res.writeHead(200, { ...headers, 'content-length': bytes.length });
			return res.end(bytes);
		}
		if (path === '/api/audio/transcribe' && req.method === 'POST') {
			const body = await readJson(req);
			state.transcripts += 1;
			const heard = typeof body.data_url === 'string' && body.data_url.length > 40;
			return json(res, 200, { ok: true, transcript: heard ? TRANSCRIPT : '', provider: 'mock' });
		}
		// Refused voices held for "That was me" (the adapter's speaker.Unrecognised).
		// `state.unrecognised` is seeded by a test; a claim takes one off and is recorded.
		if (path === '/api/speaker/unrecognised' && req.method === 'GET') {
			return json(res, 200, { unrecognised: state.unrecognised ?? [] });
		}
		const claimed = /^\/api\/speaker\/unrecognised\/([^/]+)\/mine$/.exec(path);
		if (claimed && req.method === 'POST') {
			const held = (state.unrecognised ?? []).find((u) => u.id === claimed[1]);
			if (!held) return json(res, 404, { ok: false, error: 'that voice is no longer held' });
			state.unrecognised = state.unrecognised.filter((u) => u.id !== held.id);
			(state.claims ??= []).push(held.id);
			return json(res, 200, { ok: true, label: held.nearest, source: held.surface });
		}
		if (path === '/api/audio/speak' && req.method === 'POST') {
			const body = await readJson(req);
			state.spoken.push(String(body.text ?? ''));
			// Who said it: the persona's voice the console asked for (the adapter
			// passes it to CosyVoice; `jarvis` when none), so a test can prove a
			// subagent's report went out in its own voice.
			state.voices.push(String(body.voice ?? 'jarvis'));
			// The shape the running adapter answers with (`data_url`, not the
			// doc's `audio`), so the console proves it reads the real one.
			// A longer clip when a test asked for one (POST /api/test/speech): the
			// barge-in spec needs a reply still being spoken when the key goes down.
			const clip = state.speechSeconds ? `data:audio/wav;base64,${makeWav(state.speechSeconds).toString('base64')}` : WAV_DATA_URL;
			return json(res, 200, { ok: true, voice: String(body.voice ?? 'jarvis'), data_url: clip, mime_type: 'audio/wav', provider: 'mock' });
		}
		// The streamed line (the adapter's `speak-pcm`): 16-bit PCM at 24 kHz in
		// pieces, a pause between them, the way CosyVoice makes it. The console
		// takes this route first and the whole-line one when it answers 404, so
		// a test that wants the old shape sets `state.noStream`.
		if (path === '/api/audio/speak-pcm' && req.method === 'POST') {
			const body = await readJson(req);
			if (state.noStream) return json(res, 404, { detail: 'this voice does not stream' });
			state.spoken.push(String(body.text ?? ''));
			state.voices.push(String(body.voice ?? 'jarvis'));
			state.streamed.push(String(body.text ?? ''));
			const seconds = state.speechSeconds || 0.25;
			const wav = makeWav(seconds, 24000);
			const pcm = wav.subarray(44);
			res.writeHead(200, { 'content-type': 'audio/pcm', 'x-sample-rate': '24000', 'x-jarvis-tts-engine': 'cosyvoice', 'cache-control': 'no-store' });
			const pieces = 4;
			const step = Math.ceil(pcm.length / pieces / 2) * 2;
			let at = 0;
			const send = () => {
				if (at >= pcm.length) return res.end();
				res.write(pcm.subarray(at, Math.min(pcm.length, at + step)));
				at += step;
				setTimeout(send, 30);
			};
			send();
			return;
		}
		if (path === '/api/test/models' && req.method === 'POST') {
			const body = await readJson(req);
			state.mm = freshModels();
			state.mmMode = String(body.mode ?? 'ok');
			if (state.mmMode === 'empty') Object.assign(state.mm, { models: [], local: new Set() });
			if (body.missing_llama === true) {
				state.mm.models.push(structuredClone(MISSING_LLAMA.row));
				state.mm.files[MISSING_LLAMA.row.id] = MISSING_LLAMA.file;
			}
			if (typeof body.free_bytes === 'number') state.mm.disk.free_bytes = body.free_bytes;
			return json(res, 200, { ok: true });
		}
		if (path === '/api/models' || path.startsWith('/api/models/')) {
			const mm = (state.mm ??= freshModels());
			const fail = (status, error) => json(res, status, { ok: false, error });
			const sub = path.slice('/api/models'.length).replace(/^\//, '');
			const gpus = MODELS_API.endpoints.gpus.example;
			if (state.mmMode === 'error') return fail(500, 'The model manager broke.');
			if (state.mmMode === 'gone') return fail(503, 'The model manager is away.');
			if (sub === '' && req.method === 'GET') {
				// Each read moves a download on: the console polls, and a test sees the bar fill.
				for (const d of mm.downloads) {
					if (d.state !== 'running') continue;
					d.done_bytes = Math.min(d.total_bytes, d.done_bytes + Math.ceil(d.total_bytes / 3));
					if (d.done_bytes >= d.total_bytes) {
						d.state = 'done';
						mm.local.add(d.repo);
						// A row waiting for exactly the file this download fetched has its weights now.
						for (const m of mm.models) if (m.state === 'missing' && m.repo === d.repo && mm.files[m.id] === d.file) m.state = 'unloaded';
					}
				}
				for (const m of mm.models) if (m.state === 'starting') m.state = 'loaded';
				// As the manager's _state: a repo with a download running reads "downloading" on every row of it.
				const running = new Set(mm.downloads.filter((d) => d.state === 'running').map((d) => d.repo));
				const models = mm.models.map((m) => (running.has(m.repo) ? { ...m, state: 'downloading' } : m));
				// `file` is the job's own, not a list field: the list carries what the contract's example does.
				const downloads = mm.downloads.map(({ file: _file, ...d }) => d);
				return json(res, 200, { ok: true, models, downloads, disk: mm.disk });
			}
			if (sub === 'gpus' && req.method === 'GET') return json(res, 200, gpus);
			if (sub === 'search' && req.method === 'GET') {
				const q = (url.searchParams.get('q') ?? '').toLowerCase();
				const hits = SEARCH_CATALOGUE.filter((r) => r.repo.toLowerCase().includes(q) || !q).map((r) => ({ ...r, local: mm.local.has(r.repo) }));
				return json(res, 200, { ok: true, results: hits });
			}
			if (sub === 'downloads' && req.method === 'POST') {
				let { repo, file } = await readJson(req);
				const hit = SEARCH_CATALOGUE.find((r) => r.repo === repo);
				if (!hit) return fail(404, `No such repository: ${repo}.`);
				if (file !== undefined && ggufFileError(file)) return fail(400, ggufFileError(file));
				// What the console sees of a job: the list's shape, not the mock's own `file`.
				const view = ({ file: _file, ...d }) => d;
				// The manager answers a repo already downloading with the running job, not a refusal.
				const running = mm.downloads.find((d) => d.repo === repo && d.state === 'running');
				if (running) return json(res, 200, { ok: true, download: view(running) });
				// With no file: the one a llama.cpp row of this repo has lost, from the manager's own config.
				const gone = mm.models.find((m) => m.repo === repo && m.state === 'missing' && mm.files[m.id]);
				file ??= gone && mm.files[gone.id];
				if (hit.gated) return fail(...refusal('gated', { repo }));
				const free = Math.max(mm.disk.free_bytes - MODEL_LIMITS.disk_reserve_bytes, 0);
				if (hit.size_bytes > free) {
					return fail(...refusal('disk_full', { repo, need_gb: (hit.size_bytes / 1e9).toFixed(1), free_gb: (free / 1e9).toFixed(1), reserve_gb: MODEL_LIMITS.disk_reserve_bytes / GIB }));
				}
				const download = { repo, file, state: 'running', done_bytes: 0, total_bytes: hit.size_bytes, error: null };
				mm.downloads = mm.downloads.filter((d) => d.repo !== repo).concat(download);
				return json(res, 200, { ok: true, download: view(download) });
			}
			if (sub === '' && req.method === 'POST') {
				const { id, repo, engine = 'vllm', file, settings = {} } = await readJson(req);
				if (!id) return fail(...refusal('id_invalid'));
				if (!repo) return fail(...refusal('repo_invalid'));
				if (!MODELS_API.create_body.engines.includes(engine)) return fail(...refusal('engine_unknown', { engines: MODELS_API.create_body.engines.join(', ') }));
				if (engine === 'vllm' && file !== undefined) return fail(...refusal('file_on_vllm'));
				if (engine === 'llama.cpp' && ggufFileError(file)) return fail(400, file ? ggufFileError(file) : GGUF.missing_error);
				if (mm.models.some((m) => m.id === id)) return fail(...refusal('model_exists', { id }));
				const bad = modelSettingsError(settings);
				if (bad) return fail(400, bad);
				if (!mm.local.has(repo)) return fail(...refusal('not_downloaded', { what: file || repo }));
				const hit = SEARCH_CATALOGUE.find((r) => r.repo === repo);
				mm.models.push({ id, name: repo.split('/').pop(), repo, state: 'unloaded', on_disk_bytes: hit?.size_bytes ?? 0, settings: { engine, kv_cache_dtype: 'auto', quantization: null, ...settings } });
				return json(res, 200, { ok: true, model: { id, state: 'unloaded' } });
			}
			const one = /^([^/]+)(?:\/(load|unload))?$/.exec(sub);
			const model = one && mm.models.find((m) => m.id === decodeURIComponent(one[1]));
			if (!one) return fail(404, 'Not a model manager route.');
			if (!model) return fail(404, `No model called ${decodeURIComponent(one[1])}.`);
			if (one[2] === 'load' && req.method === 'POST') {
				for (const m of mm.models) if (m !== model && m.state === 'loaded') m.state = 'unloaded';
				model.state = 'starting';
				// Where it lands (T-0548), as the contract's load example says.
				return json(res, 200, { ok: true, model: { id: model.id, state: model.state }, gpus: MODELS_API.endpoints.load.example.gpus });
			}
			if (one[2] === 'unload' && req.method === 'POST') {
				model.state = 'unloaded';
				return json(res, 200, { ok: true, model: { id: model.id, state: model.state } });
			}
			if (!one[2] && req.method === 'PATCH') {
				const { settings = {} } = await readJson(req);
				const bad = modelSettingsError(settings);
				if (bad) return fail(400, bad);
				Object.assign(model.settings, settings);
				return json(res, 200, { ok: true, model: { id: model.id, state: model.state } });
			}
			if (!one[2] && req.method === 'DELETE') {
				mm.models = mm.models.filter((m) => m !== model);
				const freed = url.searchParams.get('weights') === '1' ? model.on_disk_bytes : 0;
				if (freed) {
					mm.local.delete(model.repo);
					mm.disk.free_bytes += freed;
				}
				return json(res, 200, { ok: true, freed_bytes: freed });
			}
			return fail(405, 'Not a model manager verb.');
		}
		if (path === '/api/jobs' && req.method === 'GET') return json(res, 200, { jobs: state.jobs });
		// The Hermes memory layers the orb's mid field is built from: one row per
		// layer kind, so a console test can tell the graph read them apart.
		if (path === '/api/memory/layers' && req.method === 'GET')
			// `unreadable` names the layers the adapter could not read (it is another
			// uid on a cd-through mount in production); empty here, present so the
			// console is exercised against the real shape.
			return json(res, 200, { entries: state.memoryLayers, notes: [], counts: countBySource(state.memoryLayers), unreadable: [] });
		const job = /^\/api\/jobs\/([^/]+)\/(run|pause|resume)$/.exec(path);
		if (job && req.method === 'POST') {
			const row = state.jobs.find((j) => j.id === job[1]);
			if (!row) return json(res, 404, { detail: 'no such job' });
			if (job[2] === 'run') {
				row.last_run_at = new Date().toISOString();
				row.last_status = 'ok';
				return json(res, 200, { ok: true, job: row, started: true });
			}
			row.enabled = job[2] === 'resume';
			row.state = row.enabled ? 'scheduled' : 'paused';
			row.paused_at = row.enabled ? null : new Date().toISOString();
			return json(res, 200, { ok: true, job: row });
		}
		if (path === '/v1/toolsets') return json(res, 200, { object: 'list', platform: 'api_server', data: TOOLSETS });
		if (path === '/v1/skills') return json(res, 200, { object: 'list', data: SKILLS });
		if (path === '/api/test/layouts' && req.method === 'POST') {
			// Forget every saved layout, the mode and the lock: the HUD reports the
			// person's arrangement into `hud.layouts` and restores it on the next
			// load, so a spec that dragged a panel or cycled to Focus would hand the
			// next spec a surface with its panel in the rail. Each HUD spec starts
			// from the shipped layouts by calling this first.
			const fresh = seedConfig();
			for (const key of Object.keys(state.config)) if (key.startsWith('hud.')) state.config[key] = fresh[key];
			return json(res, 200, { ok: true });
		}
		if (path === '/api/test/drop' && req.method === 'POST') {
			// A gateway restart, as the console sees it: every socket gone at
			// once -- Hermes' and the house's, which is what one `docker compose
			// up` does to an open console -- the servers still there to dial
			// again. For the reconnect test: the console must come back on its
			// own, no click.
			let n = 0;
			for (const ws of clients) {
				ws.terminate();
				n += 1;
			}
			const house = options.house?.dropAll?.() ?? 0;
			return json(res, 200, { dropped: n, house });
		}
		if (path === '/api/test/unrecognised' && req.method === 'POST') {
			const body = await readJson(req);
			state.unrecognised = Array.isArray(body.unrecognised) ? body.unrecognised : [];
			state.claims = [];
			return json(res, 200, { ok: true });
		}
		if (path === '/api/test/claims' && req.method === 'GET') return json(res, 200, { claims: state.claims ?? [] });
		if (path === '/api/board' && req.method === 'GET') {
			if (state.board === 'error') return json(res, 500, { detail: 'board broke' });
			if (state.board === 'gone') return json(res, 503, { detail: 'adapter away' });
			return json(res, 200, boardFixture(state.board, Date.now() / 1000));
		}
		if (path.startsWith('/api/themes/') && req.method === 'GET') {
			const pack = state.themes.get(decodeURIComponent(path.slice('/api/themes/'.length)));
			return pack ? json(res, 200, pack) : json(res, 404, { detail: 'no such theme' });
		}
		if (path === '/api/test/theme' && req.method === 'POST') {
			// What theme_draft and theme_apply send (drafts.py): `{op: 'preview', pack}` shows a
			// draft on every live session; `{op: 'apply', id, pack?}` wears it (no pack: shipped).
			const body = await readJson(req);
			if (body.op === 'preview') {
				state.themes.set(body.pack.id, body.pack);
				for (const sid of state.live.keys()) emit(sid, 'theme.preview', { id: body.pack.id, name: body.pack.name, pack: body.pack });
			} else {
				if (body.pack) state.themes.set(body.id, body.pack);
				state.theme = body.id;
				for (const sid of state.live.keys()) emit(sid, 'theme.apply', { id: body.id, pack: body.pack ?? null });
			}
			return json(res, 200, { ok: true, live: state.live.size });
		}
		if (path === '/api/test/board' && req.method === 'POST') {
			state.board = String((await readJson(req)).mode ?? 'ok');
			return json(res, 200, { ok: true, board: state.board });
		}
		if (path === '/api/test/status' && req.method === 'POST') {
			const body = await readJson(req);
			state.status = { model: MODEL, state: body.state ?? 'ready', warming_up: body.state === 'starting' };
			// The primary persona a fresh page reads from /api/status (`persona.active`): AURA's
			// screenshots wear her name, not the mock's JARVIS. resetHermes puts it back.
			if (body.persona) state.persona = body.persona;
			if (body.kill !== undefined) {
				state.killSwitch = body.kill
					? { engaged: true, actor: 'owner', reason: String(body.kill_reason ?? 'drill'), at: new Date().toISOString() }
					: { engaged: false, actor: '', reason: '', at: '' };
			}
			return json(res, 200, { ok: true, status: state.status, kill_switch: state.killSwitch });
		}
		if (path === '/api/test/panel' && req.method === 'POST') {
			// Summon, update or dismiss a panel on every live session — a spec drives
			// the surface without needing the page's live id, since the page filters
			// events to its own session. The panel is stored per stored session so
			// `hud list` (the late-join path) replays it after a resume.
			const body = await readJson(req);
			const op = body.op ?? 'show';
			const id = String(body.id ?? '');
			const stored = new Set([...state.live.values()].map((l) => l.stored));
			for (const storedId of stored) {
				const store = panelStore(storedId);
				if (op === 'dismiss') {
					if (id === '*') store.clear();
					else store.delete(id);
					emitPanel(storedId, 'hud.panel.dismiss', { id });
				} else if (op === 'update') {
					const existing = store.get(id);
					const rev = body.rev ?? (existing?.rev ?? 0) + 1;
					const panel = {
						...(existing ?? { id, kind: body.kind ?? 'text', title: '', payload: {}, origin: 'agent', created_at: new Date().toISOString(), ttl_s: null }),
						rev
					};
					if (body.title !== undefined) panel.title = body.title;
					if (body.payload !== undefined) panel.payload = body.payload;
					if (body.ttl_s !== undefined) panel.ttl_s = body.ttl_s;
					store.set(id, panel);
					const payload = { id, rev };
					if (body.title !== undefined) payload.title = body.title;
					if (body.payload !== undefined) payload.payload = body.payload;
					emitPanel(storedId, 'hud.panel.update', payload);
				} else {
					const panel = {
						id,
						kind: body.kind ?? 'text',
						title: body.title ?? '',
						payload: body.payload ?? {},
						origin: body.origin ?? 'agent',
						created_at: body.created_at ?? new Date().toISOString(),
						rev: body.rev ?? 1,
						ttl_s: body.ttl_s ?? null
					};
					store.set(id, panel);
					emitPanel(storedId, 'hud.panel.show', panel);
				}
			}
			return json(res, 200, { ok: true, op, id });
		}
		if (path === '/api/test/projection' && req.method === 'POST') {
			// To every client (session ''), so the gallery's bare socket hears it
			// without a session of its own.
			const body = await readJson(req);
			emit('', 'hud.projection', { id: body.id, type: body.type, data: body.data, origin: 'agent' });
			return json(res, 200, { ok: true, id: body.id });
		}
		if (path === '/api/test/plugin' && req.method === 'POST') {
			const body = await readJson(req);
			state.pluginInstalled = body.installed !== false;
			return json(res, 200, { ok: true, installed: state.pluginInstalled });
		}
		if (path === '/api/test/spoken') return json(res, 200, { spoken: state.spoken, voices: state.voices, transcripts: state.transcripts, streamed: state.streamed });
		if (path === '/api/test/speech' && req.method === 'POST') {
			const body = await readJson(req);
			state.speechSeconds = Number(body.seconds) || 0;
			if (body.stream !== undefined) state.noStream = !body.stream;
			return json(res, 200, { ok: true, seconds: state.speechSeconds, stream: !state.noStream });
		}
		json(res, 404, { detail: `no route ${path}` });
	});

	const wss = new WebSocketServer({ noServer: true });
	server.on('upgrade', (req, socket, head) => {
		const url = new URL(req.url ?? '/', 'http://mock');
		if (url.pathname !== '/ws') {
			socket.destroy();
			return;
		}
		wss.handleUpgrade(req, socket, head, (ws) => {
			const auth = req.headers.authorization ?? '';
			const key = auth.startsWith('Bearer ') ? auth.slice(7) : url.searchParams.get('key') ?? '';
			if (key !== MOCK_KEY) {
				ws.close(4401, 'bad key');
				return;
			}
			clients.add(ws);
			log('ws open');
			send(ws, { jsonrpc: '2.0', method: 'event', params: { type: 'gateway.ready', payload: { change_events: true, heartbeat: true } } });
			ws.on('message', (data, isBinary) => {
				if (isBinary) return;
				let frame;
				try {
					frame = JSON.parse(data.toString());
				} catch {
					return;
				}
				void handle(ws, frame);
			});
			ws.on('close', () => clients.delete(ws));
		});
	});

	await new Promise((resolve) => server.listen(port, '127.0.0.1', resolve));
	const address = server.address();
	const urlOf = `http://127.0.0.1:${address.port}`;
	log(`mock hermes adapter at ${urlOf}`);

	return {
		url: urlOf,
		key: MOCK_KEY,
		state,
		close: () =>
			new Promise((resolve) => {
				for (const ws of clients) ws.terminate();
				wss.close();
				server.close(() => resolve());
			})
	};
}

if (import.meta.url === `file://${process.argv[1]}`) {
	const port = Number(process.argv[2] ?? 8643);
	startMockHermes({ port, log: console.log });
}
