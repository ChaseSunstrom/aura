<!--
@component
What the worn persona may do: each capability and its level (Off, Read, Ask first,
Allowed), from `/hud capabilities`. Picking a level does not set it: it asks
JARVIS to (`capability_set`), which always asks the owner, so the change lands
only after the approval card's yes and shows here on the next read.

Does not cover: per-tool exceptions the matrix keeps whatever the level (a
lock, a destructive command, a first-time recipient), or the kill switch.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import Segmented from '$lib/ds/ui/Segmented.svelte';
	import State from '$lib/ds/ui/State.svelte';
	import { LEVELS, LEVEL_LABEL, type Capability, type Level } from '$lib/hud/data/capabilities';
	import type { Status } from '$lib/ui/status';

	interface Props {
		status: Status;
		error?: string;
		capabilities: readonly Capability[];
		/** Hands JARVIS the change; the host says what happens next. */
		onlevel: (c: Capability, level: Level) => void;
		onretry?: () => void;
		/** The line on what "Ask first" does; off where the host says it another way. */
		note?: boolean;
	}
	let { status, error = '', capabilities, onlevel, onretry, note = true }: Props = $props();
	const options = LEVELS.map((l) => ({ value: l, label: LEVEL_LABEL[l] }));
</script>

<State {status} {error} empty="No capability has a level to set." {onretry} testid="levels-state">
	<ul class="rows" data-testid="levels">
		{#each capabilities as c (c.id)}
			<li data-capability={c.id}>
				<span class="name">{c.name}</span>
				{#if c.level === null}<span class="meta">As the approval rules say</span>{/if}
				<Segmented label="What {wornName()} may do with {c.name}" {options} value={c.level as Level} onchange={(l) => onlevel(c, l)} />
			</li>
		{/each}
	</ul>
	{#if note}<p class="note">“Ask first” shows an approval on whichever device you’re using.</p>{/if}
</State>

<style>
	.note {
		margin: 0;
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
</style>
