<!--
@component
`/gallery/projections`: every generative-UI type drawn from its contract
example, the same types "light" (only their required fields, so a visual with
nothing optional is judged too), and the loading, empty and error states. Each
has a Back button that sends it into the core the way the host does.

  ?live=1   nothing but what the socket sends: each `hud.projection` frame is
            filed by id and drawn. When one is up (a tick for the mount, then
            the next animation frame) its time goes in
            `window.__projected[id]` (performance.now()), so a spec measures
            emission to paint from the page's own clock.

What `e2e/projections.spec.ts` drives and the screenshots in
`docs/screens/v2/projections/` are made of. Development only, like the rest of
/gallery: it ships because the e2e suite tests the production build.
-->
<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { page } from '$app/state';
	import { HermesClient } from '$lib/hermes/client';
	import { tokenMs } from '$lib/tokens';

	import {
		Label,
		PROJECTION_CONTRACT as C,
		PROJECTION_TYPES,
		Projected,
		projectionFromEvent
	} from '$lib/ds';

	interface Item {
		key: string;
		type: string;
		data: unknown;
		leaving?: boolean;
		gone?: boolean;
	}

	const live = page.url.searchParams.get('live') === '1';

	/** The example with only the fields its type requires (title is never required). */
	function light(type: string): Record<string, unknown> {
		const example = C.examples[type] as Record<string, unknown>;
		const fields = C.types[type].fields;
		return Object.fromEntries(Object.entries(example).filter(([k]) => fields[k]?.required));
	}

	const all: Item[] = PROJECTION_TYPES.map((t) => ({ key: t, type: t, data: C.examples[t] }));
	const lights: Item[] = PROJECTION_TYPES.filter(
		(t) => Object.keys(light(t)).length < Object.keys(C.examples[t] as object).length
	).map((t) => ({ key: `light-${t}`, type: t, data: light(t) }));
	const states: Item[] = [
		{ key: 'loading', type: 'bar', data: undefined },
		{ key: 'empty', type: 'bar', data: { title: 'Tables at eight', bars: [] } },
		{ key: 'error', type: C.malformed[0].type, data: C.malformed[0].data },
		{ key: 'unknown', type: 'hologram', data: {} }
	];

	let sections = $state([
		{ name: 'Every type', items: all },
		{ name: 'Light: required fields only', items: lights },
		{ name: 'States', items: states }
	]);
	let frames = $state<Item[]>([]);
	let link = $state('idle');

	function back(item: Item) {
		item.leaving = true;
		setTimeout(() => (item.gone = true), tokenMs('--jv-dur-exit'));
	}
	function again(item: Item) {
		item.leaving = false;
		item.gone = false;
	}

	onMount(() => {
		if (!live) return;
		const seen: Record<string, number> = {};
		(window as unknown as { __projected: Record<string, number> }).__projected = seen;
		const client = new HermesClient();
		const off = client.onEvent((e) => {
			if (e.type !== 'hud.projection') return;
			const f = projectionFromEvent(e.payload);
			if (!f) return;
			const item = { key: f.id, type: f.type, data: f.data };
			const at = frames.findIndex((x) => x.key === f.id);
			if (at >= 0) frames[at] = item;
			else frames.push(item);
			delete seen[f.id];
			tick().then(() => requestAnimationFrame(() => (seen[f.id] = performance.now())));
		});
		const unlink = client.onStatus((s) => (link = s));
		client.start();
		return () => {
			off();
			unlink();
			client.stop();
		};
	});
</script>

<svelte:head>
	<title>Projections</title>
	<meta name="robots" content="noindex" />
</svelte:head>

{#snippet cell(item: Item)}
	<div class="cell" data-testid="cell-{item.key}">
		<div class="bar">
			<Label>{item.key}</Label>
			{#if item.gone}
				<button type="button" onclick={() => again(item)}>Project again</button>
			{:else}
				<button type="button" disabled={item.leaving} onclick={() => back(item)}>Back</button>
			{/if}
		</div>
		{#if !item.gone}
			<Projected
				type={item.type}
				data={item.data}
				placement={{ leaving: item.leaving }}
				testid="projected-{item.key}"
			/>
		{/if}
	</div>
{/snippet}

<main
	class="page"
	data-testid="projections-gallery"
	data-live={live || undefined}
	data-link={live ? link : undefined}
>
	<h1>Projections</h1>
	{#if live}
		<section aria-label="Live">
			<div class="grid">
				{#each frames as item (item.key)}{@render cell(item)}{/each}
			</div>
			{#if !frames.length}<p class="note">Waiting for a projection</p>{/if}
		</section>
	{:else}
		{#each sections as s (s.name)}
			<section aria-label={s.name}>
				<h2>{s.name}</h2>
				<div class="grid">
					{#each s.items as item (item.key)}{@render cell(item)}{/each}
				</div>
			</section>
		{/each}
	{/if}
</main>

<style>
	.page {
		min-height: 100vh;
		background: var(--jv-stage-ground);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
		/* The backing bleeds past the projection's box; a narrower side made the page
		   scroll sideways (scrollWidth 15px over at both widths). */
		padding: var(--jv-space-4) var(--jv-projection-bleed);
		display: grid;
		gap: var(--jv-space-5);
		align-content: start;
	}
	h1,
	h2 {
		margin: 0 0 var(--jv-space-3);
		font-family: var(--jv-font-chrome);
		font-weight: normal;
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	h1 {
		font-size: var(--jv-fs-sm);
	}
	h2 {
		font-size: var(--jv-fs-2xs);
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, var(--jv-measure-panel-min)), 1fr));
		gap: var(--jv-space-4);
		align-items: start;
	}
	.cell {
		display: grid;
		gap: var(--jv-space-2);
		min-width: 0;
	}
	.bar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-2);
	}
	button {
		min-height: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		background: transparent;
		border: 1px solid var(--jv-line-hair);
		color: var(--jv-text-dim);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		cursor: pointer;
	}
	button:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	button:disabled {
		color: var(--jv-text-faint);
		cursor: default;
	}
	.note {
		color: var(--jv-text-faint);
	}
</style>
