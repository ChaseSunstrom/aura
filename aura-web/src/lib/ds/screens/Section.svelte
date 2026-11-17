<!--
@component
One titled part of a full screen: its cap (the board's small uppercase line),
an optional icon and count, an optional line under it, and its body. A list inside
marked `class="rows"` gets the screens' one row look: a hairline between
rows, each at least a touch target tall.

Does not cover: the body's states (`ds/ui/State` inside the body).
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Label } from '$lib/ds';

	interface Props {
		title: string;
		count?: number;
		lede?: string;
		/** A 24-grid stroke path drawn before the cap. */
		icon?: string;
		/** Kept mounted but not shown: a search that matches none of its rows. */
		hidden?: boolean;
		testid?: string;
		children?: Snippet;
	}
	let { title, count, lede = '', icon = '', hidden = false, testid, children }: Props = $props();
	const id = $derived(`section-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`);
</script>

<section class="section" aria-labelledby={id} data-testid={testid} {hidden}>
	<h2 {id}>{#if icon}<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d={icon} /></svg>{/if}<Label>{title}{count !== undefined ? ` · ${count}` : ''}</Label></h2>
	{#if lede}<p class="lede">{lede}</p>{/if}
	{@render children?.()}
</section>

<style>
	.section {
		display: grid;
		gap: var(--jv-space-3);
		padding-block: var(--jv-space-4);
		border-block-start: 1px solid var(--jv-line-hair);
	}
	/* `display: grid` would show a hidden section. */
	.section[hidden] {
		display: none;
	}
	h2 {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		margin: 0;
		font: inherit;
	}
	.icon {
		flex: none;
		inline-size: var(--jv-space-4);
		block-size: var(--jv-space-4);
		fill: none;
		stroke: var(--jv-text-faint);
		stroke-width: 1.5;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.lede {
		margin: 0;
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.section :global(.rows) {
		display: grid;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.section :global(.rows > li) {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		min-block-size: var(--jv-touch);
		padding-block: var(--jv-space-2);
		border-block-end: 1px solid var(--jv-line-hair);
	}
	.section :global(.name) {
		flex: 1 1 var(--jv-space-7);
		/* Never narrower than its longest word, so a long meta wraps below instead of
		   squeezing the name: at 390 a device read "Worksh op Deskto p" (T-0690).
		   break-word, not anywhere: anywhere makes the minimum one letter. */
		min-inline-size: min-content;
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-chrome);
		overflow-wrap: break-word;
	}
	.section :global(.meta) {
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
</style>
