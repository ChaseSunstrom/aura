<!--
@component
A smooth line between two things on the stage: the core and a visual, or two
related visuals. It draws in (≈0.5s), and on each working beat a point of light
runs along it from `start` to `end`.

Like LeaderLine it lies over the whole stage and takes points as percent across
and down it.

```svelte
<Connector start={[50, 17]} end={[30, 60]} beat={beats} />
```

`beat` is a counter: each new value sends one pulse. Wire it to the core's
`onbeat` so the pulse leaves the core with the shock ring, not on a clock of its
own. Under reduced motion no pulse is sent.

Does not cover: routing round things. It is one curve; the host picks points
that do not cross a projection.
-->
<script lang="ts">
	import { connectorCurve, cleanPoints, type Point } from './lines';
	import { MS, prefersReducedMotion } from './motion';
	import { animationOffset } from './stage';

	interface Props {
		start: Point;
		end: Point;
		/** Each change sends one pulse of light from start to end. 0 sends none. */
		beat?: number;
		leaving?: boolean;
		from?: number;
		testid?: string;
		/** The id of the slot it ends on, for a spec to pair them. */
		target?: string;
	}
	let { start, end, beat = 0, leaving = false, from, testid = '', target = '' }: Props = $props();

	/** Samples along the curve for the pulse: enough that it visibly follows the bend. */
	const STEPS = 16;

	const ends = $derived(cleanPoints([start, end]));
	const curve = $derived(ends.length === 2 ? connectorCurve(ends[0], ends[1]) : null);
	const delay = $derived(animationOffset(leaving, from ?? (leaving ? 1 : 0)));

	let track: HTMLElement | undefined = $state();

	$effect(() => {
		if (!beat || !track || !curve || leaving || prefersReducedMotion()) return;
		// Transform only, so the pulse stays on the compositor: the track is the
		// stage's size, so translate(x%, y%) puts its corner (where the light is)
		// at stage point (x, y).
		const frames = Array.from({ length: STEPS + 1 }, (_, i) => {
			const [x, y] = curve.at(i / STEPS);
			return { transform: `translate(${x}%, ${y}%)`, opacity: i === 0 || i === STEPS ? 0 : 1 };
		});
		const run = track.animate(frames, { duration: MS.beat });
		return () => run.cancel();
	});
</script>

{#if curve}
	<div
		class="connector"
		class:leaving
		style:--ox="{ends[0][0]}%"
		style:--oy="{ends[0][1]}%"
		style:animation-delay="{delay}ms"
		aria-hidden="true"
		data-testid={testid || undefined}
		data-beat={beat || undefined}
		data-for={target || undefined}
	>
		<svg viewBox="0 0 100 100" preserveAspectRatio="none">
			<path d={curve.d} vector-effect="non-scaling-stroke" />
		</svg>
		<span class="track" bind:this={track}><i class="pulse"></i></span>
	</div>
{/if}

<style>
	.connector {
		position: absolute;
		inset: 0;
		pointer-events: none;
		color: var(--jv-accent);
		/* Draws in from its start: scaled from the first point, never a dash
		   offset (only transform, opacity and filter animate). */
		transform-origin: var(--ox) var(--oy);
		animation: connector-in var(--jv-dur-connector) var(--jv-ease-appear) both;
	}
	.connector.leaving {
		animation: connector-out var(--jv-dur-exit) var(--jv-ease-appear) reverse both;
	}
	svg {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		overflow: visible;
	}
	path {
		fill: none;
		stroke: currentColor;
		stroke-width: 1;
		opacity: 0.55;
	}
	.track {
		position: absolute;
		inset: 0;
		opacity: 0;
	}
	.pulse {
		position: absolute;
		top: 0;
		left: 0;
		width: var(--jv-space-2);
		height: var(--jv-space-2);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-core-spark);
		box-shadow: var(--jv-glow-md);
		transform: translate(-50%, -50%);
	}
	@keyframes connector-in {
		from { opacity: 0; transform: scale(0); }
		to { opacity: 1; transform: scale(1); }
	}
	@keyframes connector-out {
		from { opacity: 0; transform: scale(0); }
		to { opacity: 1; transform: scale(1); }
	}
</style>
