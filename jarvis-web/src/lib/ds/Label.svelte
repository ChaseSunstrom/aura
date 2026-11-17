<!--
@component
A caption: JetBrains Mono, uppercase, tracked .22em. It names what is next to
it; it is never the content.

```svelte
<Label>Living room</Label>
<Label tone="violet">Memory</Label>
```

Does not cover: hiding itself from assistive tech. It is real text; when it
only repeats a heading, mark the wrapper `aria-hidden`.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	import { toneColor, type Tone } from './tone';

	interface Props {
		/** Faint by default; violet for memory, embeddings and models; warn while something waits. */
		tone?: Tone;
		testid?: string;
		children: Snippet;
	}
	let { tone = 'faint', testid = '', children }: Props = $props();
</script>

<span class="label" style:--tone={toneColor(tone)} data-tone={tone} data-testid={testid || undefined}>
	{@render children()}
</span>

<style>
	.label {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		font-weight: var(--jv-weight-label);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--tone);
	}
	/* A pack whose labels are a gradient (AURA's accent → memory, `data-label` from
	   theme.svelte.ts) paints the plain caption with it; a toned one keeps its tone. */
	:global(:root[data-label='gradient']) .label[data-tone='faint'] {
		background: var(--jv-theme-label-fill);
		background-clip: text;
		color: transparent;
	}
</style>
