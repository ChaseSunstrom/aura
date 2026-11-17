<!--
@component
Content projected out of the core as light: a dark radial backing, one cyan
bracket at the top left, glowing text, an edge line where it butts against
something. Never a card: no fill edge, no frosted glass, no big radius.

It comes out of the core. A bright point leaves the core, travels to its place,
opens into a thin line and grows to full height, turned slightly toward the
core (≈0.5s). Leaving plays the same path backwards (≈0.45s). Both directions
are one curve of the stage's presence, so the stage can turn it round from any
point without a jump (`from`).

```svelte
<Projection core={{ x: 720, y: 396 }} leaving={v.leaving} from={v.from} label="Outside">
	<Label>Outside</Label>
	<Figure value="14" unit="°C" />
	{#each rows as row, i}<div data-ds-row style:--i={i}>{row}</div>{/each}
</Projection>
```

Children marked `data-ds-row` (with `style:--i={index}`) slide in 35ms apart;
`data-ds-bar` rise from their foot (≈0.4s). Nothing is traced: a chart is
whole the moment it can be seen.

Does not cover: placing itself. The host lays projections out; this measures
where it landed so it can come out of the core, on mount and when `core` moves
(not on a resize the host does not report).
-->
<script lang="ts">
	import type { Snippet } from 'svelte';

	import { animationOffset } from './stage';

	interface Props {
		/** The core's centre in viewport pixels. Omitted: the projection grows in place. */
		core?: { x: number; y: number };
		/** Play the exit: collapse back into the core. */
		leaving?: boolean;
		/**
		 * The stage view's `from`: presence when this animation began, so a reversal
		 * continues instead of restarting. Defaults to a fresh entrance or exit.
		 */
		from?: number;
		/** An edge line along one side. */
		edge?: 'top' | 'right' | 'bottom' | 'left';
		/** Its name for assistive tech. */
		label?: string;
		testid?: string;
		children: Snippet;
	}
	let { core, leaving = false, from, edge, label, testid = '', children }: Props = $props();

	let box: HTMLElement | undefined = $state();
	let fx = $state(0);
	let fy = $state(0);

	$effect(() => {
		if (!box || !core || !Number.isFinite(core.x) || !Number.isFinite(core.y)) return;
		// The outer box is never transformed, so this is the layout position, not
		// wherever the animation has the body this frame.
		const r = box.getBoundingClientRect();
		fx = core.x - (r.left + r.width / 2);
		fy = core.y - (r.top + r.height / 2);
	});

	const delay = $derived(animationOffset(leaving, from ?? (leaving ? 1 : 0)));
</script>

<section
	bind:this={box}
	class="projection"
	aria-label={label}
	data-testid={testid || undefined}
	data-leaving={leaving || undefined}
>
	<div
		class="body"
		class:leaving
		style:--fx="{fx}px"
		style:--fy="{fy}px"
		style:--turn={Math.sign(fx)}
		style:animation-delay="{delay}ms"
	>
		<span class="rim" aria-hidden="true"></span>
		{#if edge}<span class="edge {edge}" aria-hidden="true"></span>{/if}
		{@render children()}
	</div>
</section>

<style>
	.projection {
		position: relative;
	}

	.body {
		--persp: perspective(var(--jv-projection-perspective));
		/* Turned toward the core: its near edge recedes, like wings round it. */
		--ry: calc(var(--jv-projection-tilt) * var(--turn, 0));
		position: relative;
		isolation: isolate;
		padding: var(--jv-space-3) var(--jv-space-4);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
		font-size: var(--jv-fs-sm);
		text-shadow: var(--jv-glow-text);
		transform-origin: center;
		/* One curve of presence for both directions (reverse below): the stage's
		   reversal offset is only meaningful because exit mirrors entrance. */
		animation: project-in var(--jv-dur-appear) var(--jv-ease-appear) both;
	}

	.body.leaving {
		animation: project-out var(--jv-dur-exit) var(--jv-ease-appear) reverse both;
	}

	.body::before {
		content: '';
		position: absolute;
		inset: calc(-1 * var(--jv-projection-bleed));
		z-index: -1;
		background: var(--jv-projection-tint), var(--jv-projection-backing);
		pointer-events: none;
	}

	.body::after {
		content: '';
		position: absolute;
		top: 0;
		left: 0;
		width: var(--jv-projection-bracket);
		height: var(--jv-projection-bracket);
		border-top: 1px solid var(--jv-accent);
		border-left: 1px solid var(--jv-accent);
		box-shadow: var(--jv-glow-bracket);
		pointer-events: none;
	}

	/* A pack whose marker is the star (AURA, `data-marker` from theme.svelte.ts): a
	   rounded panel with a gradient edge, and a four-point star where the bracket was. */
	.rim {
		display: none;
	}
	:global(:root[data-marker='star']) .body::before {
		inset: 0;
		border-radius: var(--jv-radius-md);
		background: var(--jv-theme-panel-fill), var(--jv-projection-tint), var(--jv-projection-backing);
	}
	:global(:root[data-marker='star']) .rim {
		display: block;
		position: absolute;
		inset: 0;
		z-index: -1;
		padding: 1px;
		border-radius: var(--jv-radius-md);
		background: var(--jv-theme-accent-gradient);
		/* Only the 1px ring shows: the content box is masked out of the whole. */
		mask:
			linear-gradient(var(--jv-bg) 0 0) content-box exclude,
			linear-gradient(var(--jv-bg) 0 0);
		opacity: 0.45;
		pointer-events: none;
	}
	/* Inside the corner, not centred on it: a projection in a scroll box (the car's card) had
	   three quarters of it clipped away, and the quarter left read as a triangle (the owner,
	   2026-11-10). */
	:global(:root[data-marker='star']) .body::after {
		border: none;
		box-shadow: none;
		background: var(--jv-theme-accent-gradient);
		/* No drop-shadow: the clip-path is applied after a filter and cut the glow off whole. */
		clip-path: polygon(50% 0, 60% 40%, 100% 50%, 60% 60%, 50% 100%, 40% 60%, 0 50%, 40% 40%);
	}

	.edge {
		position: absolute;
		pointer-events: none;
		opacity: 0.6;
	}
	.edge.top,
	.edge.bottom {
		left: 0;
		right: 0;
		height: 1px;
		background: linear-gradient(90deg, transparent, var(--jv-accent), transparent);
	}
	.edge.top {
		top: 0;
	}
	.edge.bottom {
		bottom: 0;
	}
	.edge.left,
	.edge.right {
		top: 0;
		bottom: 0;
		width: 1px;
		background: linear-gradient(180deg, transparent, var(--jv-accent), transparent);
	}
	.edge.left {
		left: 0;
	}
	.edge.right {
		right: 0;
	}

	.body :global([data-ds-row]) {
		animation: row-in var(--jv-dur-row-in) var(--jv-ease-quick) both;
		animation-delay: min(calc(var(--i, 0) * var(--jv-stagger-project)), var(--jv-stagger-row-cap));
	}

	.body :global([data-ds-bar]) {
		transform-origin: bottom;
		animation: bar-up var(--jv-dur-bar) var(--jv-ease-quick) both;
	}

	/* Point at the core → travels to its place → a thin line → full height. */
	@keyframes project-in {
		0% { opacity: 0; transform: var(--persp) translate(var(--fx), var(--fy)) rotateY(0deg) scale(0.04); filter: brightness(3); }
		6% { opacity: 1; }
		26% { transform: var(--persp) translate(0, 0) rotateY(0deg) scale(0.04); filter: brightness(3); }
		46% { transform: var(--persp) translate(0, 0) rotateY(var(--ry)) scale(1, 0.025); filter: brightness(2.4); }
		100% { opacity: 1; transform: var(--persp) translate(0, 0) rotateY(var(--ry)) scale(1); filter: brightness(1); }
	}

	/* The same frames under another name, played in reverse: a new name is what
	   restarts a CSS animation when `leaving` flips. */
	@keyframes project-out {
		0% { opacity: 0; transform: var(--persp) translate(var(--fx), var(--fy)) rotateY(0deg) scale(0.04); filter: brightness(3); }
		6% { opacity: 1; }
		26% { transform: var(--persp) translate(0, 0) rotateY(0deg) scale(0.04); filter: brightness(3); }
		46% { transform: var(--persp) translate(0, 0) rotateY(var(--ry)) scale(1, 0.025); filter: brightness(2.4); }
		100% { opacity: 1; transform: var(--persp) translate(0, 0) rotateY(var(--ry)) scale(1); filter: brightness(1); }
	}

	@keyframes row-in {
		from { opacity: 0; transform: translateX(calc(-1 * var(--jv-space-3))); }
		to { opacity: 1; transform: none; }
	}

	@keyframes bar-up {
		from { transform: scaleY(0); }
		to { transform: scaleY(1); }
	}
</style>
