<!--
  The turn's steps as the stage's steps projection draws them (T-0732): a mark per step says
  its state, the current step opens to its live detail (the page JARVIS has open, the sources
  a search brought back, a command's last lines, the state a device was left in), the older
  ones keep only what their words lack (a page's title, a count of sources, a device's new
  state), and Stop ends the run. The newest six are drawn; the rest are counted.
  The picture is the browser's latest frame, held in memory only (`live.ts`).
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import Button from '../ui/Button.svelte';
	import type { LiveStep } from './live';

	interface Props {
		steps: readonly LiveStep[];
		/** Ends the run; no Stop is drawn without it. */
		onstop?: () => void;
	}

	let { steps, onstop }: Props = $props();

	const SHOWN = 6;
	const list = $derived(steps.slice(-SHOWN));
	const earlier = $derived(steps.length - list.length);
	// The current step is the newest call; a trailing thinking or waiting row is not one.
	const current = $derived(list.findLast((s) => s.id !== 'thinking' && s.id !== 'waiting')?.id);
	const live = $derived(steps.some((s) => s.state === 'acting' || s.state === 'waiting' || s.state === 'thinking'));

	function host(url: string): string {
		try {
			return new URL(url).host.replace(/^www\./, '');
		} catch {
			return url;
		}
	}
	// The mark's meaning in words: colour and shape are not all a reader gets (WCAG 1.4.1).
	const SAID: Record<LiveStep['state'], string> = { thinking: 'thinking', acting: 'running', waiting: 'waiting on you', done: 'done', failed: 'failed' };
	const took = (ms: number) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
</script>

{#if earlier}<p class="earlier" data-testid="stage-steps-earlier">{earlier} earlier {earlier === 1 ? 'step' : 'steps'}</p>{/if}
<!-- A new step is announced; a step that changes in place (a longer tail) is not. -->
<ol class="steps" data-testid="stage-steps" aria-live="polite" aria-relevant="additions">
	{#each list as step, i (step.id)}
		{@const full = step.id === current}
		<li class="step" data-state={step.state} data-kind={step.kind} data-current={full || undefined} data-ds-row style:--i={i}>
			<span class="what">{step.text}</span>
			<span class="sr">{`(${SAID[step.state]})`}</span>
			{#if step.ms !== null && step.state !== 'acting' && step.state !== 'waiting'}<span class="took">{took(step.ms)}</span>{/if}
			{#if step.kind === 'browse' && (full || step.title)}
				<div class="detail">
					{#if full && step.image}
						<img class="page" src={step.image} alt={`The page ${wornName()} has open${step.title ? `: ${step.title}` : ''}`} decoding="async" data-testid="step-page" />
					{/if}
					<!-- The host only where the step's words do not already say it ("Using the browser · example.com"). -->
					<p class="line">{[full && step.url && !step.text.includes(host(step.url)) ? host(step.url) : '', step.title ?? ''].filter(Boolean).join(' · ')}</p>
				</div>
			{:else if step.kind === 'search' && step.sources?.length}
				<div class="detail">
					{#if full}
						<ul class="sources" data-testid="step-sources">
							{#each step.sources as source (source.url)}
								<li><span class="host">{host(source.url)}</span>{` ${source.title}`}</li>
							{/each}
						</ul>
					{:else}
						<p class="line">{`${step.sources.length} ${step.sources.length === 1 ? 'source' : 'sources'}`}</p>
					{/if}
				</div>
			{:else if step.kind === 'shell' && full && step.tail?.length}
				<div class="detail"><pre class="tail" data-testid="step-tail">{step.tail.join('\n')}</pre></div>
			{:else if step.kind === 'device' && step.entity && (full || step.left)}
				<div class="detail">
					<p class="line">{[step.left ? `${step.entity} → ${step.left}` : step.entity, full ? (step.service ?? '') : ''].filter(Boolean).join(' · ')}</p>
				</div>
			{:else if full && step.line}
				<div class="detail"><p class="line">{step.line}</p></div>
			{/if}
		</li>
	{/each}
</ol>
{#if live && onstop}
	<div class="stop"><Button tone="quiet" testid="stage-stop" onclick={onstop}>Stop</Button></div>
{/if}

<style>
	/* Oldest first: a mark per step says acting (cyan, breathing), waiting on you (amber),
	   thinking (cyan, dim), done (green) or failed (pink); the words stay text-coloured so
	   the list reads as one. */
	.steps {
		display: grid;
		gap: var(--jv-space-2);
		margin: var(--jv-space-2) 0 0;
		padding: 0;
		list-style: none;
		max-block-size: 52vh;
		overflow-y: auto;
	}
	.step {
		display: grid;
		grid-template-columns: var(--jv-space-2) 1fr auto;
		align-items: baseline;
		column-gap: var(--jv-space-2);
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	.step::before {
		content: '';
		inline-size: var(--jv-space-1);
		block-size: var(--jv-space-1);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-ok);
	}
	.step[data-state='acting'],
	.step[data-state='waiting'] {
		color: var(--jv-text-bright);
	}
	.step[data-state='acting']::before,
	.step[data-state='thinking']::before {
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
		animation: breathe var(--jv-dur-beat) var(--jv-ease-standard) infinite alternate;
	}
	.step[data-state='thinking'] {
		color: var(--jv-text-faint);
	}
	/* A ring waits, a square failed: the shape says it as well as the colour. */
	.step[data-state='waiting']::before {
		background: none;
		border: 1px solid var(--jv-warn);
	}
	.step[data-state='failed']::before {
		background: var(--jv-danger);
		border-radius: 0;
	}
	.sr {
		position: absolute;
		inline-size: 1px;
		block-size: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}
	.took {
		color: var(--jv-text-faint);
		font-size: var(--jv-fs-2xs);
		font-variant-numeric: tabular-nums;
	}
	.detail {
		grid-column: 2 / -1;
		display: grid;
		gap: var(--jv-space-1);
		margin-block-start: var(--jv-space-1);
		min-inline-size: 0;
	}
	.line {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
		overflow-wrap: anywhere;
	}
	.host {
		color: var(--jv-text-dim);
	}
	/* The page as JARVIS sees it: the top of the viewport, never taller than a strip of the stage. */
	.page {
		display: block;
		inline-size: 100%;
		max-block-size: 22vh;
		object-fit: cover;
		object-position: top;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-sm);
	}
	.sources {
		display: grid;
		gap: var(--jv-space-1);
		margin: 0;
		padding: 0;
		list-style: none;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-faint);
	}
	.sources li {
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}
	.tail {
		margin: 0;
		padding: var(--jv-space-1) var(--jv-space-2);
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-dim);
		background: var(--jv-surface-sunken);
		border-radius: var(--jv-radius-sm);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}
	.earlier {
		margin: var(--jv-space-2) 0 0;
		font-size: var(--jv-fs-2xs);
		color: var(--jv-text-faint);
	}
	/* Beside the projection's label, not under the list: a row of its own pushed the chart
	   under the steps off a 390x844 screen. */
	.stop {
		position: absolute;
		inset-block-start: 0;
		inset-inline-end: 0;
	}
	@keyframes breathe {
		from {
			opacity: 1;
		}
		to {
			opacity: 0.35;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.step::before {
			animation: none !important;
		}
	}
</style>
