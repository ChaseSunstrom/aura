<!--
@component
One theme pack as a tile: its ground, its accent gradient as a small core, its name and
its colour swatches, painted in the pack's own colours whatever the page wears.

```svelte
<ThemeTile pack={THEMES.aura} worn={theme.id === 'aura'}>
	<Button onclick={wear}>Wear</Button>
</ThemeTile>
```

The tile re-declares the pack's `--jv-*` colours on itself (`packVars`, colours only:
`validPack` admitted nothing else), so everything inside it, the actions included,
reads as that pack.

Does not cover: the pack's fonts (they ride on `data-theme`, which only :root wears).
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	import { packVars, type Pack } from './pack';

	interface Props {
		pack: Pack;
		/** Shown as the theme on screen now. */
		worn?: boolean;
		/** A line under the name: whose it is, or that it is a draft. */
		note?: string;
		testid?: string;
		children?: Snippet;
	}
	let { pack, worn = false, note = '', testid = '', children }: Props = $props();

	const vars = $derived(
		Object.entries(packVars(pack))
			.map(([name, value]) => `${name}: ${value}`)
			.join('; ')
	);
	const swatches = ['--jv-bg', '--jv-accent', '--jv-violet', '--jv-text'];
</script>

<article class="tile" style={vars} data-worn={worn || undefined} data-testid={testid || undefined}>
	<div class="sky" aria-hidden="true"><span class="orb"></span></div>
	<div class="body">
		<div class="head">
			<h3 class="name">{pack.name}</h3>
			<div class="swatches" aria-hidden="true">
				{#each swatches as s (s)}<span class="swatch" style:background="var({s})"></span>{/each}
			</div>
		</div>
		<div class="foot">
			<p class="note">{note}</p>
			{#if children}<div class="actions">{@render children()}</div>{/if}
		</div>
	</div>
</article>

<style>
	.tile {
		display: flex;
		flex-direction: column;
		overflow: hidden;
		border: 1px solid var(--jv-theme-panel-edge);
		border-radius: var(--jv-radius-md);
		background: var(--jv-theme-panel-fill), var(--jv-bg);
		color: var(--jv-text);
	}
	.tile[data-worn] {
		border-color: var(--jv-accent);
		box-shadow: var(--jv-glow-bracket);
	}
	/* A low sky and the action beside the note, to keep Settings short (v2-screens.spec). */
	.sky {
		display: grid;
		place-items: center;
		aspect-ratio: 16 / 4;
		background: var(--jv-theme-backdrop);
	}
	.orb {
		width: var(--jv-space-7);
		height: var(--jv-space-7);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-theme-accent-gradient);
		box-shadow: var(--jv-glow-bracket);
	}
	.body {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
		padding: var(--jv-space-3);
	}
	.head,
	.foot {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--jv-space-2);
	}
	.name {
		margin: 0;
		font-family: var(--jv-font-display);
		font-size: var(--jv-fs-md);
		font-weight: var(--jv-weight-display);
	}
	.note {
		margin: 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-xs);
	}
	.swatches {
		display: flex;
		gap: var(--jv-space-1);
	}
	.swatch {
		width: var(--jv-space-4);
		height: var(--jv-space-4);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
	}
	.foot {
		flex-wrap: wrap;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
</style>
