<script lang="ts">
	/*
	 * The app shell, which is only what every route shares: the styles, the
	 * two links and the reader's text size. The v2 shell (`$lib/ds/shell`)
	 * draws everything else; there is no toast rail (the v2 brief: no toast
	 * survives), a moment comes out of the core instead.
	 */
	import { onDestroy, onMount } from 'svelte';

	import '$lib/styles/fonts.css';
	import '$lib/styles/tokens.css';
	// After the tokens: each theme pack re-points them under :root[data-theme=…] (T-0861).
	import '$lib/styles/themes.css';
	import '$lib/styles/base.css';

	import { ConsoleLink } from '$lib/consoleLink';
	import { HermesLink } from '$lib/hermes/link';
	import { provideHermes } from '$lib/hermes/store.svelte';
	import { provideConsole } from '$lib/hud/links.svelte';
	import { applyTextSize, readTextSize } from '$lib/textSize';

	let { children } = $props();

	// The reader's own text size: one multiplier on the root element.
	onMount(() => applyTextSize(document, readTextSize(localStorage)));

	/*
	 * The house socket: the one connection in the app that reconnects. The
	 * HUD's house, telemetry and every jarvis-core-fed panel ride on it, and a
	 * tier-3 approval raised by a voice turn arrives on it — so it runs on
	 * every route, the gallery included, and is never stopped for a path.
	 * The index (states, registries) is switched on by the HUD for the
	 * layouts that read it.
	 */
	const link = new ConsoleLink({ indexed: false });
	const house = provideConsole(link);

	/*
	 * The link to Hermes: the assistant's own socket, distinct from the house
	 * link above. One per app, owned here, handed down through context.
	 * Started on mount (it dials a WebSocket, which the server has none of)
	 * and stopped with the layout.
	 */
	const hermesLink = new HermesLink();
	const hermes = provideHermes(hermesLink);

	onMount(() => {
		link.start();
		hermesLink.start();
		return () => hermesLink.stop();
	});

	onDestroy(() => {
		link.stop();
		house.dispose();
		hermes.view.dispose();
	});
</script>

{@render children()}
