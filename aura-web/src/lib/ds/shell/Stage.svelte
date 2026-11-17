<!--
@component
The core screen's stage. The core sits where the scene puts it; the scene's
projections stand on the board's slots, coming out of the core and going back
into it; while working the core rides in the presence strip and connectors
pulse from it to the work; while an answer is waited on the card stands beside
the core on a leader line. Subtitles run under it all.

It draws one stage view (`stage.ts`) and never ticks it: the shell does, so a
scene change is one decision in one place.

Does not cover: which scene (Shell, `scene.ts`), what is projected
(`projections.ts`) or the card's contents (the `card` snippet). Under 721 px
the slots stack in a column under the core and the lines are not drawn: a line
across a phone points at nothing it can keep in view.
-->
<script lang="ts">
	import { tick, type Snippet } from 'svelte';

	import { draggable, glide } from '../drag';
	import { Connector, Conversation, Core, Figure, Label, LeaderLine, PresenceStrip, Projected, Projection, type CoreState, type Point, type StageView } from '$lib/ds';
	import type { ChatTurn } from '$lib/glass';
	import { TOKENS, type TokenName } from '$lib/tokens';
	import { MS, prefersReducedMotion } from '../motion';
	import { regrowFrom } from '../stage';
	import { CORE_LIGHT, edgePoint, onStage, relativeTo } from '../lines';
	import { SLOTS, type Shown } from './projections';
	import Steps from '../activity/Steps.svelte';

	interface Strip {
		task: string;
		steps: { label: string }[];
		current: number;
	}
	interface Props {
		view: StageView;
		/** The core's look: the scene's, or a louder one the link asks for (offline, warming). */
		look: CoreState;
		/** The primary persona's hue on the core, in its calm states. */
		tint?: TokenName;
		shown: readonly Shown[];
		strip?: Strip | null;
		/** The waiting request, drawn in the alert scene. */
		card?: Snippet;
		/** The session's turns, drawn in the conversation panel (T-0728). */
		turns?: readonly ChatTurn[];
		/** A turn is running: the panel types until JARVIS's first word. */
		running?: boolean;
		/** What the microphone is hearing, not yet sent. */
		heard?: string;
		/** Who answers, as its turns are labelled. */
		persona?: string;
		/** The shell's status line, drawn in the same column as the words so it cannot land on them (T-0703). */
		notice?: string;
		level?: number;
		envelope?: number;
		music?: number;
		ontakeover?: () => void;
		/** Stops the run from the steps projection (the Shell's stopTurn). */
		onstop?: () => void;
		/** A screen is open beside the stage (the shell's side panel): on a phone the stage keeps only the core. */
		aside?: boolean;
		/** A panel dropped on a tile (T-0951): the shell swaps it in and keeps the arrangement. */
		onretile?: (id: string, tile: number) => void;
	}
	// `look`, not `state`: a prop called state turns every `$state` below into a store read.
	let { view: v, look, tint, shown, strip = null, card, turns = [], running = false, heard = '', persona, notice = '', level, envelope, music, ontakeover, onstop, aside = false, onretile }: Props = $props();

	let stage: HTMLElement | undefined = $state();
	let cardBox: HTMLElement | undefined = $state();
	let box = $state<{ left: number; top: number; width: number; height: number } | null>(null);
	/** Where each projection's line lands, in percent of the stage. */
	let ends = $state<Record<string, Point>>({});
	let cardEnd = $state<Point | null>(null);
	/** Where the leader leaves the core: the edge of its light, not a fixed share of the stage. */
	let leaderFrom = $state<Point>([59, 50]);
	let coreEl: HTMLElement | undefined = $state();
	let beats = $state(0);
	let narrow = $state(false);
	// Not state: written by bind:this, read only by measure().
	const slots: Record<string, HTMLElement | null> = {};
	// The core's width as last laid out, and the regrow under way (regrow below).
	let coreW = 0;
	let regrowing: { run: Animation; from: number; to: number } | null = null;

	const corePx = $derived(box ? { x: box.left + box.width / 2, y: box.top + v.core.y * box.height } : undefined);
	const coreAt = $derived<Point>([50, v.core.y * 100]);

	function measure(): void {
		if (!stage) return;
		const r = stage.getBoundingClientRect();
		box = { left: r.left, top: r.top, width: r.width, height: r.height };
		narrow = r.width <= 720;
		if (!r.width || !r.height) return;
		// Slots and the card are never scaled (only a projection's inner body is), so their
		// client rects are their layout boxes. What left a line behind was nothing re-measuring
		// when a slot or the card grew with the stage the same size, and a line to a target
		// outside the stage (see the effect below and `onStage`).
		const w = r.width;
		const h = r.height;
		const pct = (p: Point): Point => [(p[0] / w) * 100, (p[1] / h) * 100];
		/** The edge point a line lands on, on the stage, or null when it is off it. */
		const landing = (el: HTMLElement, edge: 'left' | 'right' | 'top'): Point | null => {
			const b = relativeTo(el.getBoundingClientRect(), r);
			return onStage(b, w, h, edge) ? pct(edgePoint(b, edge)) : null;
		};
		const next: Record<string, Point> = {};
		for (const s of shown) {
			const p = slots[s.id] && landing(slots[s.id]!, s.edge);
			if (p) next[s.id] = p;
		}
		ends = next;
		cardEnd = cardBox ? landing(cardBox, 'left') : null;
		if (coreEl) leaderFrom = [50 + ((coreEl.offsetWidth * v.core.scale * CORE_LIGHT) / w) * 100, v.core.y * 100];
		if (coreEl) regrow(coreEl);
	}

	/**
	 * A screen opening or closing on a phone gives the core a new box: it shrinks or grows into
	 * it while the shell glides it there, instead of jumping to its new size on the first frame
	 * (the owner, 2026-11-10, T-0974). Measured after layout (the resize observer), so the old
	 * width is the last one seen; the phone turning or its keyboard opening regrows it the same
	 * way. Not covered: wider windows, whose core keeps its size beside a panel more often.
	 */
	function regrow(el: HTMLElement): void {
		const now = el.offsetWidth;
		if (!now || now === coreW) return;
		if (coreW && narrow && !prefersReducedMotion()) {
			// From the size it is drawn at: opening a screen through the apps sheet resizes it
			// three times in a moment (the sheet in, the sheet out, the panel in), and each regrow
			// restarted from the last laid-out size read as a bounce.
			const p = regrowing?.run.effect?.getComputedTiming().progress;
			const drawn = regrowing && p != null && p < 1 ? regrowing.from + (regrowing.to - regrowing.from) * p : coreW;
			regrowing?.run.cancel();
			regrowing = null;
			// Already drawn at its new size (a regrow overtaken at its start): nothing to glide.
			if (Math.abs(drawn - now) >= 1) {
				const shrinking = now < drawn;
				const run = el.animate([regrowFrom(drawn / now, now, el.offsetHeight), { translate: '0px 0px', scale: '1' }], {
					duration: shrinking ? MS.appear : MS.exit,
					easing: TOKENS[shrinking ? '--jv-ease-appear' : '--jv-ease-exit']
				});
				regrowing = { run, from: drawn, to: now };
			}
		}
		coreW = now;
	}

	// The tile a held panel will take, drawn as a ghost under it (T-0951), in stage px.
	let ghost = $state<{ x: number; y: number; w: number; h: number } | null>(null);

	/** Where the layout puts a panel's centre on the page: its box less the offset it is held at. */
	function laidAt(el: HTMLElement): Point {
		const b = el.getBoundingClientRect();
		const [tx, ty] = el.style.translate.split(' ').map(parseFloat);
		return [b.left + b.width / 2 - (tx || 0), b.top + b.height / 2 - (ty || 0)];
	}

	/**
	 * The tile nearest a point on the page, and its centre: where its panel stands, or the board's
	 * slot when it is empty. A phone's column has no empty places, only its panels.
	 */
	function nearest(x: number, y: number): { tile: number; at: Point } | null {
		if (!stage) return null;
		const r = stage.getBoundingClientRect();
		const held = new Map(shown.flatMap((s) => (slots[s.id] ? [[s.tile, laidAt(slots[s.id]!)] as const] : [])));
		const empty = narrow ? [] : SLOTS[v.scene].flatMap((p, tile): [number, Point][] => (held.has(tile) ? [] : [[tile, [r.left + (p[0] / 100) * r.width, r.top + (p[1] / 100) * r.height]]]));
		let best: { tile: number; at: Point } | null = null;
		for (const [tile, at] of [...held, ...empty]) {
			if (!best || Math.hypot(at[0] - x, at[1] - y) < Math.hypot(best.at[0] - x, best.at[1] - y)) best = { tile, at };
		}
		return best;
	}

	function held(id: string, x: number, y: number): void {
		measure();
		const el = slots[id];
		const to = nearest(x, y);
		if (!el || !to || !stage) return;
		const r = stage.getBoundingClientRect();
		const [w, h] = [el.offsetWidth, el.offsetHeight];
		ghost = { x: to.at[0] - r.left - w / 2, y: to.at[1] - r.top - h / 2, w, h };
	}

	// A scene that changes under a held panel takes the panel away: its ghost goes with it.
	$effect(() => {
		void v.scene;
		ghost = null;
	});

	// Every panel's box as it is seen now, then the new arrangement, then each glides from the
	// one to the other: the dropped panel from where it was let go, the one it displaced from its tile.
	function dropped(id: string, x: number, y: number): void {
		ghost = null;
		const to = nearest(x, y);
		const before = new Map(shown.flatMap((s) => (slots[s.id] ? [[slots[s.id]!, slots[s.id]!.getBoundingClientRect()] as const] : [])));
		const from = shown.find((s) => s.id === id)?.tile;
		if (to && to.tile !== from) onretile?.(id, to.tile);
		void tick().then(() => glide(before, measure));
	}

	// Anything that can change a box while the stage keeps its size: a slot or the card
	// resizing (content, a chart), fonts arriving, the phone turning.
	$effect(() => {
		if (!stage) return;
		const watch = new ResizeObserver(() => measure());
		watch.observe(stage);
		for (const s of shown) if (slots[s.id]) watch.observe(slots[s.id]!);
		if (cardBox) watch.observe(cardBox);
		void document.fonts?.ready.then(measure);
		window.addEventListener('orientationchange', measure);
		return () => {
			watch.disconnect();
			window.removeEventListener('orientationchange', measure);
		};
	});
	// Effects run after the DOM is patched: the new slots are there to measure.
	$effect(() => {
		void shown;
		void cardBox;
		void v.scene;
		void v.core.scale;
		measure();
	});
</script>

<div class="stage" bind:this={stage} data-testid="stage" data-narrow={narrow || undefined} data-aside={aside || undefined}>
	<div class="lane" style:--y={v.core.y}>
		<div class="core" style:--s={v.core.scale} bind:this={coreEl}>
			<Core state={look} {tint} {level} {envelope} {music} onbeat={(n) => (beats = n)} />
		</div>
	</div>

	{#if v.scene === 'working' && strip}
		<div class="strip">
			<PresenceStrip task={strip.task} steps={strip.steps} current={strip.current} leaving={v.leaving} from={v.from} {ontakeover} testid="presence-strip" />
		</div>
	{/if}

	{#if !narrow}
		{#if v.scene === 'working'}
			{#each shown as s (s.id)}
				{#if ends[s.id]}
					<Connector start={coreAt} end={ends[s.id]} target={s.id} beat={beats} leaving={v.leaving} from={v.from} testid="stage-connector" />
				{/if}
			{/each}
		{/if}
		{#if v.scene === 'alert' && card && cardEnd}
			<LeaderLine points={[leaderFrom, cardEnd]} waiting leaving={v.leaving} from={v.from} testid="stage-leader" />
		{/if}
	{/if}

	{#if ghost}
		<div class="ghost" data-testid="tile-ghost" aria-hidden="true" style:left="{ghost.x}px" style:top="{ghost.y}px" style:inline-size="{ghost.w}px" style:block-size="{ghost.h}px"></div>
	{/if}

	<!-- One column on a phone: what is projected, then the conversation under it (T-0728). -->
	<div class="below" data-scene={v.scene}>
	<div class="slots" class:working={v.scene === 'working'} class:drawn={shown.some((s) => s.visual)}>
		{#each shown as s (s.id)}
			<div class="slot" use:draggable={{ moved: (x, y) => held(s.id, x, y), dropped: (x, y) => dropped(s.id, x, y) }} data-shown={s.id} data-visual={s.visual ? '' : undefined} data-edge={s.edge} style:--x="{s.at[0]}%" style:--y="{s.at[1]}%" bind:this={slots[s.id]}>
				{#if s.visual}
					<Projected type={s.visual.type} data={s.visual.data} placement={{ core: corePx, edge: s.edge, leaving: v.leaving, from: v.from }} testid="stage-visual" />
				{:else}
					<Projection core={corePx} leaving={v.leaving} from={v.from} edge={s.edge} label={s.label} testid="stage-projection">
						<Label tone={s.tone}>{s.label}</Label>
						{#if s.value !== undefined}<Figure value={s.value} unit={s.unit} size="lg" />{/if}
						{#each s.rows as row, i (i)}
							<!-- data-ds-row: the projection slides its rows in 35 ms apart, capped (Projection.svelte). -->
							{#if row}<p class="row" data-ds-row style:--i={i}>{row}</p>{/if}
						{/each}
						{#if s.steps}<Steps steps={s.steps} {onstop} />{/if}
					</Projection>
				{/if}
			</div>
		{/each}
	</div>

	{#if v.scene === 'alert' && card}
		<div class="card" bind:this={cardBox}>
			<Projection core={corePx} leaving={v.leaving} from={v.from} edge="left">
				{@render card()}
			</Projection>
		</div>
	{/if}

	<div class="words">
		<div class="talk"><Conversation {turns} {running} {heard} {persona} /></div>
		<!-- The shell's role=status element announces it; this is the copy the eye reads. -->
		{#if notice}<p class="notice" aria-hidden="true" data-testid="stage-notice">{notice}</p>{/if}
	</div>
	</div>
</div>

<style>
	.stage {
		/* The core's box at rest; the card is placed against it. Sizes are of the
		   window, or of the stage's own box while a screen is open beside it
		   (--u-w/--u-h, the shell's side panel, T-0725). */
		--core-box: min(var(--jv-core-width), calc(46 * var(--u-w, 1vw)), calc(62 * var(--u-h, 1vh)));
		position: relative;
		flex: 1;
		min-block-size: 0;
		overflow: hidden;
	}
	/* The lane is the stage's height, so a translate of its own 100% is the
	   stage's height: the core travels on transform alone. */
	.lane {
		position: absolute;
		inset: 0;
		transform: translateY(calc(var(--y) * 100%));
		pointer-events: none;
	}
	.core {
		position: absolute;
		top: 0;
		left: 50%;
		inline-size: var(--core-box);
		transform: translate(-50%, -50%) scale(var(--s));
	}
	.strip {
		position: absolute;
		top: 17%;
		left: calc(50% + var(--jv-space-7) * 2);
		transform: translateY(-50%);
		inline-size: min(var(--jv-strip-width), calc(40 * var(--u-w, 1vw)));
		z-index: 2;
	}
	.slot {
		position: absolute;
		left: var(--x);
		top: var(--y);
		inline-size: max-content;
		max-inline-size: min(calc(30 * var(--u-w, 1vw)), var(--jv-strip-width));
		min-inline-size: var(--jv-measure-card);
		transform: translate(-50%, -50%);
		z-index: 1;
	}
	/* In thinking the steps stand against the core's light, as the card does: centred on a
	   slot, a long list reached into the core at 1024 (T-0707). On whichever side its tile is. */
	@media (min-width: 721px) {
		.slots:not(.working) .slot[data-shown='steps'] {
			--clear: calc(var(--core-box) * 0.32);
			left: calc(50% + var(--clear));
			max-inline-size: min(var(--jv-strip-width), calc(50% - var(--clear) - var(--jv-space-5)));
			transform: translateY(-50%);
		}
		.slots:not(.working) .slot[data-shown='steps'][data-edge='right'] {
			left: auto;
			right: calc(50% + var(--clear));
		}
	}
	/* The panel in the hand rides over the others, and the ghost under it marks its tile (T-0951). */
	/* :global because the draggable action sets the attribute at run time: the compiler pruned
	   the plain selector as unused, and the dragged panel never rose (T-1193). */
	.slot:global([data-dragging]) {
		z-index: 4;
		cursor: grabbing;
	}
	.ghost {
		position: absolute;
		z-index: 1;
		pointer-events: none;
		border: 1px solid var(--jv-accent-deep);
		border-radius: var(--jv-radius-md);
		background: var(--jv-wash);
		transition:
			left var(--jv-dur-row-in) var(--jv-ease-quick),
			top var(--jv-dur-row-in) var(--jv-ease-quick);
	}
	/* A chart is drawn to its slot's width, not its content's: the side of the core it faces, out
	   to the core's light, so a 24-hour line reads at 1024 and never reaches into the core (T-0698). */
	@media (min-width: 721px) {
		.slot[data-visual] {
			inline-size: min(var(--jv-strip-width), calc(34 * var(--u-w, 1vw)));
			max-inline-size: none;
		}
		.slots:not(.working) .slot[data-visual] {
			--clear: calc(var(--core-box) * 0.32);
			inline-size: min(var(--jv-strip-width), calc(50% - var(--clear) - var(--jv-space-5)));
			transform: translateY(-50%);
		}
		.slots:not(.working) .slot[data-visual][data-edge='right'] {
			left: auto;
			right: calc(50% + var(--clear));
		}
		.slots:not(.working) .slot[data-visual][data-edge='left'] {
			left: calc(50% + var(--clear));
		}
	}
	.row {
		margin: var(--jv-space-1) 0 0;
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	/* Starts where the core's light ends: at the alert scale (0.82, stage.ts) its
	   particles reach about 0.3 of its box from the centre. Centred at 72%, the card's
	   left edge sat over them at 1440 (an amber particle under "Next time" read 1.9:1,
	   a11y-contrast, now and then) and covered half the core at 768. */
	.card {
		--clear: calc(var(--core-box) * 0.32);
		position: absolute;
		top: 44%;
		left: calc(50% + var(--clear));
		inline-size: min(var(--jv-measure-dialog), calc(50% - var(--clear) - var(--jv-space-5)));
		transform: translateY(-50%);
		z-index: 3;
	}
	/* The column a phone stacks; elsewhere its children stand on the stage itself. */
	.below {
		display: contents;
	}
	/* Beside a screen on a phone the stage is a strip over the panel: the core alone fits it. */
	.stage[data-narrow][data-aside] .below {
		display: none;
	}
	/* The conversation: across the bottom under the core, bottom-left beside it on a desktop or
	   any landscape screen (T-0728; the owner: "in a different spot to match the other UI
	   stuff"). Across the bottom at 1024x768 it covered the core's lower third. */
	.words {
		position: absolute;
		inset: auto 0 var(--jv-space-4);
		display: grid;
		justify-items: center;
		gap: var(--jv-space-1);
		padding: 0 var(--jv-space-4);
		pointer-events: none;
	}
	.talk {
		display: flex;
		flex-direction: column;
		min-block-size: 0;
		inline-size: min(var(--jv-measure-dialog), 100%);
		max-block-size: calc(34 * var(--u-h, 1vh));
		pointer-events: auto;
	}
	@media (min-width: 1100px), (min-width: 721px) and (orientation: landscape) {
		.words {
			inset: auto auto var(--jv-space-4) var(--jv-space-5);
			/* Clear of the core's light, as the slots are (0.4 of its box is the sphere's edge). */
			inline-size: min(var(--jv-measure-dialog), calc(30 * var(--u-w, 1vw)), calc(50% - var(--core-box) * 0.4 - 2 * var(--jv-space-5)));
			justify-items: stretch;
			padding: 0;
		}
		.talk {
			inline-size: auto;
			max-block-size: calc(46 * var(--u-h, 1vh));
		}
	}
	/* A portrait tablet: across the bottom, only as tall as the room under the core's light (it
	   sits at 44% of the stage, up to 1.06 of its box while listening); at 34vh the panel
	   reached into the sphere at 768x1024. */
	@media (min-width: 721px) and (max-width: 1099px) and (orientation: portrait) {
		.words {
			display: flex;
			flex-direction: column;
			align-items: center;
			max-block-size: calc(56% - var(--core-box) * 0.42 - var(--jv-space-4));
		}
	}
	.notice {
		margin: 0;
		max-width: var(--jv-strip-width);
		color: var(--jv-text-bright);
		font: var(--jv-fs-sm) var(--jv-font-chrome);
		text-align: center;
	}

	/* A phone: the core on top, then the strip, then everything in one column. */
	@media (max-width: 720px) {
		.core {
			inline-size: min(var(--jv-core-width), calc(62 * var(--u-w, 1vw)), calc(var(--core-share, 40) * var(--u-h, 1vh)));
		}
		.strip {
			top: calc(17% + var(--jv-space-7));
			left: var(--jv-space-3);
			right: var(--jv-space-3);
			inline-size: auto;
			transform: none;
		}
		/* Under the core, one column: what is projected first, then the conversation, which
		   keeps its newest lines and scrolls when a projection needs the room (T-0728). */
		.below {
			position: absolute;
			inset: 60% var(--jv-space-3) var(--jv-space-3);
			display: flex;
			flex-direction: column;
			gap: var(--jv-space-3);
		}
		.below[data-scene='working'] {
			inset-block-start: 40%;
		}
		/* A request stands on the bottom edge and grows up as far as it needs: from 60% the card
		   had 270px of an 844px phone and scrolled inside it ("an approval fits one phone screen
		   without scrolling", T-0694). A tall one reaches over the core's foot; its backing is dark. */
		.below:has(> .card) {
			inset-block-start: auto;
			max-block-size: calc(100% - var(--jv-space-7) * 2);
			z-index: 3;
		}
		/* A chart under the steps needs about 270px: from 66% the stack had 30% and clipped the
		   chart's axis at 390x844 (T-0698). It starts just under the core instead. */
		.below:has(.slots.drawn):not([data-scene='working']) {
			inset-block-start: 56%;
		}
		.slots,
		.card {
			position: static;
			display: grid;
			gap: var(--jv-space-3);
			flex: 0 1 auto;
			min-block-size: 0;
			overflow-y: auto;
			transform: none;
			inline-size: auto;
		}
		.slots:empty {
			display: none;
		}
		/* A panel in the hand leaves the column: clipped to it, it vanished as it left (T-0887).
		   The column stops scrolling while one is out. */
		.slots:has(> :global([data-dragging])) {
			overflow: visible;
		}
		/* The conversation gives up room first and scrolls (it never draws past its own box);
		   only then does a projection scroll. A 34% cap clipped the weather chart at 390x844 by
		   56px. */
		.words {
			position: static;
			display: flex;
			flex-direction: column;
			flex: 0 1000 auto;
			min-block-size: 0;
			margin-block-start: auto;
			padding: 0;
		}
		/* Nothing said yet and no notice: the column is the projections'. */
		.words:not(:has(.conversation, .notice)) {
			display: none;
		}
		.talk {
			inline-size: 100%;
			max-block-size: 100%;
		}
		.slot {
			position: static;
			transform: none;
			max-inline-size: none;
			inline-size: auto;
		}
	}
</style>
