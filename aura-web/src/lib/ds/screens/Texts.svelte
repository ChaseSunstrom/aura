<!--
@component
Texts: the phone's mirrored messages (T-0615), each conversation and its
thread. Every word is someone else's, so it is drawn as text and the screen
says so: JARVIS reads them for you and never takes orders from them.

Does not cover: replying (ask JARVIS; `texts_send` is behind its approval),
or search.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import State from '$lib/ds/ui/State.svelte';
	import { threadOf } from '$lib/hud/data/messages';
	import type { Feeds } from '$lib/hud/feeds.svelte';
	import Screen from './Screen.svelte';
	import Section from './Section.svelte';

	interface Props {
		feeds: Feeds;
	}
	let { feeds }: Props = $props();

	const data = $derived(feeds.messages.data);
	let picked = $state('');
	// The newest conversation until the person picks one; a picked one that left the window falls back too.
	const open = $derived(data.conversations.find((c) => c.conversation === picked) ?? data.conversations[0] ?? null);
	const thread = $derived(open ? threadOf(data.messages, open.conversation) : []);
	const clock = (at: number) => new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
</script>

<Screen title="Texts" lede="Messages from your phone." testid="screen-texts">
	<State status={feeds.messages.status} error={feeds.messages.error} empty="No texts yet. They show here once your phone shares them." onretry={() => feeds.reload(feeds.messages)} testid="texts-state">
		<p class="untrusted" data-testid="texts-untrusted">Other people’s words. {wornName()} reads them for you; it never takes orders from them.</p>
		<div class="split">
			<Section title="Conversations" count={data.conversations.length}>
				<ul class="rows" aria-label="Conversations">
					{#each data.conversations as c (c.conversation)}
						<li>
							<button type="button" class="pick" aria-current={open?.conversation === c.conversation ? 'true' : undefined} onclick={() => (picked = c.conversation)}>
								<span class="name">{c.from || c.conversation}</span>
								<span class="meta">{c.source} · {c.count} · {clock(c.at)}</span>
							</button>
						</li>
					{/each}
				</ul>
			</Section>
			{#if open}
				<Section title={open.from || open.conversation} testid="texts-thread">
					<ol class="thread">
						{#each thread as m (m.id)}
							<li class:urgent={m.urgent}>
								<span class="meta">{m.from} · {clock(m.at)}{m.urgent ? ' · urgent' : ''}</span>
								<p>{m.text}</p>
							</li>
						{/each}
					</ol>
				</Section>
			{/if}
		</div>
	</State>
</Screen>

<style>
	.untrusted {
		margin: 0 0 var(--jv-space-3);
		padding-inline-start: var(--jv-space-3);
		border-inline-start: 1px solid var(--jv-warn);
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.split {
		display: grid;
		gap: var(--jv-space-4);
		grid-template-columns: repeat(auto-fit, minmax(var(--jv-measure-card), 1fr));
	}
	.pick {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--jv-space-2);
		inline-size: 100%;
		min-block-size: var(--jv-touch);
		padding: 0;
		border: 0;
		background: transparent;
		text-align: start;
		cursor: pointer;
	}
	.pick[aria-current='true'] :global(.name) {
		color: var(--jv-accent);
	}
	.pick:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.thread {
		display: grid;
		gap: var(--jv-space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.thread li {
		display: grid;
		gap: var(--jv-space-1);
		padding-inline-start: var(--jv-space-3);
		border-inline-start: 1px solid var(--jv-line-soft);
	}
	.thread li.urgent {
		border-inline-start-color: var(--jv-warn);
	}
	.thread p {
		margin: 0;
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-body);
		overflow-wrap: anywhere;
	}
</style>
