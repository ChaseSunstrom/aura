<!--
@component
One Board panel's chrome, after the Sampo live Board: a numbered accent chip,
the UPPERCASE name, what it shows in a few dim words, the live figure on the
right, the drawing, and a footer of what the figure rests on. 1px rules and one
cyan bracket; no card, no shadow.

A stale panel says its age in the footer, in amber; an unavailable one replaces
its drawing with the reason. Neither draws an old or missing number as current.

Does not cover: the drawing (the mechanism passed as children).
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	import type { Chrome } from './model';

	interface Props {
		chrome: Chrome;
		/** Its place in the grid: panels rise one after another. */
		index?: number;
		children: Snippet;
	}
	let { chrome, index = 0, children }: Props = $props();
</script>

<article
	class="panel"
	data-testid="board-panel-{chrome.n}"
	data-state={chrome.state}
	aria-labelledby="board-{chrome.n}"
	style:animation-delay="calc({index} * var(--jv-stagger-project) * 2)"
>
	<header>
		<span class="n" aria-hidden="true">{chrome.n}</span>
		<h2 id="board-{chrome.n}">{chrome.name}</h2>
		{#if chrome.state !== 'unavailable'}<span class="label" data-testid="board-label-{chrome.n}">{chrome.label}</span>{/if}
		<span class="what">{chrome.what}</span>
	</header>
	<div class="body">
		{#if chrome.state === 'unavailable'}
			<p class="why" role="status">Unavailable{chrome.why ? `: ${chrome.why}` : '.'}</p>
		{:else}
			{@render children()}
		{/if}
	</div>
	<footer>
		<span class="foot" class:stale={chrome.state === 'stale'}>{chrome.foot}</span>
		{#if chrome.state !== 'unavailable' && chrome.stat}<span class="stat">{chrome.stat}</span>{/if}
	</footer>
</article>

<style>
	.panel {
		position: relative;
		display: grid;
		grid-template-rows: auto 1fr auto;
		gap: var(--jv-space-3);
		min-width: 0;
		padding: var(--jv-space-3) var(--jv-space-4);
		border: 1px solid var(--jv-line-soft);
		background: var(--jv-projection-tint), var(--jv-projection-backing);
		animation: rise var(--jv-dur-row-in) var(--jv-ease-appear) both;
	}
	.panel::before {
		content: '';
		position: absolute;
		top: -1px;
		left: -1px;
		width: var(--jv-projection-bracket);
		height: var(--jv-projection-bracket);
		border-top: 1px solid var(--jv-accent);
		border-left: 1px solid var(--jv-accent);
		box-shadow: var(--jv-glow-bracket);
		pointer-events: none;
	}
	header {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: baseline;
		gap: var(--jv-space-1) var(--jv-space-2);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
	}
	.n {
		padding: 0 var(--jv-space-1);
		border: 1px solid var(--jv-accent-deep);
		background: var(--jv-wash);
		color: var(--jv-accent);
		font-variant-numeric: tabular-nums;
	}
	h2 {
		margin: 0;
		color: var(--jv-text-bright);
		font: inherit;
		letter-spacing: inherit;
	}
	.what {
		grid-column: 1 / -1;
		color: var(--jv-text-faint);
		letter-spacing: normal;
		text-transform: none;
	}
	.label {
		color: var(--jv-accent);
		font-variant-numeric: tabular-nums;
		text-shadow: var(--jv-glow-text);
	}
	.body {
		min-height: 0;
	}
	.why {
		margin: 0;
		padding: var(--jv-space-5) 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	footer {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		gap: var(--jv-space-1) var(--jv-space-3);
		padding-top: var(--jv-space-2);
		border-top: 1px solid var(--jv-line-hair);
		color: var(--jv-text-faint);
		font: var(--jv-fs-2xs) var(--jv-font-chrome);
		font-variant-numeric: tabular-nums;
	}
	.foot.stale {
		color: var(--jv-warn);
	}
	.stat {
		color: var(--jv-text-dim);
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translateY(var(--jv-space-3));
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.panel {
			animation: none;
		}
	}
</style>
