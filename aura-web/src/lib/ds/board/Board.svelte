<!--
@component
The Board (T-0733): seven numbered panels of what JARVIS has been doing, each a
mechanism driven by its own number: 01 MODELS (kinds of task flowing to the
model that answered), 02 TURNS (an hour a bar), 03 TASKS (a cell a turn, by how
it ended), 04 TOOLS (a minute a bar, a hand that turns at the call rate),
05 LOAD (each card a tank), 06 SUITES (a cell a bench task), 07 VERIFIED (a cell
a check of the deploy's smoke or a surface's golden turn: passed on this deploy, an earlier
one, failed, never or skipped; T-0580).

One column on a phone, two on a tablet, three on a wide screen. Each panel says
its own state: stale with its age, unavailable with the reason.

Does not cover: fetching (`ds/screens/Board.svelte`) or the numbers (`model.ts`).
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import Cells from './Cells.svelte';
	import Dial from './Dial.svelte';
	import Flows from './Flows.svelte';
	import Hours from './Hours.svelte';
	import type { Panel as PanelData } from './model';
	import Panel from './Panel.svelte';
	import Tanks from './Tanks.svelte';

	interface Props {
		panels: PanelData[];
	}
	let { panels }: Props = $props();

	const QUIET = `Nothing in the last 24 hours. Ask ${wornName()} something and it shows here.`;
</script>

<div class="board">
<div class="grid" data-testid="board">
	{#each panels as p, i (p.kind)}
		<Panel chrome={p.chrome} index={i}>
			{#if p.kind === 'models'}
				{#if p.flows.length}<Flows kinds={p.kinds} models={p.models} flows={p.flows} />{:else}<p class="quiet">{QUIET}</p>{/if}
			{:else if p.kind === 'turns'}
				{#if p.peak}<Hours hours={p.hours} series={p.series} peak={p.peak} />{:else}<p class="quiet">{QUIET}</p>{/if}
			{:else if p.kind === 'tasks'}
				{#if p.rows.length}
					<Cells
						rows={p.rows.map((r) => ({ id: r.id, words: r.words, note: String(r.turns), cells: r.cells }))}
						legend={{ answered: 'answered', cut_off: 'cut off', no_reply: 'no reply', running: 'running' }}
						live="running"
						label="Turns by kind of task and how each ended"
					/>
				{:else}<p class="quiet">{QUIET}</p>{/if}
			{:else if p.kind === 'tools'}
				<Dial perMinute={p.perMinute} peak={p.peak} rate={p.rate} top={p.top} />
			{:else if p.kind === 'load'}
				<Tanks cards={p.cards} models={p.models} />
			{:else if p.kind === 'suites'}
				<Cells
					rows={p.categories.map((c) => ({ id: c.id, words: c.words, note: `${c.cells.filter((x) => x !== 'never').length}/${c.cells.length}`, cells: c.cells }))}
					legend={{ recent: 'lately', passed: 'passed', never: 'never' }}
					label="Bench tasks by category and when each last passed"
				/>
			{:else if p.kind === 'verified'}
				<Cells
					rows={p.rows.map((r) => ({ id: r.id, words: r.words, note: r.note, cells: [r.cell] }))}
					legend={{ recent: 'this deploy', passed: 'earlier', no_reply: 'failed', never: 'no pass' }}
					label="The deploy's checks and each surface's turn, and when each last passed"
				/>
			{/if}
		</Panel>
	{/each}
</div>
</div>

<style>
	/* Columns by the board's own width, not the window's: in the console's side
	   panel (T-0725) a wide window still leaves it two thirds of a phone. */
	.board {
		container-type: inline-size;
	}
	.grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--jv-space-4);
	}
	@container (min-width: 700px) {
		.grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}
	@container (min-width: 1200px) {
		.grid {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}
	.quiet {
		margin: 0;
		padding: var(--jv-space-5) 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
</style>
