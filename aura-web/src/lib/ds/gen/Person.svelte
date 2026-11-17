<!--
@component
Who someone is: a monogram, the name, the role and a few facts. Violet when the
facts come from memory.
Does not cover: a photo. The contract carries none, so a person is never drawn
from a picture the model fetched.
-->
<script lang="ts">
	import Label from '../Label.svelte';
	import { toneColor } from '../tone';

	import { initials } from './draw';

	interface Fact {
		label: string;
		value: string;
	}
	let { data }: { data: { name: string; role?: string; facts?: Fact[]; tone?: string } } =
		$props();
</script>

<div class="person" data-gen="person" style:--tone={toneColor(data.tone ?? 'accent')}>
	<span class="mono" aria-hidden="true">{initials(data.name)}</span>
	<div class="who">
		<span class="name">{data.name}</span>
		{#if data.role}<Label>{data.role}</Label>{/if}
	</div>
	{#if data.facts?.length}
		<dl>
			{#each data.facts as f, i (i)}
				<div data-ds-row style:--i={i}><dt>{f.label}</dt><dd>{f.value}</dd></div>
			{/each}
		</dl>
	{/if}
</div>

<style>
	.person {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: var(--jv-space-2) var(--jv-space-3);
		align-items: center;
		min-width: var(--jv-measure-card);
	}
	.mono {
		display: grid;
		place-items: center;
		width: var(--jv-touch);
		height: var(--jv-touch);
		border: 1px solid var(--tone);
		border-radius: var(--jv-radius-pill);
		font-family: var(--jv-font-display);
		font-size: var(--jv-fs-md);
		color: var(--tone);
		filter: drop-shadow(var(--jv-glow-sm));
	}
	.who {
		display: grid;
		gap: var(--jv-space-1);
	}
	.name {
		font-family: var(--jv-font-display);
		font-size: var(--jv-fs-lg);
		color: var(--jv-text-bright);
		overflow-wrap: anywhere;
	}
	dl {
		grid-column: 1 / -1;
		margin: 0;
		display: grid;
		gap: var(--jv-space-1);
	}
	dl div {
		display: flex;
		justify-content: space-between;
		gap: var(--jv-space-3);
	}
	dt {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	dd {
		margin: 0;
		color: var(--tone);
		text-align: right;
	}
</style>
