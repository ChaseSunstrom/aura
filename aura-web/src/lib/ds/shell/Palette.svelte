<!--
@component
"Search or command" (⌘K): one line that ranks the screens and the few things
the shell can do (`hud/palette.ts`), and hands anything it does not match to
JARVIS. Enter runs the highlighted row, the arrows move it, Escape closes.

Does not cover: searching the house's entities or history (ask JARVIS: the
fallback row does), or a chord per row (the shell owns ⌘K alone).
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import Sheet from '$lib/ds/ui/Sheet.svelte';
	import { rank, type PaletteItem } from '$lib/hud/palette';

	interface Props {
		open: boolean;
		items: readonly PaletteItem[];
		/** A query nothing matched, or the person chose to ask. */
		onask: (text: string) => void;
	}
	let { open = $bindable(), items, onask }: Props = $props();

	let query = $state('');
	let active = $state(0);
	const ASK = 'palette-ask';

	const hits = $derived.by(() => {
		const q = query.trim();
		const found = rank(items, q, 12);
		if (!q) return found;
		const ask: PaletteItem = { id: ASK, group: 'Actions', title: `Ask ${wornName()}: ${q}`, run: () => onask(q) };
		// Nothing matched: asking is the answer, so it comes first.
		return found.length ? [...found, ask] : [ask];
	});

	// A fresh sheet starts empty on the first row; a typed letter moves the
	// highlight back to the top, where the best match is.
	$effect(() => {
		if (!open) query = '';
	});
	$effect(() => {
		void query;
		active = 0;
	});

	const optionId = (i: number) => `palette-option-${i}`;

	function run(item: PaletteItem | undefined): void {
		if (!item) return;
		open = false;
		item.run();
	}

	function keys(e: KeyboardEvent): void {
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			const n = hits.length;
			if (n) active = (active + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
		} else if (e.key === 'Enter') {
			e.preventDefault();
			run(hits[active]);
		}
	}
</script>

<Sheet bind:open label="Search or command" testid="palette">
	<!-- svelte-ignore a11y_autofocus -->
	<input
		class="query"
		role="combobox"
		aria-label="Search or command"
		aria-expanded="true"
		aria-controls="palette-list"
		aria-autocomplete="list"
		aria-activedescendant={hits.length ? optionId(active) : undefined}
		placeholder="A screen, an action, or ask {wornName()}"
		autocomplete="off"
		spellcheck="false"
		autofocus
		bind:value={query}
		onkeydown={keys}
	/>
	<ul id="palette-list" role="listbox" aria-label="Results">
		{#each hits as item, i (item.id)}
			<!-- The combobox owns the keys; a row answers the pointer. -->
			<!-- svelte-ignore a11y_click_events_have_key_events -->
			<li id={optionId(i)} role="option" aria-selected={i === active} class:active={i === active} onclick={() => run(item)} onpointermove={() => (active = i)}>
				<span class="title">{item.title}</span>
				{#if item.sub}<span class="sub">{item.sub}</span>{/if}
				<span class="group">{item.id === ASK ? 'Ask' : item.group}</span>
			</li>
		{/each}
	</ul>
</Sheet>

<style>
	.query {
		inline-size: 100%;
		min-block-size: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text-bright);
		font: inherit;
		font-size: var(--jv-fs-md);
	}
	.query:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	ul {
		display: grid;
		margin: var(--jv-space-3) 0 0;
		padding: 0;
		list-style: none;
	}
	li {
		display: grid;
		grid-template-columns: 1fr auto;
		align-items: center;
		column-gap: var(--jv-space-3);
		min-block-size: var(--jv-touch);
		padding: var(--jv-space-1) var(--jv-space-3);
		border-radius: var(--jv-radius-sm);
		cursor: pointer;
	}
	li.active {
		background: var(--jv-wash);
		box-shadow: inset var(--jv-rule-live) 0 0 var(--jv-accent);
	}
	.title {
		color: var(--jv-text);
	}
	.sub {
		grid-column: 1;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
	}
	.group {
		grid-column: 2;
		grid-row: 1;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
</style>
