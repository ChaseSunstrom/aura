<!--
@component
One row of Settings: its control (the body), then one line that says what it
does, "More" for a second line, and "Ask" to hand the change to the assistant.
The words come from `settings-rows.ts`, so the search and the row agree.

A row the search does not match stays mounted and is hidden: unmounting it
would drop a half-typed field and reload a list.

Does not cover: setting anything (the control does), or what the assistant
does with the ask.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import { wornName } from '$lib/ds/theme.svelte';
	import { askFor, settingRow } from './settings-rows';

	interface Props {
		id: string;
		/** The ids the search shows. */
		shown: ReadonlySet<string>;
		onask: (text: string) => void;
		children?: Snippet;
	}
	let { id, shown, onask, children }: Props = $props();
	const row = $derived(settingRow(id));
	let open = $state(false);
</script>

<div class="setting" data-setting={id} hidden={!shown.has(id)}>
	{@render children?.()}
	<p class="hint">
		<span class="says" data-testid="hint-{id}">{row.hint}</span>
		<button type="button" aria-expanded={open} aria-controls="more-{id}" onclick={() => (open = !open)}>{open ? 'Less' : 'More'}</button>
		<button type="button" aria-label="Ask {wornName()} to change {row.label}" data-testid="ask-{id}" onclick={() => onask(askFor(row))}>Ask</button>
	</p>
	<p class="more" id="more-{id}" hidden={!open}>{row.more}</p>
</div>

<style>
	.setting {
		display: grid;
		gap: var(--jv-space-1);
		min-inline-size: 0;
	}
	.setting[hidden],
	.more[hidden] {
		display: none;
	}
	.hint,
	.more {
		margin: 0;
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.hint {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
	}
	/* The words take the line; More and Ask keep their width at its end. */
	.says {
		flex: 1 1 0;
		min-inline-size: 0;
	}
	.more {
		color: var(--jv-text-faint);
	}
	button {
		flex: none;
		padding: 0;
		border: 0;
		background: none;
		color: var(--jv-accent);
		font: var(--jv-fs-2xs) var(--jv-font-body);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		cursor: pointer;
	}
	button:hover {
		color: var(--jv-text-bright);
	}
	button:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	/* A finger's width: the two words become touch targets. */
	@media (max-width: 720px) {
		button {
			min-block-size: var(--jv-touch);
			min-inline-size: var(--jv-touch);
		}
	}
</style>
