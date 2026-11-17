// `panel.models` from the model manager's two lists (tests/contracts/models-api.json).
//
// The manager answers in bytes and MiB; a person reads gigabytes and a bar. This
// file is that translation and the client-side half of the limits: the manager
// refuses a value outside them, but a box that says so before the round trip is
// the difference between a typo and a failed save. The limits are typed here and
// held to the contract's by models.test.ts, so a widened range on one side is a
// failing test on the other.

import type { Status } from '../../ui/status';

export type ModelState = 'loaded' | 'starting' | 'unloaded' | 'downloading' | 'missing';

export interface ModelSettings {
	/** Which server runs it; llama.cpp has none of vLLM's card share, card count, cache type or quantisation (null). */
	engine?: 'vllm' | 'llama.cpp';
	max_model_len: number;
	gpu_memory_utilization: number | null;
	tensor_parallel_size: number | null;
	kv_cache_dtype: string | null;
	quantization: string | null;
	keep_loaded: boolean;
	idle_unload_s: number;
}

export interface ApiModel {
	id: string;
	name: string;
	repo: string;
	state: ModelState;
	on_disk_bytes: number;
	settings: ModelSettings;
}

export interface ApiDownload {
	repo: string;
	state: string;
	done_bytes: number;
	total_bytes: number | null;
	error: string | null;
}

export interface ApiList {
	models: ApiModel[];
	downloads: ApiDownload[];
	disk: { free_bytes: number; total_bytes: number };
}

export interface ApiGpu {
	index: number;
	name: string;
	total_mib: number;
	used_mib: number;
	users: { who: string; mib: number }[];
}

export interface ApiSearchResult {
	repo: string;
	downloads: number;
	likes: number;
	params_b: number | null;
	size_bytes: number | null;
	gated: boolean;
	quantization: string | null;
	local: boolean;
}

/** What the feed holds: both lists from one poll. */
export interface ModelsData {
	list: ApiList;
	gpus: ApiGpu[];
}

export const EMPTY_MODELS: ModelsData = { list: { models: [], downloads: [], disk: { free_bytes: 0, total_bytes: 0 } }, gpus: [] };

export const LIMITS = {
	max_model_len: [1024, 1048576],
	gpu_memory_utilization: [0.1, 0.95],
	tensor_parallel_size: [1, 2],
	idle_unload_s: [0, 86400],
	kv_cache_dtype: ['auto', 'fp8_e5m2', 'fp8_e4m3'],
	quantization: [null, 'awq', 'awq_marlin', 'gptq', 'gptq_marlin', 'fp8'],
	disk_reserve_bytes: 10737418240
} as const;

/** What a model added from a download starts with: small, and off the cards until asked for. */
export const DEFAULT_SETTINGS = { max_model_len: 32768, gpu_memory_utilization: 0.4, tensor_parallel_size: 1, keep_loaded: false, idle_unload_s: 600 } as const;

const GIB = 1024 ** 3;
const MIB = 1024 ** 2;

export function bytesLabel(bytes: number | null | undefined): string {
	if (bytes == null || !Number.isFinite(bytes)) return 'size unknown';
	return bytes >= GIB ? `${(bytes / GIB).toFixed(1)} GB` : `${Math.round(bytes / MIB)} MB`;
}

/** The words a card shows for each state: ModelBody's `starting`, and the manager's others. */
export const STATE_WORDS: Record<ModelState, string> = {
	loaded: 'Loaded',
	starting: 'Starting',
	unloaded: 'Not loaded',
	downloading: 'Downloading',
	missing: 'Weights missing'
};

export interface DownloadView {
	repo: string;
	kind: 'running' | 'done' | 'failed';
	/** 0-100, or null when the total is unknown. */
	pct: number | null;
	label: string;
	error: string;
	/** A finished download that no model uses yet, so the row offers "Add". */
	addable: boolean;
}

export interface ModelCard {
	id: string;
	name: string;
	repo: string;
	state: ModelState;
	word: string;
	size: string;
	settings: ModelSettings;
	/** Progress when the manager says it is downloading. */
	download: DownloadView | null;
	/** A llama.cpp row whose .gguf is gone: "Download again" posts only its repo, the manager finds the file. */
	refetch: boolean;
	/** That repo has a download running, so a second one is not offered. */
	fetching: boolean;
}

export interface ModelsView {
	cards: ModelCard[];
	downloads: DownloadView[];
	disk: { free: string; total: string; freeBytes: number; usedPct: number };
	/** Something is changing under the panel: poll fast. */
	busy: boolean;
}

function downloadView(d: ApiDownload, configured: ReadonlySet<string>): DownloadView {
	const kind: DownloadView['kind'] = d.error || d.state === 'error' ? 'failed' : d.state === 'running' ? 'running' : 'done';
	const pct = d.total_bytes ? Math.min(100, Math.round((d.done_bytes / d.total_bytes) * 100)) : null;
	return {
		repo: d.repo,
		kind,
		pct,
		label: kind === 'running' ? `${bytesLabel(d.done_bytes)} of ${bytesLabel(d.total_bytes)}` : kind === 'done' ? 'Downloaded' : 'Failed',
		error: d.error ?? '',
		addable: kind === 'done' && !configured.has(d.repo)
	};
}

export function modelsView(data: ModelsData): ModelsView {
	const { models, downloads, disk } = data.list;
	const configured = new Set(models.map((m) => m.repo));
	const views = downloads.map((d) => downloadView(d, configured));
	const byRepo = new Map(views.map((v) => [v.repo, v]));
	const cards = models.map((m) => ({
		id: m.id,
		name: m.name || m.id,
		repo: m.repo,
		state: m.state,
		word: STATE_WORDS[m.state] ?? m.state,
		size: bytesLabel(m.on_disk_bytes),
		settings: m.settings,
		download: m.state === 'downloading' ? (byRepo.get(m.repo) ?? null) : null,
		refetch: m.state === 'missing' && m.settings.engine === 'llama.cpp' && !!m.repo,
		fetching: views.some((v) => v.repo === m.repo && v.kind === 'running')
	}));
	const shown = views.filter((v) => !(v.kind === 'done' && !v.addable) && !cards.some((c) => c.download === v));
	return {
		cards,
		downloads: shown,
		disk: {
			free: bytesLabel(disk.free_bytes),
			total: bytesLabel(disk.total_bytes),
			freeBytes: disk.free_bytes,
			usedPct: disk.total_bytes ? Math.round((1 - disk.free_bytes / disk.total_bytes) * 100) : 0
		},
		busy: cards.some((c) => c.state === 'starting' || c.state === 'downloading') || views.some((v) => v.kind === 'running')
	};
}

/** 2 s while anything is starting or downloading, 15 s otherwise. */
export const pollMs = (view: Pick<ModelsView, 'busy'>): number => (view.busy ? 2000 : 15000);

export const WHO_WORDS: Record<string, string> = { models: 'Models', voice: 'Voice', hearing: 'Hearing', other: 'Other' };

export interface GpuView {
	index: number;
	name: string;
	total: string;
	used: string;
	free: string;
	usedPct: number;
	/** Over 90% of the card: the next load may not fit. */
	tight: boolean;
	segments: { who: string; label: string; mib: number; pct: number }[];
}

/** A card a load lands on (`POST /{id}/load`'s `gpus`); need_mib is null for llama.cpp. */
export interface ApiLanding {
	index: number;
	name: string;
	need_mib: number | null;
	free_mib: number;
}

/** " on card 0" / " on cards 0 and 1", for the load's notice; empty on the CPU or unmeasured. */
export function landsOn(gpus: readonly ApiLanding[] | undefined): string {
	const cards = (gpus ?? []).map((g) => g.index);
	if (!cards.length) return '';
	if (cards.length === 1) return ` on card ${cards[0]}`;
	return ` on cards ${cards.slice(0, -1).join(', ')} and ${cards[cards.length - 1]}`;
}

export function gpusView(gpus: readonly ApiGpu[]): GpuView[] {
	const gib = (mib: number) => `${(mib / 1024).toFixed(1)} GB`;
	return gpus.map((g) => ({
		index: g.index,
		name: g.name,
		total: gib(g.total_mib),
		used: gib(g.used_mib),
		free: gib(Math.max(0, g.total_mib - g.used_mib)),
		usedPct: g.total_mib ? Math.round((g.used_mib / g.total_mib) * 100) : 0,
		tight: g.total_mib > 0 && g.used_mib / g.total_mib > 0.9,
		segments: g.users.map((u) => ({ who: u.who, label: WHO_WORDS[u.who] ?? u.who, mib: u.mib, pct: g.total_mib ? (u.mib / g.total_mib) * 100 : 0 }))
	}));
}

/** The editable fields as strings, the way the form holds them. */
export type SettingsDraft = Record<keyof ModelSettings, string>;

export const draftOf = (s: ModelSettings): SettingsDraft => ({
	engine: s.engine ?? 'vllm',
	max_model_len: String(s.max_model_len),
	gpu_memory_utilization: String(s.gpu_memory_utilization ?? ''),
	tensor_parallel_size: String(s.tensor_parallel_size ?? ''),
	kv_cache_dtype: s.kv_cache_dtype ?? '',
	quantization: s.quantization ?? '',
	keep_loaded: String(s.keep_loaded),
	idle_unload_s: String(s.idle_unload_s)
});

const INTS = ['max_model_len', 'tensor_parallel_size', 'idle_unload_s'] as const;

/** Only vLLM has these; a llama.cpp model's are null, never validated and never sent (T-0518). */
export const VLLM_ONLY = ['gpu_memory_utilization', 'tensor_parallel_size', 'kv_cache_dtype', 'quantization'] as const;

/**
 * The draft against the limits: what to send (only what changed) and a sentence
 * per field that is out of range. Nothing is sent while any sentence exists.
 */
export function settingsPatch(before: ModelSettings, draft: SettingsDraft): { patch: Partial<ModelSettings>; errors: Partial<Record<keyof ModelSettings, string>> } {
	const patch: Record<string, unknown> = {};
	const errors: Partial<Record<keyof ModelSettings, string>> = {};
	const llama = before.engine === 'llama.cpp';
	for (const key of INTS) {
		if (llama && (VLLM_ONLY as readonly string[]).includes(key)) continue;
		const [lo, hi] = LIMITS[key];
		const n = Number(draft[key]);
		if (draft[key].trim() === '' || !Number.isInteger(n) || n < lo || n > hi) errors[key] = `A whole number from ${lo} to ${hi}.`;
		else if (n !== before[key]) patch[key] = n;
	}
	const keep = draft.keep_loaded === 'true';
	if (keep !== before.keep_loaded) patch.keep_loaded = keep;
	if (llama) return { patch, errors };
	const [glo, ghi] = LIMITS.gpu_memory_utilization;
	const g = Number(draft.gpu_memory_utilization);
	if (draft.gpu_memory_utilization.trim() === '' || !Number.isFinite(g) || g < glo || g > ghi) errors.gpu_memory_utilization = `A share of the card from ${glo} to ${ghi}.`;
	else if (g !== before.gpu_memory_utilization) patch.gpu_memory_utilization = g;
	if (!(LIMITS.kv_cache_dtype as readonly string[]).includes(draft.kv_cache_dtype)) errors.kv_cache_dtype = 'Not one of the cache types.';
	else if (draft.kv_cache_dtype !== before.kv_cache_dtype) patch.kv_cache_dtype = draft.kv_cache_dtype;
	const q = draft.quantization === '' ? null : draft.quantization;
	if (!(LIMITS.quantization as readonly (string | null)[]).includes(q)) errors.quantization = 'Not one of the quantisations.';
	else if (q !== before.quantization) patch.quantization = q;
	return { patch, errors };
}

export const ENGINES = ['vllm', 'llama.cpp'] as const;
export type Engine = (typeof ENGINES)[number];

/** The one-.gguf rule and its sentences: tests/contracts/models-api.json `create_body.gguf_file`, held equal by models.test.ts. */
export const GGUF_FILE = {
	pattern: '[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*\\.gguf',
	shardPattern: '-\\d{5}-of-\\d{5}\\.gguf',
	error: 'file is one .gguf in the repo.',
	shardError: 'That file is one shard of a split GGUF; only a single .gguf file is supported.',
	missingError: 'Pick the .gguf file to serve: llama.cpp reads one file of the repo.'
} as const;

/** The first sentence the file earns, or '' when it is one .gguf. Does not know whether the repo has it. */
export function ggufError(file: string): string {
	if (file.trim() === '') return GGUF_FILE.missingError;
	if (!new RegExp(`^${GGUF_FILE.pattern}$`).test(file)) return GGUF_FILE.error;
	return new RegExp(`${GGUF_FILE.shardPattern}$`).test(file) ? GGUF_FILE.shardError : '';
}

const ID_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const REPO_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._-]*$/;

/** The add form's fields, as strings; `file` only counts for llama.cpp, the card fields only for vLLM. */
export type AddDraft = Record<'id' | 'repo' | 'engine' | 'file' | 'max_model_len' | 'gpu_memory_utilization' | 'tensor_parallel_size' | 'keep_loaded' | 'idle_unload_s', string>;

export interface AddBody {
	id: string;
	repo: string;
	engine: Engine;
	file?: string;
	settings: Record<string, number | boolean>;
}

/**
 * The create body (POST /api/models) for the add form, or the one sentence that says what to fix
 * (nothing is sent while there is one). llama.cpp needs its .gguf and sends only the settings
 * llama-server has; vLLM sends no file. The manager checks all of it again.
 */
export function addBody(d: AddDraft): { body: AddBody | null; error: string } {
	const fail = (error: string) => ({ body: null, error });
	if (!ID_RE.test(d.id)) return fail('The id is lowercase letters, digits, dot, dash or underscore.');
	if (!REPO_RE.test(d.repo)) return fail('That is not a Hugging Face repo (owner/name).');
	if (!(ENGINES as readonly string[]).includes(d.engine)) return fail(`engine is one of ${ENGINES.join(', ')}.`);
	const engine = d.engine as Engine;
	const llama = engine === 'llama.cpp';
	const file = d.file.trim();
	if (llama) {
		const why = ggufError(file);
		if (why) return fail(why);
	}
	const settings: Record<string, number | boolean> = {};
	const ints = llama ? (['max_model_len', 'idle_unload_s'] as const) : (['max_model_len', 'tensor_parallel_size', 'idle_unload_s'] as const);
	for (const key of ints) {
		const [lo, hi] = LIMITS[key];
		const n = Number(d[key]);
		if (d[key].trim() === '' || !Number.isInteger(n) || n < lo || n > hi) return fail(`A whole number from ${lo} to ${hi}.`);
		settings[key] = n;
	}
	if (!llama) {
		const [lo, hi] = LIMITS.gpu_memory_utilization;
		const g = Number(d.gpu_memory_utilization);
		if (d.gpu_memory_utilization.trim() === '' || !Number.isFinite(g) || g < lo || g > hi) return fail(`A share of the card from ${lo} to ${hi}.`);
		settings.gpu_memory_utilization = g;
	}
	settings.keep_loaded = d.keep_loaded === 'true';
	return { body: { id: d.id, repo: d.repo, engine, ...(llama ? { file } : {}), settings }, error: '' };
}

export interface SearchView {
	repo: string;
	meta: string;
	size: string;
	/** Why the button is not there or not enabled; empty when a download may start. */
	blocked: string;
	/** Already on disk, so the row offers "Add" rather than a second download. */
	local: boolean;
	gated: boolean;
	sizeBytes: number | null;
}

/** A search row with the verdict on whether it may be downloaded: gated, or too big for the disk. */
export function searchView(r: ApiSearchResult, freeBytes: number): SearchView {
	const parts = [r.params_b ? `${r.params_b}B` : '', r.quantization ?? '', `${r.downloads.toLocaleString('en-US')} downloads`].filter(Boolean);
	const room = freeBytes - LIMITS.disk_reserve_bytes;
	let blocked = '';
	if (r.gated) blocked = 'Gated: accept its licence on huggingface.co first, then search again.';
	else if (!r.local && r.size_bytes != null && r.size_bytes > room) blocked = `Needs ${bytesLabel(r.size_bytes)}; only ${bytesLabel(Math.max(0, room))} is free after keeping ${bytesLabel(LIMITS.disk_reserve_bytes)} for the system.`;
	return { repo: r.repo, meta: parts.join(' · '), size: bytesLabel(r.size_bytes), blocked, local: r.local, gated: r.gated, sizeBytes: r.size_bytes };
}

/** The confirmation a download asks for. */
export const confirmText = (s: SearchView, freeLabel: string): string =>
	`Download ${s.repo}? It is ${s.size} and ${freeLabel} is free. Downloads are the only traffic that leaves the house.`;

/** The loader's sentence when the relay or the adapter is out of reach: the Models screen's offline. */
export const ADAPTER_AWAY = 'The adapter is not reachable.';

/**
 * Which of the five states the Models screen is in, from its last list and its last error.
 * A download with no model yet is something to show, so it is not empty. Does not tell a
 * manager that is down from one that refused: both are an error with the manager's sentence.
 */
export function modelsLook(data: ModelsData | null, error: string): Status {
	if (error === ADAPTER_AWAY) return 'offline';
	if (error) return 'error';
	if (!data) return 'loading';
	return data.list.models.length || data.list.downloads.length ? 'ready' : 'empty';
}

/** The add form before anyone types: vLLM, with the settings a new model starts from. */
export const newAddDraft = (): AddDraft => ({
	id: '',
	repo: '',
	engine: ENGINES[0],
	file: '',
	max_model_len: String(DEFAULT_SETTINGS.max_model_len),
	gpu_memory_utilization: String(DEFAULT_SETTINGS.gpu_memory_utilization),
	tensor_parallel_size: String(DEFAULT_SETTINGS.tensor_parallel_size),
	keep_loaded: String(DEFAULT_SETTINGS.keep_loaded),
	idle_unload_s: String(DEFAULT_SETTINGS.idle_unload_s)
});

/** What an unload costs, said beside its button: nothing is lost, but that model is silent until it is back on a card. */
export const unloadLine = (name: string): string => `Unloading stops replies from ${name} until it loads again; the next request to it loads it.`;

/** An id for a model added from a repo: its name, lowercase, in the manager's id alphabet. */
export const idOfRepo = (repo: string): string => (repo.split('/').pop() ?? repo).toLowerCase().replace(/[^a-z0-9.]+/g, '-').replace(/^-+|-+$/g, '');
