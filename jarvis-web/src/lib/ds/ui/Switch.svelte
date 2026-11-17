<!--
@component
One thing that is on or off: a switch (`role="switch"`), its name beside it, a
touch target tall. `onchange` gets the state asked for; the host sets `on`.

Does not cover: a choice of three or more (`Segmented`), or a switch that
confirms first (the host's job).
-->
<script lang="ts">
	interface Props {
		label: string;
		on: boolean;
		disabled?: boolean;
		testid?: string;
		onchange: (on: boolean) => void;
	}
	let { label, on, disabled = false, testid, onchange }: Props = $props();
</script>

<button type="button" class="switch" role="switch" aria-checked={on} {disabled} data-testid={testid} onclick={() => onchange(!on)}>
	<span class="track" aria-hidden="true"><span class="thumb"></span></span>
	<span class="name">{label}</span>
</button>

<style>
	.switch {
		display: inline-flex;
		align-items: center;
		gap: var(--jv-space-3);
		min-block-size: var(--jv-touch);
		padding: 0;
		border: 0;
		background: none;
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
		cursor: pointer;
	}
	.switch:disabled {
		opacity: 0.45;
		cursor: default;
	}
	.switch:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.track {
		display: inline-flex;
		align-items: center;
		inline-size: var(--jv-space-6);
		block-size: var(--jv-space-4);
		padding: 1px;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-surface-sunken);
		transition: border-color var(--jv-dur-fast) var(--jv-ease-standard), background var(--jv-dur-fast) var(--jv-ease-standard);
	}
	.thumb {
		inline-size: var(--jv-space-3);
		block-size: var(--jv-space-3);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-text-faint);
		transition: transform var(--jv-dur-fast) var(--jv-ease-standard), background var(--jv-dur-fast) var(--jv-ease-standard);
	}
	[aria-checked='true'] {
		color: var(--jv-text-bright);
	}
	[aria-checked='true'] .track {
		border-color: var(--jv-accent);
		background: var(--jv-wash);
	}
	[aria-checked='true'] .thumb {
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
		/* The track's inner width (less its two hairline edges and paddings) less the thumb. */
		transform: translateX(calc(var(--jv-space-6) - var(--jv-space-3) - 1px * 4));
	}
	@media (prefers-reduced-motion: reduce) {
		.track,
		.thumb {
			transition: none;
		}
	}
</style>
