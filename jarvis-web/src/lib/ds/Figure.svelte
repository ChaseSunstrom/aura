<!--
@component
A number set in Space Grotesk Light, with an optional unit: the one big thing a
projection says (21°, 64%, 3 lights).

```svelte
<Figure value="21" unit="°C" size="display" />
```

Does not cover: formatting. Pass the string you want read; a number that is not
finite is drawn as a dash rather than "NaN".
-->
<script lang="ts">
	import { toneColor, type Tone } from './tone';

	interface Props {
		value: string | number;
		unit?: string;
		size?: 'md' | 'lg' | 'display';
		tone?: Tone;
		testid?: string;
	}
	let { value, unit = '', size = 'lg', tone = 'text', testid = '' }: Props = $props();

	const shown = $derived(
		typeof value === 'number' && !Number.isFinite(value) ? '—' : String(value)
	);
</script>

<span class="figure {size}" style:--tone={toneColor(tone)} data-testid={testid || undefined}>
	<span class="value">{shown}</span>{#if unit}<span class="unit">{unit}</span>{/if}
</span>

<style>
	.figure {
		display: inline-flex;
		align-items: baseline;
		gap: var(--jv-space-1);
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-variant-numeric: tabular-nums;
		line-height: 1;
		color: var(--tone);
		text-shadow: var(--jv-glow-text);
	}
	.md {
		font-size: var(--jv-fs-xl);
	}
	.lg {
		font-size: var(--jv-fs-2xl);
	}
	.display {
		font-size: var(--jv-fs-display);
	}
	.unit {
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
</style>
