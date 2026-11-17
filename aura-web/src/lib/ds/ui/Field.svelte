<!--
@component
One line of text to type: a touch-target-tall input in the screens' look,
named by `label` (its aria-label; the placeholder is a hint, never the name).

Does not cover: validation (the form that holds it), or a multi-line answer.
-->
<script lang="ts">
	import type { HTMLInputAttributes } from 'svelte/elements';

	interface Props extends Omit<HTMLInputAttributes, 'value'> {
		label: string;
		value: string;
		testid?: string;
	}
	let { label, value = $bindable(''), testid, ...rest }: Props = $props();
</script>

<input class="field" aria-label={label} data-testid={testid} bind:value {...rest} />

<style>
	.field {
		flex: 1 1 var(--jv-measure-card);
		min-inline-size: 0;
		min-block-size: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-surface-sunken);
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-body);
	}
	.field::placeholder {
		color: var(--jv-text-faint);
	}
	.field:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
</style>
