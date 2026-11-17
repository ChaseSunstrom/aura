<!--
@component
A job's steps and how far it is: done, the one in hand, the ones to come, and a
failed one in pink.
Does not cover: time left. It says where the job is, not when it ends.
-->
<script lang="ts">
	import { fmt } from './draw';

	interface Step {
		label: string;
		state: 'done' | 'active' | 'waiting' | 'failed';
	}
	let { data }: { data: { steps?: Step[]; value?: number } } = $props();

	const MARK = { done: '✓', active: '●', waiting: '○', failed: '✕' } as const;
</script>

<div class="progress" data-gen="progress">
	{#if data.value !== undefined}
		<div
			class="meter"
			role="progressbar"
			aria-valuemin={0}
			aria-valuemax={100}
			aria-valuenow={Math.round(data.value * 100)}
		>
			<span class="fill" style:transform="scaleX({data.value})"></span>
		</div>
		<span class="pct">{fmt(Math.round(data.value * 100))}%</span>
	{/if}
	{#if data.steps?.length}
		<ol>
			{#each data.steps as s, i (i)}
				<li class={s.state} data-ds-row style:--i={i}>
					<span class="mark" aria-hidden="true">{MARK[s.state]}</span>
					<span>{s.label}</span>
					<span class="sr">({s.state})</span>
				</li>
			{/each}
		</ol>
	{/if}
</div>

<style>
	.progress {
		display: grid;
		gap: var(--jv-space-2);
		min-width: var(--jv-measure-card);
	}
	.meter {
		height: 1px;
		background: var(--jv-line);
		overflow: hidden;
	}
	.fill {
		display: block;
		height: 100%;
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
		transform-origin: left;
		transition: transform var(--jv-dur-bar) var(--jv-ease-quick);
	}
	.pct {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	ol {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: var(--jv-space-2);
	}
	li {
		display: flex;
		gap: var(--jv-space-2);
		align-items: baseline;
	}
	.mark {
		width: var(--jv-space-4);
		font-family: var(--jv-font-chrome);
	}
	.done {
		color: var(--jv-text-dim);
	}
	.done .mark {
		color: var(--jv-ok);
	}
	.active .mark {
		color: var(--jv-accent);
		text-shadow: var(--jv-glow-text);
	}
	.waiting {
		color: var(--jv-text-faint);
	}
	.failed,
	.failed .mark {
		color: var(--jv-danger-text);
	}
	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}
</style>
