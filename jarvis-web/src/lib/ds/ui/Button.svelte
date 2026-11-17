<!--
@component
The v2 button: light on the dark, never a filled slab. `primary` glows in the
accent, `ghost` is a hairline, `quiet` is text only, `danger` says no in red.
Every one is at least `--jv-touch` tall and wide (44 px).

Does not cover: holding to confirm (`HoldButton`) or a link that navigates —
pass `href` and it renders an `<a>` with the same look.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	interface Props extends Omit<HTMLButtonAttributes, 'children'> {
		tone?: 'primary' | 'ghost' | 'quiet' | 'danger';
		href?: string;
		testid?: string;
		children: Snippet;
	}
	let { tone = 'ghost', href, testid, children, type = 'button', ...rest }: Props = $props();
</script>

{#if href}
	<a class="b {tone}" {href} data-testid={testid}>{@render children()}</a>
{:else}
	<button class="b {tone}" {type} data-testid={testid} {...rest}>{@render children()}</button>
{/if}

<style>
	.b {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: var(--jv-space-2);
		min-block-size: var(--jv-touch);
		min-inline-size: var(--jv-touch);
		padding: 0 var(--jv-space-4);
		border: 1px solid transparent;
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		font-weight: var(--jv-weight-label);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		text-decoration: none;
		cursor: pointer;
		transition:
			opacity var(--jv-dur-fast) var(--jv-ease-standard),
			filter var(--jv-dur-fast) var(--jv-ease-standard);
	}
	.b:hover:not(:disabled) {
		filter: brightness(1.25);
	}
	.b:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.b:disabled {
		opacity: 0.45;
		cursor: default;
	}
	.primary {
		border-color: var(--jv-accent);
		color: var(--jv-accent-lift);
		box-shadow: var(--jv-glow-sm);
		text-shadow: var(--jv-glow-text);
	}
	.ghost {
		border-color: var(--jv-line-hair);
	}
	.quiet {
		color: var(--jv-text-dim);
		padding: 0 var(--jv-space-2);
	}
	.danger {
		border-color: var(--jv-danger);
		color: var(--jv-danger-text);
	}
	@media (prefers-reduced-motion: reduce) {
		.b {
			transition: none;
		}
	}
</style>
