<!--
@component
The words under the core: what JARVIS is saying, or what it heard. A new line
fades in; a line that only grows (a reply streaming, a transcript filling in)
does not fade again on every word.

```svelte
<Subtitles text={reply} />
<Subtitles text={heard} speaker="user" />
```

It is a polite live region, so a screen reader hears each finished change once.
JARVIS's words are Markdown, drawn by `renderMarkdown` (escaped first, no raw HTML):
lists, bold, code and tables read as such, not as asterisks (T-0727). The user's
words are what was heard, so they stay plain text.

Does not cover: timing the words to the voice. It shows what it is given.
-->
<script lang="ts">
	import { renderMarkdown } from '$lib/markdown';

	interface Props {
		text: string;
		/** JARVIS's words (bright) or the user's (dim). */
		speaker?: 'jarvis' | 'user';
		testid?: string;
	}
	let { text, speaker = 'jarvis', testid = '' }: Props = $props();

	// Which line this is: a new one when the text stops extending the last.
	// Plain variables, not state: they only remember, nothing renders from them.
	let last = '';
	let line = 0;
	const key = $derived.by(() => {
		if (!last || !text.startsWith(last)) line += 1;
		last = text;
		return line;
	});
	const html = $derived(speaker === 'jarvis' ? renderMarkdown(text) : '');
</script>

<div
	class="subtitles"
	class:user={speaker === 'user'}
	aria-live="polite"
	data-testid={testid || undefined}
	data-speaker={speaker}
>
	{#if text}
		{#key key}
			{#if speaker === 'user'}
				<span class="line">{text}</span>
			{:else}
				<!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes the text before it draws its subset; see $lib/markdown. -->
				<div class="line md">{@html html}</div>
			{/if}
		{/key}
	{/if}
</div>

<style>
	.subtitles {
		margin: 0;
		max-width: 60ch;
		min-height: 1lh;
		text-align: center;
		font-family: var(--jv-font-display);
		font-weight: var(--jv-weight-display);
		font-size: var(--jv-fs-xl);
		color: var(--jv-text);
		text-shadow: var(--jv-glow-subtitle);
	}
	.user {
		color: var(--jv-text-dim);
	}
	.line {
		display: inline-block;
		animation: fade-in var(--jv-dur-fade) var(--jv-ease-out) both;
	}
	/* Paragraphs stay centred under the core; a list or a table is one block, centred,
	   read from its left edge. */
	.md {
		display: block;
	}
	.md :global(p) {
		margin: 0 0 var(--jv-space-2);
	}
	.md :global(:last-child) {
		margin-block-end: 0;
	}
	.md :global(ul),
	.md :global(ol),
	.md :global(table),
	.md :global(pre),
	.md :global(blockquote) {
		display: inline-block;
		margin: 0 0 var(--jv-space-2);
		text-align: start;
	}
	.md :global(ul),
	.md :global(ol) {
		padding-inline-start: var(--jv-space-5);
	}
	.md :global(strong) {
		font-weight: var(--jv-weight-label);
		color: var(--jv-text-bright);
	}
	.md :global(code) {
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-lg);
	}
	.md :global(a) {
		color: var(--jv-accent);
	}
	.md :global(th),
	.md :global(td) {
		padding: 0 var(--jv-space-2);
	}
	@keyframes fade-in {
		from { opacity: 0; }
		to { opacity: 1; }
	}
</style>
