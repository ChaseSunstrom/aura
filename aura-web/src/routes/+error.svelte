<!--
	The page that renders when a route throws, or a path has no screen.

	Without this, SvelteKit's default error page appears: unstyled, in another
	typeface, with a stack trace where the interface was. It is the one screen a
	person sees at their least patient moment, so it is drawn like every other
	v2 screen, light on the dark ground, and says the two things worth knowing:
	what happened, and the way back.

	Does not cover: a screen that loads and then fails to fetch; each screen
	says that itself (`$lib/ds/ui/State.svelte`).
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { page } from '$app/state';
	import Label from '$lib/ds/Label.svelte';
	import Button from '$lib/ds/ui/Button.svelte';

	const detail = $derived(page.error?.message ? `${page.status}. ${page.error.message}` : `HTTP ${page.status}`);
</script>

<svelte:head><title>Jarvis {page.status}</title></svelte:head>

<main class="wrap" data-testid="route-error">
	<Label tone="danger">Error</Label>
	<div role="alert" data-testid="error">
		<h1>{page.status === 404 ? 'There is no screen here' : 'This screen failed to load'}</h1>
		<p>{detail}</p>
	</div>
	<div class="ways">
		<Button onclick={() => location.reload()}>Reload</Button>
		<Button tone="primary" href="/">Back to {wornName()}</Button>
	</div>
</main>

<style>
	:global(body) {
		background: var(--jv-stage-ground);
	}
	.wrap {
		display: grid;
		gap: var(--jv-space-4);
		max-inline-size: var(--jv-measure-dialog);
		min-block-size: 100dvh;
		align-content: center;
		margin: 0 auto;
		padding: var(--jv-space-6) var(--jv-space-4);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
	}
	h1 {
		margin: 0;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-xl);
		color: var(--jv-text-bright);
	}
	p {
		margin: var(--jv-space-2) 0 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.ways {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-3);
	}
</style>
