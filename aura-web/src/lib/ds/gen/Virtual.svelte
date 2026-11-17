<!--
@component
A long list drawn a window at a time: the rows in view and four either side,
with spacers standing in for the rest, so two thousand rows cost a dozen nodes.
Every row is one touch target tall (`--jv-touch`); the box shows eight before
it scrolls.

Rows render through the `row` snippet: `(item, index, fresh)`. `fresh` is true
until the first scroll; mark a fresh row `data-ds-row` so the first screen
slides in and rows scrolled into view simply appear.

Does not cover: rows of different heights. A row taller than `--jv-touch` is
clipped by its own CSS or the window drifts.
-->
<script lang="ts" generics="T">
	import type { Snippet } from 'svelte';

	import { tokenPx } from '$lib/tokens';

	import { windowOf } from './draw';

	interface Props {
		items: readonly T[];
		row: Snippet<[T, number, boolean]>;
		/** A row above the window that stays put (a table's header). */
		head?: Snippet;
		role?: 'list' | 'table';
		label?: string;
	}
	let { items, row, head, role = 'list', label }: Props = $props();

	const ROW = tokenPx('--jv-touch');
	const SHOWN = 8;

	let top = $state(0);
	let fresh = $state(true);
	const win = $derived(windowOf(top, ROW * SHOWN, ROW, items.length));
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
	class="virtual"
	tabindex="0"
	onscroll={(e) => {
		top = e.currentTarget.scrollTop;
		fresh = false;
	}}
>
	<div
		{role}
		aria-label={label}
		aria-rowcount={role === 'table' ? items.length + 1 : undefined}
	>
		{#if head}{@render head()}{/if}
		<div style:height="{win.start * ROW}px" aria-hidden="true"></div>
		{#each items.slice(win.start, win.end) as item, k (win.start + k)}
			{@render row(item, win.start + k, fresh)}
		{/each}
		<div style:height="{(items.length - win.end) * ROW}px" aria-hidden="true"></div>
	</div>
</div>

<style>
	.virtual {
		max-height: calc(var(--jv-touch) * 8);
		overflow-y: auto;
		overscroll-behavior: contain;
		scrollbar-width: thin;
		scrollbar-color: var(--jv-tick) transparent;
	}
	.virtual:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
</style>
