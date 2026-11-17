<!--
@component
The conversation beside the core (T-0728): the session's last turns as a projection, each
labelled (YOU dim, JARVIS bright and in Markdown), the newest followed unless the reader has
scrolled back. A turn that is running with no words yet types; a streaming reply is drawn to
its last whole word with its open emphasis closed, and only the words that just arrived fade
and rise in, so nothing already read moves or flickers. The owner, 2026-11-08: the reply was
"one centered wall" under the core and should be "smoother, animated, and in a different spot
to match the other UI stuff".

```svelte
<Conversation turns={turns} running={turn.running} heard={voice.transcript} />
```

It is a polite live region on the newest turn only, so a screen reader hears each finished
change once. Under reduced motion words appear without moving.

Does not cover: where it stands (the stage places it), the full history and its tools (the
Chat screen), or timing words to the voice.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { untrack } from 'svelte';

	import type { ChatTurn } from '$lib/glass';
	import { renderMarkdown } from '$lib/markdown';
	import Label from './Label.svelte';
	import Projection from './Projection.svelte';
	import { closeOpen, revealFrom, typing, visibleTurns, wholeWords } from './conversation';
	import { MS } from './motion';

	interface Props {
		turns: readonly ChatTurn[];
		/** A turn is running (the send is a stop). */
		running?: boolean;
		/** What the microphone is hearing, not yet sent. */
		heard?: string;
		/** Who answers, as its turns are labelled. */
		persona?: string;
		testid?: string;
	}
	let { turns, running = false, heard = '', persona = `${wornName()}`, testid = 'conversation' }: Props = $props();

	const shown = $derived(visibleTurns(turns, heard));
	const waiting = $derived(typing(shown, running));
	/** The reply still arriving, wherever a heard line has put it. */
	const live = $derived(shown.findLast((t) => t.role === 'jarvis' && t.streaming));
	/** The newest turn of each side, which the tests and the contrast audit read. */
	const newest = $derived({ you: shown.findLastIndex((t) => t.role === 'you'), jarvis: shown.findLastIndex((t) => t.role === 'jarvis') });
	/** The newest JARVIS turn while it streams: drawn to its last whole word, its marks closed. */
	const liveHtml = $derived(live ? renderMarkdown(closeOpen(wholeWords(live.text))) : '');

	let history: HTMLElement | undefined = $state();
	/** The history's height: a projection that takes the room under a phone's core shrinks it
	    after the last word, and the newest lines must still be what shows (T-0728). */
	let tall = $state(0);
	let liveBox: HTMLElement | undefined = $state();
	/** Following the newest turn: false once the reader scrolls back, true again at the bottom. */
	let follow = true;
	let revealId = '';
	/** Per word of the streaming reply, when its fade began (see splitWords). */
	let born: number[] = [];

	function onscroll() {
		if (!history) return;
		follow = history.scrollHeight - history.scrollTop - history.clientHeight < 8;
	}

	// After each streamed delta: wrap only the newly arrived words, then follow the newest line.
	$effect(() => {
		const html = liveHtml;
		const id = live?.id ?? '';
		untrack(() => {
			if (liveBox && html) {
				if (id !== revealId) {
					revealId = id;
					born = [];
				}
				revealFrom(liveBox, born, performance.now(), MS.rowStagger, MS.fade);
			}
		});
	});
	$effect(() => {
		void shown.length;
		void liveHtml;
		void waiting;
		void tall;
		if (history && untrack(() => follow)) history.scrollTop = history.scrollHeight;
	});
</script>

{#if shown.length || waiting}
	<div class="conversation" data-testid={testid}>
		<Projection edge="left" label="Conversation">
			<header class="head">
				<Label>Conversation</Label>
				<span class="count" aria-hidden="true">{String(shown.filter((t) => t.role === 'you').length).padStart(2, '0')}</span>
			</header>
			<div class="history" bind:this={history} bind:clientHeight={tall} {onscroll} tabindex="-1">
				{#each shown as turn, i (turn.id)}
					{@const last = i === shown.length - 1}
					{@const id = newest[turn.role] === i ? `conversation-${turn.role}` : undefined}
					<article class="turn" data-turn={turn.role} aria-live={last ? 'polite' : undefined}>
						<span class="who" data-who>{turn.role === 'you' ? 'You' : (turn.who ?? persona)}</span>
						{#if turn.role === 'you'}
							<p class="words" data-testid={id}>{turn.text}</p>
						{:else if turn.id === live?.id}
							<!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes the text before it draws its subset; see $lib/markdown. -->
							<div class="words md" data-testid={id} bind:this={liveBox}>{@html liveHtml}</div>
						{:else}
							<!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes the text before it draws its subset; see $lib/markdown. -->
							<div class="words md" data-testid={id}>{@html renderMarkdown(turn.text)}</div>
						{/if}
					</article>
				{/each}
				{#if waiting}
					<article class="turn" data-turn="jarvis" data-testid="conversation-typing" aria-label="{persona} is answering">
						<span class="who">{persona}</span>
						<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
					</article>
				{/if}
			</div>
		</Projection>
	</div>
{/if}

<style>
	.conversation {
		display: flex;
		flex-direction: column;
		min-block-size: 0;
		max-block-size: 100%;
		/* Never drawn past its own box (with room for the backing's bleed): on a short stage at
		   720x300 the history spilled over the status line under it; it scrolls instead. */
		overflow: clip;
		overflow-clip-margin: var(--jv-projection-bleed);
	}
	.conversation :global(.projection),
	.conversation :global(.projection > .body) {
		display: flex;
		flex-direction: column;
		flex: 1 1 auto;
		min-block-size: 0;
		max-block-size: 100%;
	}
	.head {
		flex: none;
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--jv-space-3);
		padding-block-end: var(--jv-space-2);
		border-block-end: 1px solid var(--jv-line-hair);
	}
	.count {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		color: var(--jv-accent);
		font-variant-numeric: tabular-nums;
	}
	.history {
		flex: 1 1 auto;
		display: grid;
		align-content: start;
		gap: var(--jv-space-3);
		min-block-size: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		scrollbar-width: none;
		padding-block: var(--jv-space-3) var(--jv-space-1);
		/* Older turns fade out at the top edge instead of being cut. */
		mask-image: linear-gradient(to bottom, transparent, black var(--jv-space-4));
	}
	.history:focus {
		outline: none;
	}
	.turn {
		display: grid;
		gap: var(--jv-space-1);
		animation: turn-in var(--jv-dur-row-in) var(--jv-ease-quick) both;
	}
	.who {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		color: var(--jv-text-faint);
	}
	[data-turn='jarvis'] > .who {
		color: var(--jv-accent);
	}
	.words {
		margin: 0;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		line-height: 1.45;
		overflow-wrap: anywhere;
	}
	[data-turn='you'] > .words {
		font-size: var(--jv-fs-md);
		color: var(--jv-text-dim);
	}
	[data-turn='jarvis'] > .words {
		font-size: var(--jv-fs-lg);
		color: var(--jv-text);
		text-shadow: var(--jv-glow-subtitle);
	}
	.md :global(p) {
		margin: 0 0 var(--jv-space-2);
	}
	.md :global(:last-child) {
		margin-block-end: 0;
	}
	.md :global(ul),
	.md :global(ol),
	.md :global(pre),
	.md :global(table),
	.md :global(blockquote) {
		margin: 0 0 var(--jv-space-2);
	}
	.md :global(ul),
	.md :global(ol) {
		padding-inline-start: var(--jv-space-4);
	}
	.md :global(li::marker) {
		color: var(--jv-accent);
	}
	.md :global(strong) {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text-bright);
	}
	.md :global(code) {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-bright);
	}
	.md :global(pre) {
		padding: var(--jv-space-2);
		background: var(--jv-surface-sunken);
		overflow-x: auto;
	}
	.md :global(a) {
		color: var(--jv-accent);
	}
	.md :global(th),
	.md :global(td) {
		padding: 0 var(--jv-space-2) 0 0;
		text-align: start;
	}
	/* A word that just arrived fades and rises in, the next ones a step behind it. */
	.md :global([data-reveal]) {
		display: inline-block;
		/* The delay is set per word: when it arrived, kept across redraws (splitWords). */
		animation: word-in var(--jv-dur-fade) var(--jv-ease-out) both;
	}
	.dots {
		display: inline-flex;
		gap: var(--jv-space-1);
		padding-block: var(--jv-space-2);
	}
	.dots i {
		inline-size: var(--jv-space-1);
		block-size: var(--jv-space-1);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-sm);
		animation: dot var(--jv-dur-beat) var(--jv-ease-standard) infinite both;
	}
	.dots i:nth-child(2) {
		animation-delay: calc(var(--jv-dur-beat) / 6);
	}
	.dots i:nth-child(3) {
		animation-delay: calc(var(--jv-dur-beat) / 3);
	}
	@keyframes word-in {
		from {
			opacity: 0;
			transform: translateY(var(--jv-space-1));
		}
		to {
			opacity: 1;
			transform: none;
		}
	}
	@keyframes turn-in {
		from {
			opacity: 0;
			transform: translateY(var(--jv-space-2));
		}
	}
	@keyframes dot {
		0%,
		100% {
			opacity: 0.25;
			transform: translateY(0);
		}
		40% {
			opacity: 1;
			transform: translateY(calc(-1 * var(--jv-space-1)));
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.turn,
		.dots i,
		.md :global([data-reveal]) {
			animation: none;
		}
	}
</style>
