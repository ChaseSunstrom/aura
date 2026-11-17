<!--
@component
01 MODELS: each kind of task on the left, the model that answered it on the
right (violet), a band between them as thick as the turns that took that path.
Packets run along each band, more and faster the more turns it carried, so the
busiest route reads at a glance and an idle one is still.

Does not cover: per-turn detail (which turn went where); the band is a count.
-->
<script lang="ts">
	import { frames } from './clock';

	interface Props {
		kinds: { id: string; words: string; turns: number }[];
		models: { name: string; turns: number }[];
		flows: { from: string; to: string; turns: number }[];
	}
	let { kinds, models, flows }: Props = $props();

	const W = 320;
	const LEFT = 104;
	const RIGHT = 216;
	const GAP = 10;
	const LABEL = 26;
	const H = $derived(Math.max(120, Math.max(kinds.length, models.length) * 34));
	const total = $derived(Math.max(1, flows.reduce((s, f) => s + f.turns, 0)));

	function stack<T extends { turns: number }>(rows: T[], scale: number): (T & { y: number; h: number })[] {
		const h = rows.map((r) => Math.max(3, r.turns * scale));
		const used = h.reduce((s, x) => s + x, 0) + GAP * Math.max(0, rows.length - 1);
		let y = (H - used) / 2;
		return rows.map((r, i) => {
			const at = { ...r, y, h: h[i] };
			y += h[i] + GAP;
			return at;
		});
	}

	// A thin band sits closer to its neighbour than a two-line label is tall (production: 109 and
	// 40 turns under 716 overlapped): each label keeps LABEL below the one above, the column
	// moved up as one if that runs off the bottom.
	function spread(centres: number[]): number[] {
		const out: number[] = [];
		for (const c of centres) out.push(Math.max(c, (out.at(-1) ?? -Infinity) + LABEL));
		const over = (out.at(-1) ?? 0) + 12 - H;
		return over > 0 ? out.map((y) => y - over) : out;
	}

	const layout = $derived.by(() => {
		const scale = (H - GAP * (Math.max(kinds.length, models.length) - 1)) / total;
		const left = stack(kinds, scale);
		const right = stack(models, scale);
		const usedL = new Map<string, number>();
		const usedR = new Map<string, number>();
		const bands = flows.map((f) => {
			const a = left.find((k) => k.id === f.from);
			const b = right.find((m) => m.name === f.to);
			const w = Math.max(1, f.turns * scale);
			const ya = (a?.y ?? 0) + (usedL.get(f.from) ?? 0) + w / 2;
			const yb = (b?.y ?? 0) + (usedR.get(f.to) ?? 0) + w / 2;
			usedL.set(f.from, (usedL.get(f.from) ?? 0) + w);
			usedR.set(f.to, (usedR.get(f.to) ?? 0) + w);
			const share = f.turns / total;
			return {
				key: `${f.from}>${f.to}`,
				w,
				p: [LEFT + 4, ya, (LEFT + RIGHT) / 2, ya, (LEFT + RIGHT) / 2, yb, RIGHT, yb] as const,
				// More turns, more packets and a quicker trip: a route that carried most of the day
				// takes under two seconds end to end, a rare one about eight.
				packets: Math.min(4, Math.max(1, Math.round(share * 6))),
				period: Math.min(8, Math.max(1.6, 8 / (0.6 + share * 4)))
			};
		});
		return { left, right, bands, leftY: spread(left.map((k) => k.y + k.h / 2)), rightY: spread(right.map((m) => m.y + m.h / 2)) };
	});

	const short = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

	let dots: SVGCircleElement[] = $state([]);
	function tick(t: number): void {
		let i = 0;
		for (const b of layout.bands) {
			const [x0, y0, x1, y1, x2, y2, x3, y3] = b.p;
			for (let k = 0; k < b.packets; k++, i++) {
				const el = dots[i];
				if (!el) continue;
				const u = (t / b.period + k / b.packets) % 1;
				const v = 1 - u;
				const x = v * v * v * x0 + 3 * v * v * u * x1 + 3 * v * u * u * x2 + u * u * u * x3;
				const y = v * v * v * y0 + 3 * v * v * u * y1 + 3 * v * u * u * y2 + u * u * u * y3;
				el.setAttribute('transform', `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
			}
		}
	}
</script>

<svg viewBox="0 0 {W} {H}" role="img" aria-label="Turns by kind of task and model" use:frames={tick}>
	{#each layout.bands as b (b.key)}
		<path class="band" d="M{b.p[0]} {b.p[1]} C{b.p[2]} {b.p[3]} {b.p[4]} {b.p[5]} {b.p[6]} {b.p[7]}" stroke-width={b.w} />
	{/each}
	{#each layout.left as k, i (k.id)}
		<rect class="node" x={LEFT} y={k.y} width="4" height={k.h} />
		<text class="name" x="0" y={layout.leftY[i] - 2}>{short(k.words, 15)}</text>
		<text class="count" x="0" y={layout.leftY[i] + 10}>{k.turns}</text>
	{/each}
	{#each layout.right as m, i (m.name)}
		<rect class="node model" x={RIGHT} y={m.y} width="4" height={m.h} />
		<text class="name model" x={RIGHT + 10} y={layout.rightY[i] - 2}><title>{m.name}</title>{short(m.name, 14)}</text>
		<text class="count" x={RIGHT + 10} y={layout.rightY[i] + 10}>{m.turns}</text>
	{/each}
	{#each layout.bands as b, bi (b.key)}
		{#each { length: b.packets } as _, k (k)}
			<circle class="packet" r="1.8" bind:this={dots[layout.bands.slice(0, bi).reduce((s, x) => s + x.packets, 0) + k]} transform="translate({b.p[0]} {b.p[1]})" />
		{/each}
	{/each}
</svg>

<style>
	svg {
		display: block;
		width: 100%;
		height: auto;
		overflow: visible;
	}
	.band {
		fill: none;
		stroke: var(--jv-violet);
		stroke-opacity: 0.22;
	}
	.node {
		fill: var(--jv-accent-deep);
	}
	.node.model {
		fill: var(--jv-violet);
	}
	text {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
	}
	.name {
		fill: var(--jv-text-dim);
	}
	.name.model {
		fill: var(--jv-violet);
	}
	.count {
		fill: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.packet {
		fill: var(--jv-accent);
	}
	@media (prefers-reduced-motion: reduce) {
		.packet {
			display: none;
		}
	}
</style>
