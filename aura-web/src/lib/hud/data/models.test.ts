// The Models panel's mapper, fed the examples in tests/contracts/models-api.json:
// the same file the manager's suite checks its endpoints against.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
	ADAPTER_AWAY,
	ENGINES,
	GGUF_FILE,
	addBody,
	modelsLook,
	newAddDraft,
	unloadLine,
	DEFAULT_SETTINGS,
	LIMITS,
	bytesLabel,
	confirmText,
	draftOf,
	gpusView,
	idOfRepo,
	landsOn,
	modelsView,
	pollMs,
	searchView,
	settingsPatch,
	type AddDraft,
	type ApiGpu,
	type ApiList,
	type ApiSearchResult
} from './models';

const contract = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../tests/contracts/models-api.json', import.meta.url)), 'utf8'));
const list: ApiList = contract.endpoints.list.example;
const gpus: ApiGpu[] = contract.endpoints.gpus.example.gpus;
const hit: ApiSearchResult = contract.endpoints.search.example.results[0];

describe('models limits', () => {
	it('are the contract\'s', () => {
		expect(JSON.parse(JSON.stringify(LIMITS))).toEqual(contract.limits);
	});
	it('a new model starts from the contract\'s defaults, as the manager does', () => {
		const { _comment, ...defaults } = contract.new_model_defaults;
		expect({ ...DEFAULT_SETTINGS }).toEqual(defaults);
	});
});

describe('modelsView', () => {
	const view = modelsView({ list, gpus });
	it('reads each model with its state word and size', () => {
		expect(view.cards.map((c) => [c.id, c.word, c.size])).toEqual([
			['qwen3.8-27b', 'Loaded', '20.0 GB'],
			['qwen3.6-35b', 'Not loaded', '19.0 GB']
		]);
	});
	it('keeps a download no model owns as its own row, with progress', () => {
		expect(view.downloads).toHaveLength(1);
		expect(view.downloads[0]).toMatchObject({ repo: 'Qwen/Qwen3-8B-AWQ', kind: 'running', pct: 33, label: '2.0 GB of 6.0 GB' });
	});
	it('reports the disk and polls fast while a download runs', () => {
		expect(view.disk.free).toBe('197.0 GB');
		expect(view.busy).toBe(true);
		expect(pollMs(view)).toBe(2000);
		expect(pollMs(modelsView({ list: { ...list, downloads: [] }, gpus }))).toBe(15000);
	});
	it('polls fast while a model starts', () => {
		const starting = { ...list, downloads: [], models: [{ ...list.models[0], state: 'starting' as const }] };
		expect(modelsView({ list: starting, gpus }).busy).toBe(true);
	});
	it('offers Add for a finished download no model uses', () => {
		const done = { ...list, downloads: [{ ...list.downloads[0], state: 'done', done_bytes: 6442450944 }] };
		expect(modelsView({ list: done, gpus }).downloads[0]).toMatchObject({ kind: 'done', addable: true });
	});
	it('shows a failed download with its reason', () => {
		const failed = { ...list, downloads: [{ ...list.downloads[0], state: 'error', error: 'Disk full.' }] };
		expect(modelsView({ list: failed, gpus }).downloads[0]).toMatchObject({ kind: 'failed', error: 'Disk full.' });
	});
});

describe('a row whose weights are gone', () => {
	const gone = (engine: 'llama.cpp' | 'vllm', repo = 'made-up/Pebble-7B-GGUF') => ({
		...list.models[0],
		id: 'pebble',
		repo,
		state: 'missing' as const,
		on_disk_bytes: 0,
		settings: { ...list.models[0].settings, engine }
	});
	const card = (m: ReturnType<typeof gone>, downloads = list.downloads) => modelsView({ list: { ...list, models: [m], downloads }, gpus }).cards[0];
	it('a llama.cpp row offers Download again, which needs only the repo the list carries', () => {
		expect(card(gone('llama.cpp'), [])).toMatchObject({ refetch: true, fetching: false });
	});
	it('a vLLM row does not: its own "Download weights" fetches the whole repo', () => {
		expect(card(gone('vllm'), [])).toMatchObject({ refetch: false });
	});
	it('a row with no repo cannot be fetched again', () => {
		expect(card(gone('llama.cpp', ''), [])).toMatchObject({ refetch: false });
	});
	it('a row that is loaded or not loaded does not offer it', () => {
		expect(modelsView({ list, gpus }).cards.map((c) => c.refetch)).toEqual([false, false]);
	});
	it('is held back while that repo has a download running, and free again once it is done or failed', () => {
		const running = { repo: 'made-up/Pebble-7B-GGUF', state: 'running', done_bytes: 1, total_bytes: 9, error: null };
		expect(card(gone('llama.cpp'), [running]).fetching).toBe(true);
		expect(card(gone('llama.cpp'), [{ ...running, state: 'done' }]).fetching).toBe(false);
		expect(card(gone('llama.cpp'), [{ ...running, state: 'error', error: 'x' }]).fetching).toBe(false);
		expect(card(gone('llama.cpp'), [{ ...running, repo: 'made-up/Other-GGUF' }]).fetching).toBe(false);
	});
});

describe('the contract\'s refusals', () => {
	const { _comment, ...table } = contract.refusals;
	it('are keyed, each with a status and a sentence, so the screen never reads the sentence to decide', () => {
		expect(Object.keys(table).length).toBeGreaterThan(0);
		for (const [key, r] of Object.entries(table) as [string, { status: number; message: string }][]) {
			expect(Number.isInteger(r.status), key).toBe(true);
			expect(r.status, key).toBeGreaterThanOrEqual(400);
			expect(r.message.length, key).toBeGreaterThan(0);
		}
	});
});

describe('gpusView', () => {
	const cards = gpusView(gpus);
	it('draws one budget per card, by user', () => {
		expect(cards).toHaveLength(2);
		expect(cards[0].segments.map((s) => s.label)).toEqual(['Models', 'Voice']);
		expect(cards[1].segments.map((s) => s.label)).toEqual(['Models', 'Hearing']);
		expect(cards[0].usedPct).toBe(88);
	});
	it('marks a card over 90% as tight', () => {
		expect(cards[0].tight).toBe(false);
		expect(cards[1].tight).toBe(true);
	});
});

describe('settingsPatch', () => {
	const before = list.models[0].settings;
	it('sends only what changed', () => {
		const draft = { ...draftOf(before), max_model_len: '128000', keep_loaded: 'false' };
		expect(settingsPatch(before, draft)).toEqual({ patch: { max_model_len: 128000, keep_loaded: false }, errors: {} });
	});
	it('refuses values outside the contract\'s limits and sends nothing for them', () => {
		const draft = { ...draftOf(before), max_model_len: '10', gpu_memory_utilization: '0.99', tensor_parallel_size: '3', idle_unload_s: '-1', kv_cache_dtype: 'fp4' };
		const { patch, errors } = settingsPatch(before, draft);
		expect(Object.keys(errors).sort()).toEqual(['gpu_memory_utilization', 'idle_unload_s', 'kv_cache_dtype', 'max_model_len', 'tensor_parallel_size']);
		expect(patch).toEqual({});
	});
	it('takes the limits themselves', () => {
		const draft = { ...draftOf(before), max_model_len: '1048576', gpu_memory_utilization: '0.1', quantization: 'awq' };
		expect(settingsPatch(before, draft).errors).toEqual({});
		expect(settingsPatch(before, draft).patch.quantization).toBe('awq');
	});
});

describe('searchView', () => {
	const free = list.disk.free_bytes;
	it('lets a fitting repo download', () => {
		const v = searchView(hit, free);
		expect(v.blocked).toBe('');
		expect(confirmText(v, bytesLabel(free))).toContain('6.0 GB and 197.0 GB is free');
	});
	it('says a gated repo needs its licence and offers no download', () => {
		expect(searchView({ ...hit, gated: true }, free).blocked).toMatch(/licence/);
	});
	it('says how much room is left when the repo is too big', () => {
		const v = searchView({ ...hit, size_bytes: free }, free);
		expect(v.blocked).toMatch(/Needs 197\.0 GB; only 187\.0 GB is free/);
	});
	it('never blocks what is already on disk', () => {
		expect(searchView({ ...hit, local: true, size_bytes: free * 2 }, free).blocked).toBe('');
	});
});

describe('idOfRepo', () => {
	it('is the repo name in the id alphabet', () => {
		expect(idOfRepo('Qwen/Qwen3-8B-AWQ')).toBe('qwen3-8b-awq');
	});
});

describe('landsOn', () => {
	it('names the cards the contract\'s load example lands on, and nothing for the CPU', () => {
		expect(landsOn(contract.endpoints.load.example.gpus)).toBe(' on cards 0 and 1');
		expect(landsOn([{ index: 1, name: 'x', need_mib: null, free_mib: 1 }])).toBe(' on card 1');
		expect(landsOn([])).toBe('');
		expect(landsOn(undefined)).toBe('');
	});
});

describe('addBody (the add form)', () => {
	const rule = contract.create_body;
	const llama = rule.llama_example;
	const draft = (over: Partial<AddDraft> = {}): AddDraft => ({
		id: llama.id,
		repo: llama.repo,
		engine: 'llama.cpp',
		file: llama.file,
		max_model_len: String(llama.settings.max_model_len),
		gpu_memory_utilization: '',
		tensor_parallel_size: '',
		keep_loaded: String(llama.settings.keep_loaded),
		idle_unload_s: String(llama.settings.idle_unload_s),
		...over
	});
	it('holds the contract\'s one-.gguf rule and sentences', () => {
		const g = rule.gguf_file;
		expect(GGUF_FILE).toEqual({ pattern: g.pattern, shardPattern: g.shard_pattern, error: g.error, shardError: g.shard_error, missingError: g.missing_error });
	});
	it('names the engines and the body fields the contract does', () => {
		expect([...ENGINES]).toEqual(rule.engines);
		expect(Object.keys(addBody(draft()).body ?? {}).sort()).toEqual([...rule.fields].sort());
	});
	it('sends engine and the file for llama.cpp, with the settings llama-server has', () => {
		expect(addBody(draft())).toEqual({ body: llama, error: '' });
	});
	it('sends no file for vLLM, even if one was typed before the engine changed', () => {
		const { body, error } = addBody(draft({ engine: 'vllm', gpu_memory_utilization: '0.4', tensor_parallel_size: '1' }));
		expect(error).toBe('');
		expect(body).toMatchObject({ engine: 'vllm', settings: { gpu_memory_utilization: 0.4, tensor_parallel_size: 1 } });
		expect(body).not.toHaveProperty('file');
		expect(body?.settings).not.toHaveProperty('kv_cache_dtype');
	});
	it('refuses llama.cpp with no file, with the contract\'s sentence, and sends nothing', () => {
		for (const file of ['', '   ']) expect(addBody(draft({ file }))).toEqual({ body: null, error: rule.gguf_file.missing_error });
	});
	it('applies the contract\'s one-.gguf rule', () => {
		for (const file of rule.gguf_file.ok) expect(addBody(draft({ file })).error).toBe('');
		for (const file of rule.gguf_file.bad.filter((f: string) => f.trim() !== '')) {
			const out = addBody(draft({ file }));
			expect(out.body, file).toBeNull();
			expect(out.error, file).toBe(/-\d{5}-of-\d{5}\.gguf$/.test(file) ? rule.gguf_file.shard_error : rule.gguf_file.error);
		}
	});
	it('refuses an id or repo the manager would, and a setting out of range', () => {
		expect(addBody(draft({ id: 'Bad Id' })).error).toMatch(/lowercase/);
		expect(addBody(draft({ repo: 'nope' })).error).toMatch(/owner\/name/);
		expect(addBody(draft({ max_model_len: '10' })).error).toMatch(/1024 to 1048576/);
	});
});

describe('the Models screen', () => {
	const none: ApiList = { models: [], downloads: [], disk: list.disk };
	it('is loading until the first answer, ready with a model or a download, empty with neither', () => {
		expect(modelsLook(null, '')).toBe('loading');
		expect(modelsLook({ list, gpus }, '')).toBe('ready');
		expect(modelsLook({ list: { ...none, downloads: list.downloads }, gpus }, '')).toBe('ready');
		expect(modelsLook({ list: none, gpus: [] }, '')).toBe('empty');
	});
	it('is offline when the adapter is out of reach and an error for any other refusal, over a last list or none', () => {
		for (const last of [null, { list, gpus }, { list: none, gpus: [] }]) {
			expect(modelsLook(last, ADAPTER_AWAY)).toBe('offline');
			expect(modelsLook(last, 'The model manager answered 500.')).toBe('error');
		}
	});
	it('starts the add form on vLLM with the contract\'s defaults, which addBody takes for either engine', () => {
		const start = newAddDraft();
		expect(start.engine).toBe(contract.create_body.engines[0]);
		const typed = { ...start, id: 'pebble-7b', repo: 'made-up/Pebble-7B-AWQ' };
		const { _comment, ...defaults } = contract.new_model_defaults;
		expect(addBody(typed).body).toEqual({ id: 'pebble-7b', repo: 'made-up/Pebble-7B-AWQ', engine: 'vllm', settings: defaults });
		const llama = addBody({ ...typed, engine: 'llama.cpp', file: 'pebble-7b-q4.gguf' }).body;
		expect(Object.keys(llama?.settings ?? {}).sort()).toEqual([...contract.create_body.llama_settings].sort());
		expect(addBody({ ...typed, engine: 'llama.cpp' }).error).toBe(contract.create_body.gguf_file.missing_error);
	});
	it('says what an unload stops before it is asked for', () => {
		expect(unloadLine('pebble-7b')).toBe('Unloading stops replies from pebble-7b until it loads again; the next request to it loads it.');
	});
});

describe('a llama.cpp model\'s settings', () => {
	// T-0518: its null cache type failed validation, so its context length could not be saved.
	it('validate and send only the fields llama-server has', () => {
		const before = { engine: 'llama.cpp', max_model_len: 65536, gpu_memory_utilization: null, tensor_parallel_size: null, kv_cache_dtype: null, quantization: null, keep_loaded: false, idle_unload_s: 600 } as const;
		const draft = { ...draftOf(before), max_model_len: '98304' };
		expect(draft.gpu_memory_utilization).toBe('');
		expect(settingsPatch(before, draft)).toEqual({ patch: { max_model_len: 98304 }, errors: {} });
	});
});
