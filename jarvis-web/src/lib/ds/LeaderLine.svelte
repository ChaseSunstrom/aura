<!--
@component
A thin line from the core to the projection it is talking about, with a dot at
each end. Amber while an approval waits on that projection, so the eye goes
from the core to the thing that needs a yes.

It lies over the whole stage (`position: absolute; inset: 0` of its parent); its
points are percent across and down that box. It comes out of the core with the
projection and goes back into it on exit (`leaving`, `from` as Projection).

```svelte
<LeaderLine points={[[50, 44], [62, 30], [74, 30]]} waiting={approvalPending} />
```

Does not cover: knowing where the projection is. The host gives the corners.
-->
<script lang="ts">
	import { cleanPoints, type Point } from './lines';
	import { animationOffset } from './stage';

	interface Props {
		/** The corners, the first at the core and the last at the projection. Fewer than two drawable points: nothing is drawn. */
		points: readonly Point[];
		/** An approval is waiting on the projection: amber. */
		waiting?: boolean;
		leaving?: boolean;
		from?: number;
		testid?: string;
	}
	let { points, waiting = false, leaving = false, from, testid = '' }: Props = $props();

	const pts = $derived(cleanPoints(points));
	const first = $derived(pts[0]);
	const last = $derived(pts[pts.length - 1]);
	const delay = $derived(animationOffset(leaving, from ?? (leaving ? 1 : 0)));
</script>

{#if pts.length >= 2}
	<div
		class="leader"
		class:leaving
		class:waiting
		style:--ox="{first[0]}%"
		style:--oy="{first[1]}%"
		style:animation-delay="{delay}ms"
		aria-hidden="true"
		data-testid={testid || undefined}
		data-waiting={waiting || undefined}
	>
		<svg viewBox="0 0 100 100" preserveAspectRatio="none">
			<polyline points={pts.map((p) => p.join(',')).join(' ')} vector-effect="non-scaling-stroke" />
		</svg>
		<i class="dot" style:left="{first[0]}%" style:top="{first[1]}%"></i>
		<i class="dot" style:left="{last[0]}%" style:top="{last[1]}%"></i>
	</div>
{/if}

<style>
	.leader {
		position: absolute;
		inset: 0;
		pointer-events: none;
		color: var(--jv-accent);
		/* Grows out of the core: scaled from the first point. */
		transform-origin: var(--ox) var(--oy);
		animation: leader-in var(--jv-dur-appear) var(--jv-ease-appear) both;
	}
	.leader.leaving {
		animation: leader-out var(--jv-dur-exit) var(--jv-ease-appear) reverse both;
	}
	.leader.waiting {
		color: var(--jv-warn);
	}
	svg {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		overflow: visible;
	}
	polyline {
		fill: none;
		stroke: currentColor;
		stroke-width: 1;
		opacity: 0.7;
	}
	.dot {
		position: absolute;
		width: var(--jv-space-1);
		height: var(--jv-space-1);
		border-radius: var(--jv-radius-pill);
		background: currentColor;
		box-shadow: var(--jv-glow-sm);
		transform: translate(-50%, -50%);
	}
	@keyframes leader-in {
		from { opacity: 0; transform: scale(0); }
		to { opacity: 1; transform: scale(1); }
	}
	@keyframes leader-out {
		from { opacity: 0; transform: scale(0); }
		to { opacity: 1; transform: scale(1); }
	}
</style>
