<!--
@component
A screen nothing gets past until it is answered: the lock and first-run setup.
A modal `<dialog>`, so everything behind it is inert to the pointer, the
keyboard and a screen reader, and, unlike `Sheet`, Escape and the backdrop do
not dismiss it.

Does not cover: anything a lock is not (`lib/hud/lock.ts`). The page behind is
still in the DOM; a person with the developer tools is past it.
-->
<script lang="ts">
	import Starfield from '../Starfield.svelte';
	import { starfieldWorn } from '../theme.svelte';
	import type { Snippet } from 'svelte';

	interface Props {
		/** The dialog's accessible name. */
		label: string;
		testid?: string;
		children: Snippet;
	}
	let { label, testid, children }: Props = $props();

	let dialog: HTMLDialogElement | undefined = $state();
	$effect(() => {
		if (dialog && !dialog.open) dialog.showModal();
	});
</script>

<dialog bind:this={dialog} class="gate" aria-label={label} data-testid={testid} oncancel={(e) => e.preventDefault()}>
	<!-- Under AURA the gate is her space, opaque: the locked console is behind it (T-0898). -->
	{#if starfieldWorn()}<Starfield />{/if}
	<div class="body">{@render children()}</div>
</dialog>

<style>
	.gate {
		inline-size: 100%;
		max-inline-size: none;
		block-size: 100%;
		max-block-size: none;
		margin: 0;
		padding: var(--jv-space-5);
		border: 0;
		background: var(--jv-bg);
		color: var(--jv-text);
	}
	.gate::backdrop {
		background: var(--jv-bg);
	}
	.body {
		display: grid;
		gap: var(--jv-space-3);
		inline-size: min(100%, var(--jv-measure-dialog));
		margin: var(--jv-space-7) auto 0;
	}
</style>
