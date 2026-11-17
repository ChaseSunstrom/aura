<!--
@component
05 LOAD: each GPU as a tank of its memory, filled from the bottom by who holds
it (the models in violet, the voice in the accent, anything else faint), and
the models llama-swap is serving beside it. The surface ripples only while a
model is starting or loading; a settled card is still.

Does not cover: the cards' compute load (the manager reports memory, not
utilisation) or a model's speed.
-->
<script lang="ts">
	import { frames } from './clock';

	interface Props {
		cards: { name: string; fill: number; usedMib: number; totalMib: number; users: { who: string; share: number }[] }[];
		models: { name: string; state: string }[];
	}
	let { cards, models }: Props = $props();

	const TOP = 6;
	const H = 116;
	const TW = 44;
	const tone = (who: string) => (who === 'models' ? 'violet' : who === 'voice' ? 'accent' : 'other');
	const tanks = $derived(
		cards.slice(0, 3).map((c, i) => {
			let y = TOP + H;
			const parts = c.users.map((u) => {
				const h = u.share * H;
				y -= h;
				return { who: u.who, tone: tone(u.who), y, h };
			});
			return { ...c, x: 4 + i * (TW + 22), parts, surface: TOP + H - c.fill * H };
		})
	);
	const listX = $derived(4 + tanks.length * (TW + 22) + 8);
	const moving = $derived(models.some((m) => m.state !== 'ready' && m.state !== 'stopped'));
	const gib = (mib: number) => (mib / 1024).toFixed(1);
	const short = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

	let waves: SVGPathElement[] = $state([]);
	// Flat again once the model is up: the last ripple must not stay frozen on a settled card.
	function tick(t: number): void {
		const amp = moving ? 1.6 : 0;
		tanks.forEach((k, i) => {
			const el = waves[i];
			if (!el) return;
			let d = `M${k.x} ${k.surface}`;
			for (let s = 0; s <= 8; s++) d += ` L${(k.x + (s / 8) * TW).toFixed(1)} ${(k.surface + Math.sin(t * 4 + s * 0.9 + i) * amp).toFixed(1)}`;
			el.setAttribute('d', d);
		});
	}
</script>

<svg viewBox="0 0 320 150" role="img" aria-label="Each card's memory by who holds it, and the models being served" use:frames={tick}>
	{#each tanks as k, i (k.name + i)}
		<rect class="shell" x={k.x} y={TOP} width={TW} height={H} />
		{#each k.parts as p (p.who)}
			<rect class="part {p.tone}" x={k.x + 1} y={p.y} width={TW - 2} height={Math.max(0, p.h)}><title>{p.who}</title></rect>
		{/each}
		<path class="surface" class:moving bind:this={waves[i]} d="M{k.x} {k.surface} L{k.x + TW} {k.surface}" />
		<text class="name" x={k.x} y={TOP + H + 13}><title>{k.name}</title>{short(k.name.replace(/^NVIDIA (GeForce )?/, ''), 9)}</text>
		<text class="note" x={k.x} y={TOP + H + 25}>{gib(k.usedMib)}/{gib(k.totalMib)}</text>
	{/each}
	{#if !tanks.length}
		<text class="note" x="4" y="20">No cards reported.</text>
	{/if}
	{#each models.slice(0, 6) as m, i (m.name)}
		<circle class="dot {m.state}" cx={listX + 3} cy={18 + i * 22} r="3" />
		<text class="name" x={listX + 12} y={22 + i * 22}><title>{m.name}</title>{short(m.name, 20)}</text>
		<text class="note" x="320" y={22 + i * 22} text-anchor="end">{m.state}</text>
	{/each}
	{#if !models.length}
		<text class="note" x={listX} y="22">No model loaded.</text>
	{/if}
</svg>

<style>
	svg {
		display: block;
		width: 100%;
		height: auto;
	}
	.shell {
		fill: none;
		stroke: var(--jv-line);
	}
	.part.violet {
		fill: var(--jv-violet);
		fill-opacity: 0.75;
	}
	.part.accent {
		fill: var(--jv-accent);
		fill-opacity: 0.75;
	}
	.part.other {
		fill: var(--jv-text-faint);
		fill-opacity: 0.4;
	}
	.surface {
		fill: none;
		stroke: var(--jv-text-bright);
		stroke-opacity: 0.5;
	}
	.surface.moving {
		stroke: var(--jv-accent);
		stroke-opacity: 1;
	}
	text {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		fill: var(--jv-text-faint);
		font-variant-numeric: tabular-nums;
	}
	.name {
		fill: var(--jv-text-dim);
	}
	.dot {
		fill: var(--jv-accent);
	}
	.dot.ready {
		fill: var(--jv-ok);
	}
</style>
