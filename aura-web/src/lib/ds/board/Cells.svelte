<!--
@component
A row of cells per group, each cell one item coloured by what became of it:
03 TASKS (every turn by kind: answered, cut off, no reply, still running) and
06 SUITES (every bench task by category: passed lately, passed once, never).
A cell still running breathes in the accent; the rest stand still.

Does not cover: what an item was (a cell is a count, never a message).
-->
<script lang="ts">
	import { frames } from './clock';

	interface Props {
		rows: { id: string; words: string; note: string; cells: string[] }[];
		/** Cell meaning → its words, in legend order. */
		legend: Record<string, string>;
		/** The cell kind that breathes, if any. */
		live?: string;
		label: string;
	}
	let { rows, legend, live, label }: Props = $props();

	const W = 320;
	// Past five groups (06 SUITES' eleven categories) a row is one line, its name beside its
	// cells, so the panel keeps the height of the others in the grid.
	const compact = $derived(rows.length > 5);
	const ROW = $derived(compact ? 17 : 30);
	const X0 = $derived(compact ? 118 : 0);
	const X1 = $derived(compact ? W - 34 : W);
	const CY = $derived(compact ? 2 : 14);
	const cols = $derived(Math.max(20, ...rows.map((r) => r.cells.length)));
	const cw = $derived((X1 - X0) / cols);
	const H = $derived(rows.length * ROW + 16);
	const short = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

	let pulse: SVGGElement | undefined = $state();
	function tick(t: number): void {
		pulse?.setAttribute('opacity', (0.5 + 0.5 * Math.sin(t * 4)).toFixed(2));
	}
</script>

<svg viewBox="0 0 {W} {H}" role="img" aria-label={label} use:frames={tick}>
	{#each rows as r, ri (r.id)}
		<text class="name" x="0" y={ri * ROW + 10}><title>{r.words}</title>{compact ? short(r.words, 17) : r.words}</text>
		<text class="note" x={W} y={ri * ROW + 10} text-anchor="end">{r.note}</text>
		{#each r.cells as c, ci (ci)}
			{#if c !== live}
				<rect class="cell {c}" x={X0 + ci * cw} y={ri * ROW + CY} width={cw - 1.5} height="10" style:animation-delay="calc({ci} * var(--jv-stagger-project) / 2)" />
			{/if}
		{/each}
	{/each}
	<g bind:this={pulse}>
		{#each rows as r, ri (r.id)}
			{#each r.cells as c, ci (ci)}
				{#if c === live}<rect class="cell {c}" x={X0 + ci * cw} y={ri * ROW + CY} width={cw - 1.5} height="10" />{/if}
			{/each}
		{/each}
	</g>
	{#each Object.entries(legend) as [k, words], i (k)}
		<rect class="cell {k} key" x={i * 80} y={H - 9} width="7" height="7" />
		<text class="note" x={i * 80 + 11} y={H - 2}>{words}</text>
	{/each}
</svg>

<style>
	svg {
		display: block;
		width: 100%;
		height: auto;
	}
	text {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
	}
	.name {
		fill: var(--jv-text-dim);
	}
	.note {
		fill: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.cell {
		animation: land var(--jv-dur-row-in) var(--jv-ease-appear) both;
	}
	.cell.key {
		animation: none;
	}
	.answered {
		fill: var(--jv-ok);
		fill-opacity: 0.8;
	}
	.cut_off {
		fill: var(--jv-warn);
	}
	.no_reply {
		fill: var(--jv-danger);
	}
	.running,
	.recent {
		fill: var(--jv-accent);
	}
	.passed {
		fill: var(--jv-ok);
		fill-opacity: 0.4;
	}
	.never {
		fill: none;
		stroke: var(--jv-tick);
	}
	@keyframes land {
		from {
			opacity: 0;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.cell {
			animation: none;
		}
	}
</style>
