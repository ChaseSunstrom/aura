<!--
@component
One choice of a few, all in view: a row of pressed/unpressed buttons in a
labelled group (the approval rule, a capability's level). Arrow keys are the
browser's Tab; each option is its own button so a screen reader reads
"Ask first, toggle button, pressed".

Does not cover: more options than fit a row (use a select), or no choice at all.
-->
<script lang="ts" generics="T extends string">
	interface Props {
		label: string;
		options: readonly { value: T; label: string }[];
		value: T;
		disabled?: boolean;
		testid?: string;
		onchange: (value: T) => void;
	}
	let { label, options, value, disabled = false, testid, onchange }: Props = $props();
</script>

<div class="seg" role="group" aria-label={label} data-testid={testid}>
	{#each options as o (o.value)}
		<button type="button" aria-pressed={o.value === value} {disabled} onclick={() => o.value !== value && onchange(o.value)}>{o.label}</button>
	{/each}
</div>

<style>
	.seg {
		display: inline-flex;
		flex-wrap: wrap;
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
	}
	button {
		position: relative;
		min-block-size: var(--jv-touch);
		min-inline-size: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		border: 0;
		/* The track's own corners: under a round pack (AURA's radius is a pill) the pressed
		   segment is a pill inside the pill, not a square cut out of it (T-0900). */
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text-dim);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		cursor: pointer;
	}
	/* The rule between two segments is a line of its own, not a border, which would bend with a
	   round segment's corners. None beside the pressed one: it is a shape of its own. */
	button + button::before {
		content: '';
		position: absolute;
		inset-block: 0;
		inset-inline-start: 0;
		border-inline-start: 1px solid var(--jv-line-hair);
	}
	button[aria-pressed='true']::before,
	button[aria-pressed='true'] + button::before {
		content: none;
	}
	button[aria-pressed='true'] {
		color: var(--jv-accent-lift);
		background: var(--jv-wash);
		text-shadow: var(--jv-glow-text);
	}
	button:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	button:disabled {
		opacity: 0.45;
		cursor: default;
	}
</style>
