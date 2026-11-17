<!--
@component
The escape hatch: a page the model wrote, run where it can do nothing but draw.

The frame is `sandbox="allow-scripts"` with no `allow-same-origin`, so its origin
is opaque: no cookies, no storage, no `parent.document`. It loads
`/projection-frame`, a static bootstrap whose own CSP is `default-src 'none'`
with inline script and style only, so the page cannot fetch, load or post a
form; the HTML arrives by `postMessage` once the bootstrap is up. (A `srcdoc`
frame would inherit the console's CSP, which allows same-origin fetches.)

The frame is removed when the projection goes back into the core and after
`limits.custom_html_seconds`; it never renders on the server.

It runs only when the owner presses Run, and the time cap counts from then: a
sandboxed frame without site isolation shares the console's thread, so a
`while(true){}` the model wrote (or was steered into writing by a page it read)
would stall the HUD with no way for the cap to fire. Pressing Run is the owner
choosing to take that risk for this one visual.

Does not cover: a busy loop after Run, which still stalls the HUD until the
browser offers to stop the page.
-->
<script lang="ts">
	import { onMount } from 'svelte';

	import Label from '../Label.svelte';
	import { tokenPx } from '$lib/tokens';

	import { CONTRACT } from './contract';

	let { data, leaving = false }: { data: { html: string; height?: number }; leaving?: boolean } =
		$props();

	let mounted = $state(false);
	let running = $state(false);
	let expired = $state(false);
	let frame = $state<HTMLIFrameElement>();
	let sent = false;

	const live = $derived(mounted && running && !leaving && !expired);
	const height = $derived(data.height ?? tokenPx('--jv-measure-card-height') * 2);

	onMount(() => {
		mounted = true;
	});

	// The cap counts from Run, not from mount: a visual nobody started has used none of it.
	$effect(() => {
		if (!running) return;
		const stop = setTimeout(() => (expired = true), CONTRACT.limits.custom_html_seconds * 1000);
		return () => clearTimeout(stop);
	});

	// Once: if the page navigates its own frame, the next load is not the
	// bootstrap and must not be handed the HTML again.
	function load() {
		if (sent || !frame?.contentWindow) return;
		sent = true;
		// '*' because a sandboxed frame's origin is opaque ("null"); the HTML is
		// the model's own, so nothing secret goes with it.
		frame.contentWindow.postMessage(data.html, '*');
	}
</script>

<div class="custom" data-gen="custom_html" style:height="{height}px">
	{#if live}
		<iframe
			bind:this={frame}
			sandbox="allow-scripts"
			src="/projection-frame"
			title="A custom visual"
			referrerpolicy="no-referrer"
			data-testid="projection-frame"
			onload={load}
		></iframe>
	{:else if expired}
		<Label>Stopped after {CONTRACT.limits.custom_html_seconds} s</Label>
	{:else if mounted && !running && !leaving}
		<button type="button" class="run" data-testid="projection-run" onclick={() => (running = true)}>
			Run visual
		</button>
	{:else if !leaving}
		<Label>Loading…</Label>
	{/if}
</div>

<style>
	.custom {
		display: grid;
		place-items: center;
		min-width: var(--jv-measure-panel-min);
	}
	/* The presence strip's take-over button: a hairline, never a filled card. */
	.run {
		min-height: var(--jv-touch);
		min-width: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		border: 1px solid rgb(from var(--jv-accent) r g b / 0.4);
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text-dim);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		cursor: pointer;
	}
	.run:hover,
	.run:focus-visible {
		color: var(--jv-text);
		border-color: var(--jv-accent);
	}
	iframe {
		width: 100%;
		height: 100%;
		border: 0;
		background: transparent;
		color-scheme: dark;
	}
</style>
