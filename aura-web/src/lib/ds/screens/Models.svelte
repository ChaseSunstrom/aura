<!--
@component
The Models screen at `/models`: the models the house serves, each with its state,
and what a person does with them: load one (and read which cards it lands on),
unload it, change its settings, delete it behind a hold, find a repo on Hugging
Face, download it, and add it with the engine that runs it (vLLM, or llama.cpp
with its one .gguf). The GPUs' budget sits beside the list, so "it does not fit"
is something to see before the load says so.

Every body it sends is built by the mappers in `hud/data/models.ts` (addBody,
settingsPatch), which say what to fix before the round trip; every refusal after
it is the manager's own sentence. It polls fast while something downloads or
starts, slowly otherwise, and not at all in a hidden tab.

Does not cover: a model's cache type or quantisation (ask AURA, or edit its
file); cancelling a download (the manager has no route for it); whether a repo
holds the .gguf that was typed (the download says); or the aux models
(embeddings, rerank), which the manager does not list.
-->
<script lang="ts">
	import {
		ENGINES,
		LIMITS,
		addBody,
		bytesLabel,
		confirmText,
		draftOf,
		ggufError,
		gpusView,
		idOfRepo,
		landsOn,
		modelsLook,
		modelsView,
		newAddDraft,
		pollMs,
		searchView,
		settingsPatch,
		unloadLine,
		type AddDraft,
		type ApiSearchResult,
		type DownloadView,
		type Engine,
		type ModelCard,
		type ModelsData,
		type SettingsDraft
	} from '$lib/hud/data/models';
	import { createModel, deleteModel, downloadModel, loadModel, loadModels, searchModels, unloadModel, updateModel } from '$lib/hud/models.svelte';
	import { Label } from '$lib/ds';
	import HoldButton from '../HoldButton.svelte';
	import Button from '../ui/Button.svelte';
	import Field from '../ui/Field.svelte';
	import Segmented from '../ui/Segmented.svelte';
	import State from '../ui/State.svelte';
	import Switch from '../ui/Switch.svelte';
	import Screen from './Screen.svelte';
	import Section from './Section.svelte';

	type Where = 'list' | 'find' | 'add';
	/** The settings both forms hold: a model's own, and a new model's. */
	type Shared = Pick<AddDraft, 'max_model_len' | 'gpu_memory_utilization' | 'tensor_parallel_size' | 'keep_loaded' | 'idle_unload_s'>;

	const ENGINE_WORDS: Record<Engine, string> = { vllm: 'vLLM', 'llama.cpp': 'llama.cpp' };
	const ENGINE_OPTIONS = ENGINES.map((value) => ({ value, label: ENGINE_WORDS[value] }));
	const CARD_OPTIONS = [
		{ value: '1', label: 'One card' },
		{ value: '2', label: 'Two cards' }
	];
	const FIELD_WORDS: Record<string, string> = {
		max_model_len: 'Context length',
		gpu_memory_utilization: 'Share of each card',
		tensor_parallel_size: 'Cards',
		idle_unload_s: 'Unload after idle',
		kv_cache_dtype: 'Cache type',
		quantization: 'Quantisation'
	};

	let data = $state<ModelsData | null>(null);
	let failed = $state('');
	/** What is being done, so nothing else starts under it; '' when idle. */
	let busy = $state('');
	/** The last thing an action had to say, beside where it was asked for. */
	let said = $state<{ where: Where; text: string }>({ where: 'list', text: '' });

	let open = $state('');
	let edit = $state<SettingsDraft | null>(null);
	let weights = $state(false);

	let query = $state('');
	let results = $state<ApiSearchResult[]>([]);
	/** The repo whose download is waiting for its yes. */
	let asking = $state('');

	let draft = $state<AddDraft>(newAddDraft());
	let addForm = $state<HTMLFormElement>();

	const look = $derived(modelsLook(data, failed));
	const view = $derived(data ? modelsView(data) : null);
	const cards = $derived(data ? gpusView(data.gpus) : []);
	const found = $derived(view ? results.map((r) => searchView(r, view.disk.freeBytes)) : []);
	const off = $derived(Boolean(busy) || look === 'offline');
	const llama = $derived(draft.engine === 'llama.cpp');
	/** Said as it is typed; the empty box waits for the submit to say it is needed. */
	const fileError = $derived(llama && draft.file.trim() ? ggufError(draft.file.trim()) : '');

	const why = (e: unknown): string => (e instanceof Error ? e.message : String(e));

	let timer: ReturnType<typeof setTimeout> | undefined;
	// A read still in flight when the screen closes must not arm the next poll:
	// the timer it set would outlive the screen and poll the manager for good.
	let live = true;

	async function read() {
		clearTimeout(timer);
		try {
			data = await loadModels();
			failed = '';
		} catch (e) {
			failed = why(e);
		}
		clearTimeout(timer);
		if (live) timer = setTimeout(() => void (document.hidden ? undefined : read()), pollMs({ busy: view?.busy ?? false }));
	}

	$effect(() => {
		live = true;
		void read();
		const wake = () => void (document.hidden ? undefined : read());
		document.addEventListener('visibilitychange', wake);
		return () => {
			live = false;
			clearTimeout(timer);
			document.removeEventListener('visibilitychange', wake);
		};
	});

	/** One action at a time: its result, or the manager's refusal, lands beside where it was asked. */
	async function act(where: Where, what: string, doIt: () => Promise<string>) {
		if (busy) return;
		busy = what;
		said = { where, text: '' };
		try {
			said = { where, text: await doIt() };
			await read();
		} catch (e) {
			said = { where, text: `Couldn’t ${what}: ${why(e)}` };
		} finally {
			busy = '';
		}
	}

	function toggle(c: ModelCard) {
		open = open === c.id ? '' : c.id;
		edit = open ? draftOf(c.settings) : null;
		weights = false;
	}

	function save(c: ModelCard) {
		if (!edit) return;
		const { patch, errors } = settingsPatch(c.settings, edit);
		const wrong = Object.entries(errors)[0];
		if (wrong) return void (said = { where: 'list', text: `${FIELD_WORDS[wrong[0]] ?? wrong[0]}: ${wrong[1]}` });
		if (!Object.keys(patch).length) return void (said = { where: 'list', text: 'Nothing changed.' });
		void act('list', `save ${c.name}`, async () => {
			await updateModel(c.id, patch);
			open = '';
			return `Saved ${c.name}. A loaded model takes it on its next load.`;
		});
	}

	function find(e: SubmitEvent) {
		e.preventDefault();
		const q = query.trim();
		if (!q) return;
		void act('find', 'search', async () => {
			results = await searchModels(q);
			asking = '';
			return results.length ? '' : `Nothing on Hugging Face matches “${q}”.`;
		});
	}

	const fetchIt = (where: Where, repo: string, file?: string) =>
		act(where, `download ${file || repo}`, async () => {
			await downloadModel(repo, file);
			asking = '';
			return `Downloading ${file || repo}. Add it when it is done.`;
		});

	/** A repo that is on disk goes to the add form, with the id its name suggests. */
	function prefill(repo: string) {
		draft = { ...draft, repo, id: idOfRepo(repo) };
		said = { where: 'add', text: `${repo} is filled in. Check its settings, then add it.` };
		addForm?.scrollIntoView({ block: 'nearest' });
	}

	function add(e: SubmitEvent) {
		e.preventDefault();
		const { body, error } = addBody(draft);
		if (!body) return void (said = { where: 'add', text: error });
		void act('add', `add ${body.id}`, async () => {
			await createModel(body);
			draft = newAddDraft();
			return `Added ${body.id}. Load it from the list when you want it.`;
		});
	}

	function fetchDraft() {
		const repo = draft.repo.trim();
		const file = llama ? draft.file.trim() : undefined;
		const wrong = !repo ? 'Type the Hugging Face repo to download.' : file !== undefined ? ggufError(file) : '';
		if (wrong) return void (said = { where: 'add', text: wrong });
		void fetchIt('add', repo, file);
	}
</script>

{#snippet note(where: Where)}
	{#if said.where === where && said.text}<p class="said" role="status" data-testid="models-said-{where}">{said.text}</p>{/if}
{/snippet}

{#snippet progress(d: DownloadView)}
	<span class="meta" class:bad={d.kind === 'failed'}>{d.label}{d.pct === null || d.kind !== 'running' ? '' : ` · ${d.pct}%`}{d.error ? `: ${d.error}` : ''}</span>
	{#if d.kind === 'running' && d.pct !== null}<div class="bar" aria-hidden="true"><i style:transform="scaleX({d.pct / 100})"></i></div>{/if}
{/snippet}

{#snippet settings(d: Shared, isLlama: boolean, id: string)}
	<label class="set"><span class="meta">Context length, tokens</span><Field label="Context length" inputmode="numeric" maxlength={7} disabled={off} bind:value={d.max_model_len} testid="{id}-context" /></label>
	{#if !isLlama}
		<label class="set">
			<span class="meta">Share of each card, {LIMITS.gpu_memory_utilization[0]} to {LIMITS.gpu_memory_utilization[1]}</span>
			<Field label="Share of each card" inputmode="decimal" maxlength={4} disabled={off} bind:value={d.gpu_memory_utilization} testid="{id}-share" />
		</label>
		<div class="set"><span class="meta">Spread over</span><Segmented label="Cards" options={CARD_OPTIONS} value={d.tensor_parallel_size} disabled={off} testid="{id}-cards" onchange={(v) => (d.tensor_parallel_size = v)} /></div>
	{/if}
	<label class="set"><span class="meta">Unload after idle, seconds</span><Field label="Unload after idle" inputmode="numeric" maxlength={5} disabled={off} bind:value={d.idle_unload_s} testid="{id}-idle" /></label>
	<Switch label="Keep loaded" on={d.keep_loaded === 'true'} disabled={off} testid="{id}-keep" onchange={(on) => (d.keep_loaded = String(on))} />
{/snippet}

<Screen title="Models" lede="The models this house serves, on its own cards. Only a download leaves the house." testid="screen-models">
	<Section title="Models" count={view?.cards.length} lede={view ? `${view.disk.free} free of ${view.disk.total} for weights.` : ''} testid="models-list">
		{#if look !== 'ready'}
			<State status={look} error={failed} empty="No models yet. Find one below, download it, then add it." onretry={() => void read()} testid="models-state" />
		{/if}
		{#if view?.cards.length}
			<ul class="rows">
				{#each view.cards as c (c.id)}
					{@const up = c.state === 'loaded' || c.state === 'starting'}
					<li class="model" data-testid="model-{c.id}" data-state={c.state}>
						<div class="who">
							<span class="name">{c.name}</span>
							<Label tone={up ? 'violet' : c.state === 'missing' ? 'warn' : 'dim'} testid="model-{c.id}-state">{c.word}</Label>
							<span class="meta">{c.repo} · {c.size} · {ENGINE_WORDS[c.settings.engine ?? 'vllm']}</span>
							{#if c.download}{@render progress(c.download)}{/if}
							{#if up}<span class="meta" data-testid="model-{c.id}-cost">{unloadLine(c.name)}</span>{/if}
							{#if c.refetch}<span class="meta">Its .gguf is not on disk: download it again from here.</span>{/if}
						</div>
						<div class="line">
							{#if up}
								<HoldButton label="Unload {c.name}" tone="warn" disabled={off} testid="model-{c.id}-unload" onconfirm={() => void act('list', `unload ${c.name}`, async () => (await unloadModel(c.id), `Unloaded ${c.name}.`))} />
							{:else if c.state === 'unloaded'}
								<Button tone="primary" disabled={off} testid="model-{c.id}-load" onclick={() => void act('list', `load ${c.name}`, async () => `Loading ${c.name}${landsOn(await loadModel(c.id))}.`)}>Load</Button>
							{:else if c.refetch}
								<!-- The repo alone: the manager reads the lost file from its own entry for this model. -->
								<Button disabled={off || c.fetching} testid="model-{c.id}-refetch" onclick={() => void fetchIt('list', c.repo)}>Download again</Button>
							{:else if c.state === 'missing' && c.settings.engine !== 'llama.cpp'}
								<Button disabled={off} testid="model-{c.id}-fetch" onclick={() => void fetchIt('list', c.repo)}>Download weights</Button>
							{/if}
							<Button tone="quiet" aria-expanded={open === c.id} testid="model-{c.id}-edit" onclick={() => toggle(c)}>{open === c.id ? 'Close' : 'Edit'}</Button>
						</div>
						{#if open === c.id && edit}
							<form class="line editor" onsubmit={(e) => (e.preventDefault(), save(c))} data-testid="model-{c.id}-editor">
								{@render settings(edit, c.settings.engine === 'llama.cpp', `model-${c.id}`)}
								<Button type="submit" disabled={off} testid="model-{c.id}-save">Save</Button>
							</form>
							<div class="line editor">
								<Switch label="Delete its weights too ({c.size})" on={weights} disabled={off} testid="model-{c.id}-weights" onchange={(on) => (weights = on)} />
								<HoldButton
									label="Delete {c.name}"
									tone="danger"
									disabled={off}
									testid="model-{c.id}-delete"
									onconfirm={() =>
										void act('list', `delete ${c.name}`, async () => {
											const res = (await deleteModel(c.id, weights)) as { freed_bytes?: number };
											open = '';
											return `Deleted ${c.name}${res.freed_bytes ? `, and freed ${bytesLabel(res.freed_bytes)}` : ''}.`;
										})}
								/>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
		{@render note('list')}
	</Section>

	{#if view}
		{#if view.downloads.length}
			<Section title="Downloads" count={view.downloads.length} testid="models-downloads">
				<ul class="rows">
					{#each view.downloads as d (d.repo)}
						<li class="model" data-testid="download-{d.repo}" data-state={d.kind}>
							<div class="who"><span class="name">{d.repo}</span>{@render progress(d)}</div>
							{#if d.addable}<Button disabled={off} testid="download-{d.repo}-add" onclick={() => prefill(d.repo)}>Add</Button>{/if}
						</li>
					{/each}
				</ul>
			</Section>
		{/if}

		{#if cards.length}
			<Section title="Cards" count={cards.length} lede="What each GPU holds now. A vLLM model takes its share of a whole card when it loads." testid="models-cards">
				<ul class="rows">
					{#each cards as g (g.index)}
						<li class="model" data-testid="card-{g.index}">
							<div class="who">
								<span class="name">Card {g.index} · {g.name}</span>
								<span class="meta">{g.used} of {g.total} in use, {g.free} free</span>
								<div class="bar split" aria-hidden="true">{#each g.segments as s (s.who)}<i class:mine={s.who === 'models'} style:inline-size="{s.pct}%"></i>{/each}</div>
								<span class="meta">{g.segments.map((s) => `${s.label} ${(s.mib / 1024).toFixed(1)} GB`).join(' · ') || 'Nothing on it'}</span>
								{#if g.tight}<span class="meta tight">Nearly full: the next load may not fit.</span>{/if}
							</div>
						</li>
					{/each}
				</ul>
			</Section>
		{/if}

		<Section title="Find a model" lede="Search Hugging Face. A download is the only traffic that leaves the house." testid="models-find">
			<form class="line" onsubmit={find}>
				<Field label="Search Hugging Face" placeholder="A model or an owner" bind:value={query} maxlength={80} testid="models-query" />
				<Button type="submit" disabled={off || !query.trim()} testid="models-search">Search</Button>
			</form>
			{#if found.length}
				<ul class="rows">
					{#each found as s (s.repo)}
						<li class="model" data-testid="found-{s.repo}">
							<div class="who">
								<span class="name">{s.repo}</span>
								<span class="meta">{[s.meta, s.size].filter(Boolean).join(' · ')}</span>
								{#if s.blocked}<span class="meta tight">{s.blocked}</span>{/if}
								{#if asking === s.repo}<span class="meta">{confirmText(s, view.disk.free)}</span>{/if}
							</div>
							<div class="line">
								{#if s.local}
									<Button disabled={off} testid="found-{s.repo}-add" onclick={() => prefill(s.repo)}>Add</Button>
								{:else if asking === s.repo}
									<Button tone="primary" disabled={off} testid="found-{s.repo}-yes" onclick={() => void fetchIt('find', s.repo)}>Download it</Button>
									<Button tone="quiet" testid="found-{s.repo}-no" onclick={() => (asking = '')}>Not now</Button>
								{:else if !s.blocked}
									<Button disabled={off} testid="found-{s.repo}-fetch" onclick={() => (asking = s.repo)}>Download</Button>
								{/if}
							</div>
						</li>
					{/each}
				</ul>
			{/if}
			{@render note('find')}
		</Section>

		<Section title="Add a model" lede="A model is a repo that is on disk, the engine that runs it and its settings. Download it first." testid="models-add">
			<form class="add" onsubmit={add} bind:this={addForm}>
				<div class="set"><span class="meta">Engine</span><Segmented label="Engine" options={ENGINE_OPTIONS} value={draft.engine as Engine} disabled={off} testid="models-add-engine" onchange={(v) => (draft.engine = v)} /></div>
				<div class="line">
					<label class="set"><span class="meta">Name it answers to</span><Field label="Model id" placeholder="lowercase-id" maxlength={64} disabled={off} bind:value={draft.id} testid="models-add-id" /></label>
					<label class="set"><span class="meta">Hugging Face repo</span><Field label="Hugging Face repo" placeholder="owner/name" maxlength={200} disabled={off} bind:value={draft.repo} testid="models-add-repo" /></label>
					{#if llama}
						<label class="set">
							<span class="meta">The one .gguf file to serve</span>
							<Field label="GGUF file" placeholder="model-q4_k_m.gguf" maxlength={200} disabled={off} aria-invalid={Boolean(fileError)} bind:value={draft.file} testid="models-add-file" />
						</label>
					{/if}
				</div>
				{#if fileError}<p class="said bad" role="alert" data-testid="models-add-file-error">{fileError}</p>{/if}
				<div class="line">{@render settings(draft, llama, 'models-add')}</div>
				<div class="line">
					<Button type="submit" tone="primary" disabled={off || Boolean(fileError)} testid="models-add-submit">Add model</Button>
					<Button disabled={off || Boolean(fileError)} testid="models-add-fetch" onclick={fetchDraft}>Download {llama ? 'the file' : 'the repo'}</Button>
				</div>
			</form>
			{@render note('add')}
		</Section>
	{/if}
</Screen>

<style>
	.model {
		justify-content: space-between;
	}
	.who {
		display: grid;
		flex: 1 1 var(--jv-measure-card);
		gap: var(--jv-space-1);
		min-inline-size: 0;
		justify-items: start;
	}
	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: end;
		gap: var(--jv-space-2) var(--jv-space-3);
	}
	/* The open model's settings take their own line under its row. */
	.editor {
		flex-basis: 100%;
	}
	.add {
		display: grid;
		gap: var(--jv-space-3);
	}
	.set {
		display: grid;
		gap: var(--jv-space-1);
	}
	.bar {
		inline-size: 100%;
		block-size: 1px;
		background: var(--jv-line-hair);
	}
	.bar i {
		display: block;
		block-size: 100%;
		background: var(--jv-violet);
		transform-origin: left;
		transition: transform var(--jv-dur-fast) var(--jv-ease-out);
	}
	/* A card's budget: one length per user, the models' in their own colour. */
	.split {
		display: flex;
	}
	.split i {
		background: var(--jv-text-faint);
	}
	.split i.mine {
		background: var(--jv-violet);
	}
	.bad {
		color: var(--jv-danger-text);
	}
	.tight {
		color: var(--jv-warn);
	}
	.said {
		margin: 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.said.bad {
		color: var(--jv-danger-text);
	}
	@media (prefers-reduced-motion: reduce) {
		.bar i {
			transition: none;
		}
	}
</style>
