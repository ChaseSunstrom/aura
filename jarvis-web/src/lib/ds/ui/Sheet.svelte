<!--
@component
A sheet over the stage: the native `<dialog>`, opened modal, so focus is
trapped, Escape closes it and the page behind is inert without a line of our
own. A thin cap names it; the body is the caller's.

Does not cover: a sheet that must not close on Escape (approval holds use the
stage, not a sheet), or stacking two sheets — the second opens over the first.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import Grab from './Grab.svelte';
	import { dragToClose } from './drag';

	interface Props {
		open: boolean;
		/** The dialog's accessible name and its cap. */
		label: string;
		testid?: string;
		onclose?: () => void;
		children: Snippet;
	}
	let { open = $bindable(), label, testid, onclose, children }: Props = $props();

	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) dialog.showModal();
		else if (!open && dialog.open) dialog.close();
	});

	function closed(): void {
		open = false;
		onclose?.();
	}
</script>

<dialog
	bind:this={dialog}
	class="sheet"
	aria-label={label}
	data-testid={testid}
	onclose={closed}
	{@attach (el) => dragToClose(el, () => dialog?.close())}
	onclick={(e) => {
		// A press on the backdrop is the dialog itself: close, like Escape.
		if (e.target === dialog) dialog?.close();
	}}
>
	{#if open}
		<Grab />
		<header class="cap" data-drag>
			<h2>{label}</h2>
			<button type="button" class="x" aria-label="Close" onclick={() => dialog?.close()}>×</button>
		</header>
		<div class="body">{@render children()}</div>
	{/if}
</dialog>

<style>
	/* A drawer at the inline end, the side panel's measure, sliding in as a
	   screen does (T-0725); on a phone it rises over the lower part instead. */
	.sheet {
		inline-size: var(--jv-measure-panel);
		max-inline-size: none;
		block-size: 100dvh;
		max-block-size: none;
		margin: 0 0 0 auto;
		padding: 0;
		border: 0;
		border-inline-start: 1px solid var(--jv-glass-edge, var(--jv-line-hair));
		border-radius: 0;
		background: var(--jv-glass, var(--jv-panel-solid));
		color: var(--jv-text);
		box-shadow: var(--jv-elev-float);
		font-family: var(--jv-font-body);
	}
	.sheet[open] {
		display: flex;
		flex-direction: column;
	}
	.sheet::backdrop {
		background: var(--jv-scrim);
	}
	.sheet[open] {
		animation: from-end var(--jv-dur-appear) var(--jv-ease-appear);
	}
	.cap {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: var(--jv-space-2) var(--jv-space-2) var(--jv-space-2) var(--jv-space-4);
		border-block-end: 1px solid var(--jv-line-hair);
	}
	h2 {
		margin: 0;
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		font-weight: var(--jv-weight-label);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-dim);
	}
	.x {
		min-inline-size: var(--jv-touch);
		min-block-size: var(--jv-touch);
		border: 0;
		background: transparent;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-lg);
		cursor: pointer;
	}
	.x:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.body {
		flex: 1;
		min-block-size: 0;
		padding: var(--jv-space-3) var(--jv-space-4) var(--jv-space-4);
		overflow: auto;
	}
	@keyframes from-end {
		from {
			translate: 100% 0;
		}
	}
	@keyframes rise {
		from {
			translate: 0 100%;
		}
	}
	@media (max-width: 720px) {
		.sheet {
			inline-size: 100vw;
			block-size: var(--jv-measure-panel-rise);
			margin: auto 0 0;
			border-inline-start: 0;
			border-block-start: 1px solid var(--jv-glass-edge, var(--jv-line-hair));
			border-radius: var(--jv-radius-md) var(--jv-radius-md) 0 0;
			overflow: visible;
		}
		.sheet[open] {
			animation-name: rise;
		}
		/* One frame for everything that rises on a phone (the owner, 2026-11-11: "some menus
		   look different than others"): the screens' panel wears the projections' corner and
		   leaves the core lit above it, so a sheet does too. */
		.sheet::backdrop {
			background: transparent;
		}
		.sheet::after {
			content: '';
			position: absolute;
			inset: 0 auto auto 0;
			inline-size: var(--jv-projection-bracket);
			block-size: var(--jv-projection-bracket);
			border-block-start: 1px solid var(--jv-accent);
			border-inline-start: 1px solid var(--jv-accent);
			box-shadow: var(--jv-glow-bracket);
			pointer-events: none;
		}
		:global(:root[data-marker='star']) .sheet::after {
			border: 0;
			box-shadow: none;
			background: var(--jv-theme-accent-gradient);
			clip-path: polygon(50% 0, 60% 40%, 100% 50%, 60% 60%, 50% 100%, 40% 60%, 0 50%, 40% 40%);
		}
		.cap {
			touch-action: none;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.sheet[open] {
			animation: none;
		}
	}
</style>
