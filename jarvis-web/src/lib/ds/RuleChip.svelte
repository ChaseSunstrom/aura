<!--
@component
A rule, drawn small: a standing approval, an automation, a preference JARVIS
will keep to ("Always · dim the hall after 23:00"). A hairline, a kind and the
rule's words; with `onremove`, a 44px button that takes it away.

```svelte
<RuleChip kind="Always" text="Dim the hall after 23:00" onremove={() => drop(rule.id)} />
```

Does not cover: confirming the removal. A rule the host cannot get back should
ask first (HoldButton).
-->
<script lang="ts">
	import Label from './Label.svelte';
	import { toneColor, type Tone } from './tone';

	interface Props {
		text: string;
		/** The kind of rule, drawn as a label before the words. */
		kind?: string;
		tone?: Tone;
		onremove?: () => void;
		/** The remove button's name; defaults to "Remove <text>". */
		removeLabel?: string;
		testid?: string;
	}
	let { text, kind = 'Rule', tone = 'accent', onremove, removeLabel, testid = '' }: Props =
		$props();
</script>

<span class="chip" style:--tone={toneColor(tone)} data-testid={testid || undefined}>
	<Label {tone}>{kind}</Label>
	<span class="text">{text}</span>
	{#if onremove}
		<button type="button" class="remove" aria-label={removeLabel ?? `Remove ${text}`} onclick={() => onremove?.()}>
			<span aria-hidden="true">×</span>
		</button>
	{/if}
</span>

<style>
	.chip {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-2);
		max-width: 100%;
		min-height: var(--jv-touch);
		padding: 0 0 0 var(--jv-space-3);
		border: 1px solid rgb(from var(--tone) r g b / 0.4);
		border-radius: var(--jv-radius-sm);
		color: var(--jv-text);
		text-shadow: var(--jv-glow-text);
	}
	.chip:not(:has(.remove)) {
		padding-right: var(--jv-space-3);
	}
	.text {
		min-width: 0;
		font-size: var(--jv-fs-sm);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.remove {
		flex: none;
		width: var(--jv-touch);
		height: var(--jv-touch);
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-lg);
		cursor: pointer;
	}
	.remove:hover,
	.remove:focus-visible {
		color: var(--tone);
	}
</style>
