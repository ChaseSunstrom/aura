<!--
@component
Chat, full screen (the owner, 2026-09-27: "a complete chat interface … beautiful UI,
expanded, consistent"), on the v2 design system. The conversations down the side
(new, search, open, delete; a drawer on a phone) and the open one, full height: each
turn with its time, a day line where the day changes, JARVIS's words as markdown,
its tool calls and reasoning folded, and per message a copy and a read-aloud. The
composer grows with what is typed: Enter sends, Shift+Enter is a new line, the send
becomes a stop while a turn runs, and the mic is push-to-talk.

The same Hermes session as the core: what is said here is what the core said, and
the reverse. A photo can go with the words (T-0497), never in a private chat
(T-0462), and a photo still shrinking when Private goes on is dropped.

Does not cover other files (the phone's share sheet takes those), or a conversation
list that follows another device's new chat without a refresh.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { tick } from 'svelte';
	import Markdown from './Markdown.svelte';
	import type { ChatMessage } from '$lib/chat';
	import type { SessionSummary } from '$lib/hermes/protocol';
	import { SUGGESTIONS, ago, dayBreaks, filterSessions, modelOptions, toolLine } from '$lib/hud/chatScreen';
	import type { ApiModel } from '$lib/hud/data/models';
	import { photoForChat } from '$lib/hud/photo';
	import type { Status } from '$lib/ui/status';
	import Label from '../Label.svelte';
	import Button from '../ui/Button.svelte';
	import State from '../ui/State.svelte';

	interface Props {
		messages: readonly ChatMessage[];
		/** A turn is running: the send is a stop. */
		busy: boolean;
		status: Status;
		errorDetail?: string;
		title?: string;
		/** Who answers, for the empty screen's greeting. */
		persona?: string;
		/** The conversations, newest first; null while they load. */
		sessions: readonly SessionSummary[] | null;
		/** The stored id of the open one. */
		current?: string | null;
		/** Push-to-talk is held. */
		recording?: boolean;
		/** `photo` is base64 JPEG, sent with these words only. */
		onsend: (text: string, photo?: string | null) => void;
		onstop: () => void;
		onnew: () => void;
		onopen: (id: string) => void;
		ondelete: (id: string) => void;
		onrefresh: () => void;
		/** Back to the core. */
		onclose: () => void;
		onspeak?: (text: string) => void;
		onmicdown?: () => void;
		onmicup?: () => void;
		onretry?: () => void;
		/** A private conversation is on: nothing said here is kept (docs/explanation/private-chats.md). */
		privateMode?: boolean;
		/** Offered only where the private chat exists; either way the conversation so far is forgotten. */
		onprivate?: (on: boolean) => void;
		/** The manager's models, for the select; this conversation's model; and the switch. */
		models?: readonly Pick<ApiModel, 'id' | 'name' | 'state'>[];
		model?: string;
		/** Resolves false when the switch failed (the host says why), and the select goes back. */
		onmodel?: (id: string) => Promise<boolean>;
	}

	let {
		messages,
		busy,
		status,
		errorDetail = '',
		title = '',
		persona = `${wornName()}`,
		sessions,
		current = null,
		recording = false,
		onsend,
		onstop,
		onnew,
		onopen,
		ondelete,
		onrefresh,
		onclose,
		onspeak,
		onmicdown,
		onmicup,
		onretry,
		privateMode = false,
		onprivate,
		models = [],
		model = '',
		onmodel
	}: Props = $props();

	const modelChoices = $derived(modelOptions(models, model));
	// The pick until Hermes answers, then the session's model again: a failed
	// switch must not leave the select on a model this chat is not on (T-0485).
	let picked = $derived(model);
	async function pickModel(id: string): Promise<void> {
		picked = id;
		if (!(await onmodel?.(id))) picked = model;
	}

	let draft = $state('');
	let photo = $state<{ base64: string; preview: string } | null>(null);
	let photoError = $state('');
	let picker: HTMLInputElement | undefined = $state();
	let query = $state('');
	let rail = $state(false);
	let confirming = $state<string | null>(null);
	let copied = $state<string | null>(null);
	let thread: HTMLElement | undefined = $state();
	let box: HTMLTextAreaElement | undefined = $state();
	let pinned = $state(true);

	const shown = $derived(filterSessions(sessions ?? [], query));
	const breaks = $derived(dayBreaks(messages));
	const name = $derived(persona ? persona[0].toUpperCase() + persona.slice(1) : `${wornName()}`);

	// Follows the conversation down while it is read at the bottom; a person who
	// scrolled up to read is not pulled back by the next token.
	$effect(() => {
		void messages.length;
		void messages[messages.length - 1]?.content;
		if (!pinned || !thread) return;
		void tick().then(() => thread?.scrollTo({ top: thread.scrollHeight }));
	});
	function onscroll(): void {
		if (thread) pinned = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 48;
	}
	function toBottom(): void {
		pinned = true;
		thread?.scrollTo({ top: thread.scrollHeight, behavior: 'smooth' });
	}
	function grow(): void {
		if (!box) return;
		box.style.height = 'auto';
		box.style.height = `${box.scrollHeight}px`;
	}

	// A private chat never reaches Hermes, so a photo picked before it was
	// turned on must not ride the next kept conversation's words either.
	$effect(() => {
		if (!privateMode) return;
		photo = null;
		photoError = '';
	});
	async function pick(): Promise<void> {
		const file = picker?.files?.[0];
		if (picker) picker.value = '';
		if (!file) return;
		photoError = '';
		try {
			const shot = await photoForChat(file);
			// A big photo takes a moment to shrink; Private switched on meanwhile wins.
			if (!privateMode) photo = shot;
		} catch {
			photoError = 'Couldn’t read that photo. Try a JPEG or PNG.';
		}
	}

	function send(text = draft): void {
		const words = text.trim() || (photo ? "What's in this photo?" : '');
		if (!words || busy) return;
		onsend(words, photo?.base64 ?? null);
		photo = null;
		photoError = '';
		draft = '';
		pinned = true;
		void tick().then(grow);
	}
	function key(e: KeyboardEvent): void {
		if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
			e.preventDefault();
			send();
		}
	}
	let copyTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => () => clearTimeout(copyTimer));
	async function copy(id: string, text: string): Promise<void> {
		try {
			await navigator.clipboard.writeText(text);
			copied = id;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = null), 1500);
		} catch {
			/* no clipboard in this context; nothing to undo */
		}
	}
	function open(id: string): void {
		rail = false;
		onopen(id);
	}
	function startNew(): void {
		rail = false;
		onnew();
	}
	const clock = (at: number): string => (at ? new Date(at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '');
	function speaker(m: ChatMessage): string {
		if (m.role === 'user') return 'You';
		return m.persona ? m.persona[0].toUpperCase() + m.persona.slice(1) : name;
	}
	function toolState(t: ChatMessage['tools'][number]): string {
		if (t.state === 'running') return 'running';
		if (t.state === 'failed') return 'failed';
		return t.durationMs != null ? `${t.durationMs} ms` : 'done';
	}
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<section
	class="chat"
	class:railopen={rail}
	aria-label="Chat"
	data-testid="screen-chat"
	onkeydown={(e) => {
		if (e.key !== 'Escape') return;
		e.stopPropagation();
		if (rail) rail = false;
		else onclose();
	}}
>
	<aside class="rail" aria-label="Conversations" data-testid="chatscreen-rail">
		<div class="railhead">
			<Button tone="primary" testid="chatscreen-new" onclick={startNew}>New chat</Button>
			<Button tone="quiet" aria-label="Refresh the conversations" testid="chatscreen-refresh" onclick={onrefresh}>↻</Button>
		</div>
		<input class="search" type="search" placeholder="Search conversations" aria-label="Search conversations" bind:value={query} data-testid="chatscreen-search" />
		<ol class="sessions">
			{#if sessions === null}
				<li class="none" role="status">Loading…</li>
			{:else if shown.length === 0}
				<li class="none">{query ? 'No conversation matches.' : 'No conversations yet.'}</li>
			{:else}
				{#each shown as s (s.id)}
					<li class="session" data-testid="chatscreen-session-{s.id}">
						<button type="button" class="open" onclick={() => open(s.id)} aria-current={s.id === current ? 'true' : undefined}>
							<span class="stitle">{s.title || s.preview || 'Untitled'}</span>
							<span class="smeta">{ago(s.started_at)}{s.message_count ? ` · ${s.message_count}` : ''}</span>
						</button>
						{#if confirming === s.id}
							<span class="confirm">
								<Button tone="danger" testid="chatscreen-delete-yes-{s.id}" onclick={() => ((confirming = null), ondelete(s.id))}>Delete</Button>
								<Button tone="quiet" testid="chatscreen-delete-no-{s.id}" onclick={() => (confirming = null)}>Keep</Button>
							</span>
						{:else}
							<button type="button" class="del" aria-label="Delete {s.title || 'conversation'}" data-testid="chatscreen-delete-{s.id}" onclick={() => (confirming = s.id)}>×</button>
						{/if}
					</li>
				{/each}
			{/if}
		</ol>
	</aside>

	<div class="main">
		<header class="head">
			<span class="toggle"><Button tone="quiet" aria-label="Conversations" aria-pressed={rail} testid="chatscreen-rail-toggle" onclick={() => (rail = !rail)}>☰</Button></span>
			<h1 data-testid="chatscreen-title">{privateMode ? 'Private conversation' : title || 'New conversation'}</h1>
			{#if onmodel && model && modelChoices.length}
				<label class="model">
					<Label>Model</Label>
					<select data-testid="chatscreen-model" value={picked} onchange={(e) => void pickModel(e.currentTarget.value)}>
						{#each modelChoices as o (o.value)}<option value={o.value}>{o.label}</option>{/each}
					</select>
				</label>
			{/if}
			{#if onprivate}
				<Button tone="quiet" aria-label="Private conversation" aria-pressed={privateMode} testid="chatscreen-private" onclick={() => onprivate(!privateMode)}>Private</Button>
			{/if}
			<Button tone="quiet" aria-label="Close the chat" testid="chatscreen-close" onclick={onclose}>✕</Button>
		</header>
		{#if privateMode}
			<p class="private" role="status" data-testid="chatscreen-private-banner">
				Private: not saved, not logged, and {name} has no tools. Switch it off or close the chat and it is gone.
			</p>
		{/if}

		<div class="body">
			{#if status === 'empty'}
				<div class="empty" data-testid="chatscreen-state" data-state="empty">
					<h2>{privateMode ? 'A private conversation' : `Ask ${name} anything`}</h2>
					<p>
						{privateMode
							? `Nothing here is saved to your conversations, ${name}'s memory or a log on the server. ${name} can talk but cannot act.`
							: 'Type below, or hold the mic and talk. Routines, the house, your phone and your files are all in reach.'}
					</p>
					<!-- The suggestions ask for the house; a private chat has no tools to reach it. -->
					<div class="suggestions">
						{#each privateMode ? [] : SUGGESTIONS as s (s)}
							<Button testid="chatscreen-suggestion" onclick={() => send(s)}>{s}</Button>
						{/each}
					</div>
				</div>
			{:else}
				<State {status} error={errorDetail ? `The conversation could not load: ${errorDetail}` : 'The conversation could not load'} {onretry} testid="chatscreen-state">
					<!-- Focusable because it scrolls (axe scrollable-region-focusable). -->
					<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
					<ol class="thread" tabindex="0" aria-label="Messages" aria-live="polite" bind:this={thread} {onscroll} data-testid="chatscreen-thread">
						{#each messages as m, i (m.id)}
							{#if breaks.has(i)}<li class="day" aria-hidden="true"><Label>{breaks.get(i)}</Label></li>{/if}
							<li class="msg" data-role={m.role} data-streaming={m.pending ? '' : undefined} data-testid="chatscreen-msg">
								<p class="meta"><span class="who">{speaker(m)}</span> <span>{clock(m.at)}</span></p>
								{#if m.role === 'assistant' && m.tools.length}
									<details class="fold" data-testid="chatscreen-tools">
										<summary>{m.tools.length === 1 ? toolLine(m.tools[0]) : `${m.tools.length} tool calls`}</summary>
										<ul>
											{#each m.tools as t (t.key)}
												<li data-state={t.state}>
													<span>{toolLine(t)}</span>
													<span class="dim">{toolState(t)}</span>
													{#if t.error}<span class="err">{t.error}</span>{/if}
												</li>
											{/each}
										</ul>
									</details>
								{/if}
								{#if m.thinking}
									<details class="fold"><summary>Reasoning</summary><p>{m.thinking}</p></details>
								{/if}
								{#if m.role === 'assistant'}
									<div class="words"><Markdown text={m.content || (m.pending ? '…' : '')} /></div>
								{:else}
									<p class="words said">{m.content}</p>
								{/if}
								{#if m.error}<p class="err" role="alert">{m.error}</p>{/if}
								{#if !m.pending && m.content}
									<div class="acts">
										<button type="button" class="act" data-testid="chatscreen-copy" onclick={() => copy(m.id, m.content)}>{copied === m.id ? 'Copied' : 'Copy'}</button>
										{#if onspeak && m.role === 'assistant'}
											<button type="button" class="act" data-testid="chatscreen-speak" onclick={() => onspeak(m.content)}>Read aloud</button>
										{/if}
									</div>
								{/if}
							</li>
						{/each}
					</ol>
					{#if !pinned}
						<button type="button" class="latest" data-testid="chatscreen-latest" onclick={toBottom}>Latest ↓</button>
					{/if}
				</State>
			{/if}
		</div>

		{#if photoError}
			<p class="err" role="alert" data-testid="chatscreen-photo-error">{photoError}</p>
		{/if}
		<form
			class="composer"
			onsubmit={(e) => {
				e.preventDefault();
				send();
			}}
			data-testid="chatscreen-composer"
		>
			{#if photo}
				<figure class="thumb" data-testid="chatscreen-photo">
					<img src={photo.preview} alt="Attached, sent with your next message" />
					<button type="button" class="act" data-testid="chatscreen-photo-remove" onclick={() => (photo = null)}>Remove</button>
				</figure>
			{/if}
			{#if !privateMode}
				<input bind:this={picker} type="file" accept="image/*" hidden onchange={() => void pick()} data-testid="chatscreen-picker" />
				<button type="button" class="round" aria-label="Attach a photo" title="Attach a photo" data-testid="chatscreen-attach" onclick={() => picker?.click()}>+</button>
			{/if}
			<textarea
				bind:this={box}
				bind:value={draft}
				rows="1"
				placeholder={privateMode ? `Message ${name} privately` : `Message ${name}`}
				aria-label={privateMode ? `Message ${name} privately` : `Message ${name}`}
				autocomplete="off"
				spellcheck={privateMode ? false : undefined}
				oninput={grow}
				onkeydown={key}
				data-testid="chatscreen-input"
			></textarea>
			{#if onmicdown}
				<button
					type="button"
					class="round"
					class:on={recording}
					aria-label={recording ? 'Release to send' : 'Hold to talk'}
					data-testid="chatscreen-mic"
					onpointerdown={() => onmicdown?.()}
					onpointerup={() => onmicup?.()}
					onpointerleave={() => recording && onmicup?.()}>●</button
				>
			{/if}
			{#if busy}
				<Button tone="danger" testid="chatscreen-stop" onclick={onstop}>Stop</Button>
			{:else}
				<Button tone="primary" type="submit" disabled={!draft.trim() && !photo} testid="chatscreen-send">Send</Button>
			{/if}
		</form>
	</div>
</section>

<style>
	.chat {
		flex: 1;
		display: grid;
		grid-template-columns: minmax(0, var(--jv-measure-card)) minmax(0, 1fr);
		min-block-size: 0;
		position: relative;
	}
	.rail {
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-2);
		min-block-size: 0;
		padding: var(--jv-space-4) var(--jv-space-3);
		border-inline-end: 1px solid var(--jv-line-hair);
	}
	.railhead {
		display: flex;
		gap: var(--jv-space-2);
	}
	.search,
	select,
	textarea {
		min-block-size: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		border: 1px solid var(--jv-line-soft);
		border-radius: var(--jv-radius-sm);
		background: var(--jv-surface-sunken);
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-body);
	}
	.search:focus-visible,
	select:focus-visible,
	textarea:focus-visible,
	button:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.sessions {
		flex: 1;
		overflow-y: auto;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.session {
		display: flex;
		align-items: center;
		gap: var(--jv-space-1);
	}
	.open {
		flex: 1;
		display: grid;
		min-inline-size: 0;
		min-block-size: var(--jv-touch);
		padding: var(--jv-space-1) var(--jv-space-2);
		border: 0;
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text);
		text-align: start;
		cursor: pointer;
	}
	.open:hover {
		background: var(--jv-wash);
	}
	.open[aria-current='true'] .stitle {
		color: var(--jv-accent);
	}
	.stitle {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.smeta,
	.none,
	.meta,
	.dim {
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-xs);
	}
	.none {
		padding: var(--jv-space-2);
	}
	.confirm {
		display: flex;
		gap: var(--jv-space-1);
	}
	.del,
	.round,
	.act,
	.latest {
		min-inline-size: var(--jv-touch);
		min-block-size: var(--jv-touch);
		padding: 0 var(--jv-space-2);
		border: 1px solid transparent;
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-chrome);
		cursor: pointer;
	}
	.del:hover,
	.act:hover,
	.round:hover {
		color: var(--jv-text-bright);
		background: var(--jv-wash);
	}
	.round {
		border-color: var(--jv-line-soft);
		border-radius: var(--jv-radius-pill);
	}
	.round.on {
		color: var(--jv-accent);
		border-color: var(--jv-accent);
	}
	.main {
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
		min-block-size: 0;
		padding: var(--jv-space-4) var(--jv-space-5);
		gap: var(--jv-space-3);
	}
	.head {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.toggle {
		display: none;
	}
	h1 {
		flex: 1;
		margin: 0;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--jv-text-bright);
		font: var(--jv-fs-lg) var(--jv-font-display);
	}
	.model {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.private {
		margin: 0;
		padding-inline-start: var(--jv-space-3);
		border-inline-start: 1px solid var(--jv-warn);
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.body {
		flex: 1;
		display: flex;
		flex-direction: column;
		min-block-size: 0;
		position: relative;
	}
	.empty {
		margin: auto 0;
		max-inline-size: var(--jv-measure-dialog);
	}
	.empty h2 {
		margin: 0 0 var(--jv-space-2);
		color: var(--jv-text-bright);
		font: var(--jv-fs-xl) var(--jv-font-display);
	}
	.empty p {
		margin: 0 0 var(--jv-space-4);
		color: var(--jv-text-dim);
	}
	.suggestions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
	.thread {
		flex: 1;
		overflow-y: auto;
		display: grid;
		align-content: start;
		gap: var(--jv-space-4);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.day {
		justify-self: center;
	}
	.msg {
		display: grid;
		gap: var(--jv-space-1);
		max-inline-size: var(--jv-measure-dialog);
		padding-inline-start: var(--jv-space-3);
		border-inline-start: 1px solid var(--jv-line-soft);
	}
	.msg[data-role='user'] {
		justify-self: end;
		border-inline-start-color: var(--jv-accent);
	}
	.meta {
		margin: 0;
	}
	.who {
		color: var(--jv-text);
	}
	.words {
		margin: 0;
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-body);
		overflow-wrap: anywhere;
	}
	.said {
		white-space: pre-wrap;
	}
	.fold {
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.fold summary {
		min-block-size: var(--jv-touch);
		display: flex;
		align-items: center;
		cursor: pointer;
	}
	.fold ul {
		margin: 0;
		padding-inline-start: var(--jv-space-3);
	}
	.fold li[data-state='failed'] {
		color: var(--jv-danger-text);
	}
	.err {
		margin: 0;
		color: var(--jv-danger-text);
		font-size: var(--jv-fs-sm);
	}
	.acts {
		display: flex;
		gap: var(--jv-space-1);
	}
	.latest {
		position: absolute;
		inset: auto 0 var(--jv-space-2) auto;
		border-color: var(--jv-line-soft);
		background: var(--jv-surface-sunken);
	}
	.composer {
		display: flex;
		align-items: flex-end;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
	textarea {
		flex: 1 1 var(--jv-measure-card);
		min-inline-size: 0;
		max-block-size: var(--jv-measure-card-height);
		padding-block: var(--jv-space-2);
		resize: none;
	}
	textarea::placeholder,
	.search::placeholder {
		color: var(--jv-text-faint);
	}
	.thumb {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		margin: 0;
		inline-size: 100%;
	}
	.thumb img {
		block-size: var(--jv-touch);
		border-radius: var(--jv-radius-sm);
	}
	/* A phone, or the side panel on a wider window (T-0725): the conversations are a
	   drawer over the thread. Its box, not the window's: Chat is drawn in the shell's
	   panel, a container. */
	@container (max-width: 720px) {
		.chat {
			grid-template-columns: minmax(0, 1fr);
		}
		.rail {
			display: none;
			position: absolute;
			/* Below the head, so the toggle that opened it closes it. */
			inset: calc(var(--jv-touch) + var(--jv-space-3) * 2) 0 0;
			z-index: 2;
			background: var(--jv-surface-sunken);
		}
		.railopen .rail {
			display: flex;
		}
		.toggle {
			display: inline-flex;
		}
		/* The model on a row of its own: beside the title it pushed Private and the close
		   off a 390px screen and squeezed the title to nothing (T-0692). */
		.head {
			flex-wrap: wrap;
		}
		.model {
			order: 1;
			flex-basis: 100%;
			min-inline-size: 0;
		}
		.model select {
			flex: 1;
			min-inline-size: 0;
		}
		.main {
			padding: var(--jv-space-3);
		}
	}
</style>
