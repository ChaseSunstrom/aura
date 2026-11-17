<!--
@component
The desktop app's assist popup (`/?popup=1`, ADR-0051) as the island (T-0695): one
shape hanging from the top of the screen that is a pill while a turn runs out of the
way and a card while the person is here, something waits on them or a notice
arrives. The states are tests/contracts/island.json's, the phone's overlay's too
(`island.ts`); the size morphs on the glass spring and the one core moves and
scales between the pill's slot and the card's rather than one fading for another.

The card has the line (what is being heard while listening; otherwise a fresh
notice, the shell's last word, the reply, or the state, in that order) and the
input with the mic; an approval is its own card, answered there, so a hold is
never a trip to the full console. Esc or the core puts a live turn in its pill,
and so does going back to another window; the pill opens it again.

The window follows the island: the title says ` · pill` (`popupTitle`) and the app
shrinks the window round it once the morph has settled (shell.rs `popup_fit`).

Does not cover: blurring what is behind it (a window cannot reach the pixels of
the apps under it, so the focus veil only dims round the card), an ongoing
activity (the desktop holds none), or the console's screens, sheets and ⌘K, which
the desktop app opens the console for. Its line takes plain language, which the
assistant acts on (ask it to open a screen); the console's ranked rows are not here.

The app's typing key (default Ctrl+Shift+K, T-0696) raises this window and sends
`jarvis:summon` with `to: 'type'` (`popupMood.ts`); the card opens and the line is
focused. Esc puts the island away, and once it is `hidden` the title says ` · away` so
the app hides the window rather than leaving a clear one over the screen.
-->
<script lang="ts">
	import { tick } from 'svelte';
	import type { CoreState } from '../core/states';
	import Core from '../core/Core.svelte';
	import Starfield from '../Starfield.svelte';
	import { starfieldWorn } from '../theme.svelte';
	import ApprovalCard from './ApprovalCard.svelte';
	import type { Ask } from './approval';
	import { tokenPx, type TokenName } from '$lib/tokens';
	import { ISLAND, islandState, springEasing, type IslandState } from './island';
	import { SUMMON_EVENT, summonsToType } from '$lib/hud/popupMood';

	interface Props {
		name: string;
		look: CoreState;
		/** The reactor state, for the app's tests and the screen reader's line. */
		reactor: string;
		/** The primary persona's hue on the core, in its calm states. */
		tint?: TokenName;
		/** The state in a word, said when there is nothing else to say. */
		word: string;
		said: string;
		heard: string;
		/** The shell's last word ("Approved.", "Couldn’t answer that: …"). */
		status: string;
		recording: boolean;
		level?: number;
		envelope?: number;
		/** A notice that arrived while the popup was up, for a moment. */
		notice: string;
		ask?: Ask;
		more: number;
		/** The queue's `batchable` ids, for the card's "Approve all N". */
		batch?: readonly string[];
		onbatch?: (ids: readonly string[]) => void;
		busy: boolean;
		ready: boolean;
		draft: string;
		/** The island's state, for the title (the app sizes the window by it). */
		island?: IslandState;
		onanswer: (ask: Ask, choice: 'once' | 'session' | 'always' | 'deny', text?: string) => void;
		onsend: (e: SubmitEvent) => void;
		ontalk: () => void;
	}
	let { name, look, reactor, tint, word, said, heard, status, recording, level, envelope, notice, ask, more, batch = [], onbatch, busy, ready, draft = $bindable(), island = $bindable(), onanswer, onsend, ontalk }: Props = $props();

	const line = $derived(recording ? heard || 'Go ahead.' : notice || status || said || word);

	/** The person is in this window: the hotkey brought it up, or they clicked into it. */
	let here = $state(typeof document !== 'undefined' && document.hasFocus());
	/** Esc, the core, or another window: a live turn goes to its pill until something new arrives. */
	let collapsed = $state(false);
	$effect(() => {
		// Read for the effect: a new ask, notice or recording opens the island again.
		void [ask?.id, notice, recording];
		collapsed = false;
	});

	const shape = $derived(
		islandState({
			// The desktop's popup goes when idle: resting on a camera is the phone's (T-0890).
			on: false,
			// A reply stays the turn's until the app lets the idle popup go.
			turn: recording || !['idle', 'offline'].includes(reactor) || said !== '',
			ask: Boolean(ask),
			notice: notice !== '',
			expanded: here || draft.trim() !== '',
			collapsed
		})
	);
	$effect(() => {
		island = shape;
	});
	const card = $derived(shape === 'card' || shape === 'approval');

	// The card's height is what it shows, measured off its own layer (always laid out at the card's width).
	let cardHeight = $state(0);
	const size = $derived(
		card
			? { width: `min(var(${ISLAND.card.width}), 100vw - 2 * var(--jv-material-floating-halo))`, height: `${cardHeight}px` }
			: { width: `var(${ISLAND.pill.width})`, height: `var(${ISLAND.pill.height})` }
	);

	// Older webviews have no `linear()`: there the morph takes the standard ease rather than none at all.
	const spring = springEasing('glass');
	const shrink = tokenPx('--jv-material-floating-core-pill') / tokenPx('--jv-material-floating-core-card');
	const ease = typeof CSS !== 'undefined' && CSS.supports('transition-timing-function', spring.easing) ? spring.easing : 'var(--jv-ease-standard)';

	let input = $state<HTMLInputElement>();
	async function open() {
		collapsed = false;
		here = true;
		await tick();
		input?.focus();
	}
	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && !ask && card) collapsed = true;
	}
	// The app's typing key (T-0696): it raised this window; the line is where the person types.
	// A draft already there is kept, and an approval has no line to focus: it is the card.
	$effect(() => {
		const summoned = (e: Event) => {
			if (summonsToType(e)) void open();
		};
		window.addEventListener(SUMMON_EVENT, summoned);
		return () => window.removeEventListener(SUMMON_EVENT, summoned);
	});
</script>

<svelte:window onfocus={() => ((here = true), (collapsed = false))} onblur={() => ((here = false), (collapsed = true))} {onkeydown} />

<div class="veil" class:on={ISLAND[shape].focus} aria-hidden="true"></div>
<section
	class="island"
	aria-label={name}
	data-testid="popup"
	data-reactor={reactor}
	data-island={shape}
	style:inline-size={size.width}
	style:block-size={size.height}
	style:--morph-ms="{spring.ms}ms"
	style:--morph-ease={ease}
	style:--shrink={shrink}
>
	<!-- Under AURA the island is a window onto her space, not a pane of it: the desktop is
	     behind it, so it carries its own starfield (T-0898). -->
	{#if starfieldWorn()}<Starfield />{/if}
	<button
		type="button"
		class="core"
		class:small={!card}
		aria-label={card ? (ask ? name : 'Out of the way') : `Open ${name}`}
		disabled={Boolean(ask)}
		onclick={() => (card ? (collapsed = true) : open())}
	>
		<Core state={look} {tint} {level} {envelope} docked />
	</button>

	<button type="button" class="layer pill" tabindex={card ? -1 : 0} aria-hidden={card} onclick={open}>
		<span class="word">{recording ? 'Listening' : word}</span>
	</button>

	<div class="layer body" bind:offsetHeight={cardHeight} inert={!card} aria-hidden={!card}>
		<div class="head">
			{#if recording}
				<p class="line" role="status" data-testid="popup-listening"><span class="cap">Listening</span> {line}</p>
			{:else if notice}
				<p class="line" role="status" data-testid="popup-notice"><span class="cap">Notice</span> {line}</p>
			{:else}
				<p class="line" role="status" data-testid="popup-line">{line}</p>
			{/if}
		</div>
		{#if ask}
			<ApprovalCard {ask} {more} {batch} {onbatch} {busy} {onanswer} />
		{:else}
			<form class="composer" onsubmit={onsend}>
				<input bind:this={input} data-testid="chat-input" aria-label="Message {name}" placeholder="Ask {name} anything" autocomplete="off" bind:value={draft} />
				<button type="button" class="icon" aria-label={recording ? 'Stop listening' : 'Talk'} aria-pressed={recording} disabled={!ready} onclick={ontalk}>
					<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></svg>
				</button>
				<button type="submit" class="icon" aria-label="Send" data-testid="chat-send" disabled={!ready || !draft.trim()}>
					<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6" /></svg>
				</button>
			</form>
		{/if}
	</div>
</section>

<style>
	/* The app's window is clear: the page's own ground would draw a dark box round the island. */
	:global(html[data-popup]),
	:global(html[data-popup] body) {
		background: transparent;
	}
	/* Focus: a dim that falls off round the card, so it reads as in front without a box of its own. */
	.veil {
		position: fixed;
		inset: 0;
		background: radial-gradient(ellipse 70% 90% at 50% 0, var(--jv-material-floating-dim), transparent);
		opacity: 0;
		pointer-events: none;
		transition: opacity var(--jv-dur-fade) var(--jv-ease-standard);
	}
	.veil.on {
		opacity: 1;
	}
	.island {
		--pad: var(--jv-space-3);
		--slot: var(--jv-material-floating-pill-height);
		position: fixed;
		box-sizing: content-box;
		inset-block-start: var(--jv-material-floating-halo);
		inset-inline-start: 50%;
		translate: -50% 0;
		max-block-size: calc(100vh - 2 * var(--jv-material-floating-halo));
		overflow: hidden;
		border: 1px solid var(--jv-material-floating-edge);
		border-radius: var(--jv-material-floating-radius);
		background: var(--jv-material-floating-fill);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
		transform-origin: 50% 0;
		transition:
			inline-size var(--morph-ms) var(--morph-ease),
			block-size var(--morph-ms) var(--morph-ease);
		animation: float-in var(--jv-dur-float-in) var(--jv-ease-decelerate) both;
	}
	.island[data-island='hidden'] {
		opacity: 0;
		scale: 0.9;
		pointer-events: none;
		transition:
			opacity var(--jv-dur-fade) var(--jv-ease-standard),
			scale var(--jv-dur-fade) var(--jv-ease-standard);
	}
	/* A name per cue, so out then in restarts the arrival rather than holding the exit. */
	:global(html[data-float='in']) .island {
		animation: float-in-again var(--jv-dur-float-in) var(--jv-ease-decelerate) both;
	}
	:global(html[data-float='out']) .island {
		animation: float-out var(--jv-dur-float-out) var(--jv-ease-accelerate) both;
	}
	@keyframes float-in {
		from {
			opacity: 0;
			transform: translateY(calc(-1 * var(--jv-space-3))) scale(0.97);
		}
	}
	@keyframes float-in-again {
		from {
			opacity: 0;
			transform: translateY(calc(-1 * var(--jv-space-3))) scale(0.97);
		}
	}
	@keyframes float-out {
		to {
			opacity: 0;
			transform: translateY(calc(-1 * var(--jv-space-3))) scale(0.97);
		}
	}
	/* One core for both: the card's slot at its size, scaled into the pill's slot on the same spring. */
	.core {
		position: absolute;
		z-index: 1;
		inset-block-start: var(--pad);
		inset-inline-start: var(--pad);
		inline-size: var(--jv-material-floating-core-card);
		aspect-ratio: 1;
		padding: 0;
		border: 0;
		border-radius: var(--jv-radius-pill);
		background: transparent;
		cursor: pointer;
		transform-origin: 0 0;
		transition: transform var(--morph-ms) var(--morph-ease);
	}
	.core.small {
		transform: translate(
				calc((var(--slot) - var(--jv-material-floating-core-pill)) / 2 - var(--pad)),
				calc((var(--slot) - var(--jv-material-floating-core-pill)) / 2 - var(--pad))
			)
			scale(var(--shrink));
	}
	.core:disabled {
		cursor: default;
	}
	.layer {
		position: absolute;
		inset-block-start: 0;
		inset-inline-start: 0;
		transition: opacity var(--jv-dur-fade) var(--jv-ease-standard);
	}
	.pill {
		display: flex;
		align-items: center;
		inline-size: var(--jv-material-floating-pill-width);
		block-size: var(--slot);
		padding: 0 var(--jv-space-4) 0 var(--slot);
		border: 0;
		background: transparent;
		color: var(--jv-text-bright);
		cursor: pointer;
	}
	.word {
		overflow: hidden;
		font: var(--jv-fs-2xs) var(--jv-font-chrome);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.body {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-3);
		inline-size: min(var(--jv-material-floating-card-width), 100vw - 2 * var(--jv-material-floating-halo));
		max-block-size: calc(100vh - 2 * var(--jv-material-floating-halo));
		padding: var(--pad);
	}
	/* Only the shown state's layer is seen: the other fades under the morph. */
	.island[data-island='card'] .pill,
	.island[data-island='approval'] .pill,
	.island:not([data-island='card'], [data-island='approval']) .body {
		opacity: 0;
		pointer-events: none;
	}
	/* A long reply scrolls in its row, so the card and the input stay where they are (2026-10-08: "cut off"). */
	.head {
		flex: 0 1 auto;
		display: flex;
		align-items: safe center;
		min-block-size: var(--jv-material-floating-core-card);
		padding-inline-start: calc(var(--jv-material-floating-core-card) + var(--jv-space-3));
		overflow-y: auto;
	}
	.line {
		flex: 1;
		margin: 0;
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-body);
		overflow-wrap: anywhere;
	}
	.cap {
		color: var(--jv-accent);
		font: var(--jv-fs-2xs) var(--jv-font-chrome);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
	}
	.composer {
		flex: none;
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
	}
	input {
		flex: 1;
		min-inline-size: 0;
		min-block-size: var(--jv-touch);
		padding: 0 var(--jv-space-4);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-surface-sunken);
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-body);
	}
	input::placeholder {
		color: var(--jv-text-faint);
	}
	input:focus-visible,
	.icon:focus-visible,
	.core:focus-visible,
	.pill:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.icon {
		display: inline-grid;
		place-items: center;
		flex: none;
		inline-size: var(--jv-touch);
		block-size: var(--jv-touch);
		padding: 0;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
		background: transparent;
		color: var(--jv-accent);
		cursor: pointer;
	}
	.icon:hover:not(:disabled) {
		background: var(--jv-wash);
	}
	.icon:disabled {
		color: var(--jv-text-faint);
		cursor: default;
	}
	.icon[aria-pressed='true'] {
		background: var(--jv-wash-strong);
		color: var(--jv-text-bright);
	}
	.icon svg {
		inline-size: 50%;
		block-size: 50%;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.6;
		stroke-linecap: round;
	}
	@media (prefers-reduced-motion: reduce) {
		.island,
		.core,
		.layer,
		.veil,
		:global(html[data-float]) .island {
			transition-duration: var(--jv-dur-instant);
			animation-duration: var(--jv-dur-instant);
		}
	}
</style>
