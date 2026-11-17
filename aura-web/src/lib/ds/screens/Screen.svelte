<!--
@component
A full screen's frame: its title, the way back to the core, and its body.
The shell's top bar stays above it, so the launcher, ⌘K and the devices are
one press away from every screen.

Does not cover: the screen's own states (each body draws loading, empty,
error and offline through `ds/ui/State`).
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		/** One line under the title. */
		lede?: string;
		testid?: string;
		/** Panels side by side (the Board): the wide measure, not a dialog's. */
		wide?: boolean;
		/** Beside the title, on its line: a screen's search. */
		tools?: Snippet;
		children?: Snippet;
	}
	let { title, lede = '', testid, wide = false, tools, children }: Props = $props();
</script>

<section class="screen" class:wide aria-labelledby="screen-title" data-testid={testid}>
	<header>
		<a class="back" href="/">Back to the core</a>
		<div class="title">
			<h1 id="screen-title">{title}</h1>
			{@render tools?.()}
		</div>
		{#if lede}<p class="lede">{lede}</p>{/if}
	</header>
	{@render children?.()}
</section>

<style>
	.screen {
		flex: 1;
		width: 100%;
		max-width: var(--jv-measure-dialog);
		margin: 0 auto;
		padding: var(--jv-space-5) var(--jv-space-4) var(--jv-space-7);
		overflow-y: auto;
	}
	.screen.wide {
		max-width: var(--jv-measure-wide);
	}
	header {
		display: grid;
		gap: var(--jv-space-2);
		margin-bottom: var(--jv-space-5);
	}
	.back {
		justify-self: start;
		display: inline-flex;
		align-items: center;
		min-height: var(--jv-touch);
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-chrome);
		text-decoration: none;
	}
	.back:hover {
		color: var(--jv-text-bright);
	}
	.back:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.title {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
	}
	h1 {
		margin: 0;
		color: var(--jv-text-bright);
		font: var(--jv-fs-2xl) var(--jv-font-display);
		letter-spacing: var(--jv-track-logo);
	}
	.lede {
		margin: 0;
		color: var(--jv-text-dim);
		font: var(--jv-fs-md) var(--jv-font-body);
	}
	/* A phone: the cap every risen menu has (`ui/Sheet`): its name in the label's hand and
	   a cross at the end, which is the same way back to the core. */
	@media (max-width: 720px) {
		.screen {
			padding-block-start: 0;
		}
		header {
			grid-template-columns: 1fr auto;
			align-items: start;
			margin-bottom: var(--jv-space-3);
		}
		.title {
			grid-area: 1 / 1;
			min-block-size: var(--jv-touch);
		}
		.back {
			grid-area: 1 / 2;
			justify-content: center;
			min-inline-size: var(--jv-touch);
			font-size: 0;
		}
		.back::after {
			content: '×';
			font-size: var(--jv-fs-lg);
		}
		h1 {
			color: var(--jv-text-dim);
			font: var(--jv-weight-label) var(--jv-fs-2xs) var(--jv-font-chrome);
			letter-spacing: var(--jv-track-label);
			text-transform: uppercase;
		}
		.lede {
			grid-column: 1 / -1;
		}
	}
</style>
