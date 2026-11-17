<!--
@component
`/gallery/steps`: the stage's steps projection (`$lib/ds/activity`) with fixture steps of every
kind and state, so a change to it is judged without a live turn. Fixtures live here, in the
unit tests and in mock-hermes's browse turn only; what ships reads the socket (T-0732).
-->
<script lang="ts">
	import { Label, Projection } from '$lib/ds';
	import type { LiveStep } from '$lib/ds/activity/live';
	import Steps from '$lib/ds/activity/Steps.svelte';

	// A 160x100 drawing of a page stands in for the browser's frame.
	const PAGE =
		'data:image/jpeg;base64,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';

	const browsing: LiveStep[] = [
		{ id: 's1', text: 'Searched the web · tide times', state: 'done', kind: 'search', ms: 1200, query: 'tide times', sources: [{ title: 'Tide tables', url: 'https://tides.example/a' }, { title: 'Harbour times', url: 'https://harbour.example/b' }] },
		{ id: 'b1', text: 'Opening tides.example', state: 'acting', kind: 'browse', ms: null, url: 'https://tides.example/a', title: 'Tide tables', image: PAGE }
	];
	const shell: LiveStep[] = [
		{ id: 'd1', text: 'Turned on the lab light', state: 'done', kind: 'device', ms: 300, entity: 'light.lab', service: 'light.turn_on', left: 'on' },
		{ id: 't1', text: 'Running a command · ls -la /srv', state: 'done', kind: 'shell', ms: 90, command: 'ls -la /srv', tail: ['drwxr-xr-x 2 root root 4096 backups', 'drwxr-xr-x 5 root root 4096 media'] },
		{ id: 'thinking', text: 'Thinking', state: 'thinking', kind: 'other', ms: null }
	];
	const asking: LiveStep[] = [
		{ id: 't2', text: 'Running a command · rm -rf /tmp/scratch', state: 'waiting', kind: 'shell', ms: null, command: 'rm -rf /tmp/scratch', tail: [] }
	];
	const failed: LiveStep[] = [
		{ id: 'b2', text: 'Opening nowhere.example — net::ERR_NAME_NOT_RESOLVED', state: 'failed', kind: 'browse', ms: 2100, url: 'https://nowhere.example/', error: 'net::ERR_NAME_NOT_RESOLVED' },
		{ id: 'o1', text: 'Frob append entry', state: 'done', kind: 'other', ms: 40, line: 'text: a note' }
	];
	const long: LiveStep[] = Array.from({ length: 9 }, (_, i) => ({ id: `l${i}`, text: `Step ${i + 1}`, state: i < 8 ? 'done' : 'acting', kind: 'other', ms: 100 * (i + 1) }) as LiveStep);

	const cases = [
		{ key: 'browsing', steps: browsing },
		{ key: 'house', steps: shell },
		{ key: 'waiting', steps: asking },
		{ key: 'failed', steps: failed },
		{ key: 'long', steps: long }
	];
	let stopped = $state(0);
</script>

<svelte:head><title>Steps · gallery</title></svelte:head>

<main class="gallery" data-testid="steps-gallery" data-stopped={stopped}>
	{#each cases as c (c.key)}
		<section class="cell" aria-label={c.key} data-testid="steps-{c.key}">
			<Projection edge="left" label="Steps">
				<Label tone={c.steps.some((s) => s.state === 'failed') ? 'danger' : c.steps.some((s) => s.state === 'waiting') ? 'warn' : 'accent'}>Steps</Label>
				<Steps steps={c.steps} onstop={() => stopped++} />
			</Projection>
		</section>
	{/each}
</main>

<style>
	.gallery {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, var(--jv-strip-width)), 1fr));
		gap: var(--jv-space-6);
		padding: var(--jv-space-6) var(--jv-space-4);
		min-block-size: 100vh;
		/* The projections' glow bleeds past their box; the page itself never scrolls sideways. */
		overflow-x: clip;
		background: var(--jv-stage-ground);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
	}
</style>
