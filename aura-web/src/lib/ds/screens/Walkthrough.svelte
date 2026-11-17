<!--
@component
The first-run walkthrough (T-0629): six steps in order, each one a thing JARVIS proposes
and does itself when asked, with the screen where the owner can do it by hand.

A step the house can show (a password, an enrolled voice, a paired device) is done only
when the house shows it; the other three are done when the owner says so. Progress (the
owner's marks) is kept by the console's server, so a reload or another browser resumes at
the same step. "Waiting on your yes" counts every request waiting, not only this step's, and
"What was done" is JARVIS's latest reply. What the owner types under "What you want" is sent to JARVIS as an ordinary
turn with the step's request and is not stored by this screen.

Does not cover: doing a step here (each opens its own screen, except the capability levels,
which are drawn in place); whether what JARVIS proposed was sensible; a console that cannot
reach its own server, which shows the steps without saved progress and says so.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import type { Feeds } from '$lib/hud/feeds.svelte';
	import { levelRequest, type Capability, type Level } from '$lib/hud/data/capabilities';
	import { wornName } from '$lib/ds/theme.svelte';
	import Label from '../Label.svelte';
	import Button from '../ui/Button.svelte';
	import Field from '../ui/Field.svelte';
	import State from '../ui/State.svelte';
	import { STEPS, TEXT, close, current, mark, parse, serialize, status, type Mark, type Progress, type StepId } from '../walkthrough/model';
	import Levels from './Levels.svelte';
	import Screen from './Screen.svelte';
	import Section from './Section.svelte';

	interface Props {
		feeds: Feeds;
		/** The console has no password yet (`GET /api/console`). */
		firstRun: boolean;
		/** Requests waiting on a yes right now. */
		waiting: number;
		/** JARVIS's last reply, shown under the step that asked for it. */
		reply?: string;
		busy?: boolean;
		/** Hands JARVIS a request; the host submits it and says so. */
		onask: (text: string) => void;
	}
	let { feeds, firstRun, waiting, reply = '', busy = false, onask }: Props = $props();

	let look = $state<'loading' | 'ready' | 'error'>('loading');
	let failed = $state('');
	let progress = $state<Progress | null>(null);
	let people = $state(0);
	let unsaved = $state('');
	let picked = $state<StepId | null>(null);
	let asked = $state<StepId | null>(null);
	let want = $state('');

	const house = $derived({ firstRun, people, devices: feeds.companions.data?.length ?? 0 });
	const at = $derived(current(progress, house));
	const step = $derived(picked ?? at ?? STEPS[STEPS.length - 1]);
	const text = $derived(TEXT[step]);
	const now = $derived(status(step, progress, house));
	const WORD = { done: 'Done', skipped: 'Skipped', open: 'To do' } as const;

	async function load(): Promise<void> {
		look = 'loading';
		try {
			const [row, speaker] = await Promise.all([fetch('/api/console/walkthrough'), fetch('/api/voice/speaker').catch(() => null)]);
			if (!row.ok) throw new Error(`the console answered ${row.status}`);
			progress = parse(((await row.json()) as { progress?: string | null }).progress);
			// The house not answering is not this screen's failure: the step stays open and says why.
			const body = speaker?.ok ? ((await speaker.json()) as { people?: { enrolled?: boolean }[] }) : {};
			people = (body.people ?? []).filter((p) => p.enrolled).length;
			look = 'ready';
		} catch (e) {
			failed = e instanceof Error ? e.message : String(e);
			look = 'error';
		}
	}
	onMount(() => void load());

	async function save(next: Progress): Promise<void> {
		progress = next;
		unsaved = '';
		try {
			const res = await fetch('/api/console/walkthrough', {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ progress: serialize(next) })
			});
			if (!res.ok) throw new Error(`the console answered ${res.status}`);
		} catch (e) {
			unsaved = `Not saved: ${e instanceof Error ? e.message : String(e)}. It holds until this page reloads.`;
		}
	}
	function say(m: Mark): void {
		picked = null;
		// Opened from Settings with no row yet: saved closed, so it never opens by itself.
		void save(mark(progress ?? close(null), step, m));
	}
	function propose(e: SubmitEvent): void {
		e.preventDefault();
		const words = want.trim();
		asked = step;
		want = '';
		onask(words ? `${text.ask} What I want: ${words}` : text.ask);
	}
	const level = (c: Capability, l: Level) => onask(levelRequest(c, l));
</script>

<Screen title="Setup" lede="Six steps. {wornName()} proposes each one and does what it may; you say yes to the rest." testid="screen-setup">
	<State status={look} error={failed} onretry={() => void load()}>
		<ol class="rail" aria-label="Setup steps" data-testid="setup-rail">
			{#each STEPS as s, i (s)}
				{@const st = status(s, progress, house)}
				<li>
					<button type="button" class="step" data-state={st} aria-current={s === step ? 'step' : undefined} data-testid="setup-step-{s}" onclick={() => (picked = s)}>
						<span class="n">{String(i + 1).padStart(2, '0')}</span>
						<span class="name">{TEXT[s].title}</span>
						<span class="meta">{WORD[st]}</span>
					</button>
				</li>
			{/each}
		</ol>

		{#if at === null && picked === null}
			<p class="all" data-testid="setup-finished">Every step is done or skipped. Pick one above to look at it again.</p>
		{/if}

		<Section title={text.title} lede={text.what} testid="setup-current">
			<p class="state" data-state={now} data-testid="setup-state">
				<Label tone={now === 'open' ? 'warn' : 'faint'}>{WORD[now]}</Label>
				{#if now === 'open' && text.needs}<span>{text.needs}</span>{/if}
			</p>

			<form class="ask" onsubmit={propose}>
				<Field label="What you want here (optional)" placeholder="What you want here (optional)" autocomplete="off" bind:value={want} testid="setup-want" />
				<Button tone="primary" type="submit" disabled={busy} testid="setup-propose">Ask {wornName()}</Button>
			</form>

			{#if asked === step}
				<div class="proposal" data-testid="setup-proposal" aria-live="polite">
					<Label>{busy ? 'Working' : 'What was done'}</Label>
					<p>{reply || (busy ? 'Thinking it through.' : 'No reply yet.')}</p>
					<Label tone={waiting ? 'warn' : 'faint'}>Waiting on your yes</Label>
					<p data-testid="setup-waiting">{waiting ? `${waiting} request${waiting === 1 ? '' : 's'}. Answer on the card.` : 'Nothing.'}</p>
				</div>
			{/if}

			{#if step === 'privacy'}
				<Levels status={feeds.capabilities.status} error={feeds.capabilities.error} capabilities={feeds.capabilities.data} onlevel={level} onretry={() => feeds.reload(feeds.capabilities)} />
			{/if}

			<div class="acts">
				<Button href={text.open.path} testid="setup-open">Open {text.open.label}</Button>
				{#if now === 'open'}
					{#if !text.needs}<Button onclick={() => say('done')} testid="setup-done">Mark done</Button>{/if}
					<Button tone="quiet" onclick={() => say('skipped')} testid="setup-skip">Skip</Button>
				{/if}
			</div>
			{#if unsaved}<p class="unsaved" role="alert">{unsaved}</p>{/if}
		</Section>
	</State>
</Screen>

<style>
	.rail {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(var(--jv-measure-card), 1fr));
		gap: var(--jv-space-2);
		margin: 0 0 var(--jv-space-5);
		padding: 0;
		list-style: none;
	}
	.step {
		display: grid;
		gap: var(--jv-space-1);
		inline-size: 100%;
		min-block-size: var(--jv-touch);
		padding: var(--jv-space-2) var(--jv-space-3);
		border: 0;
		border-block-start: 1px solid var(--jv-line-soft);
		background: transparent;
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-chrome);
		text-align: start;
		cursor: pointer;
	}
	.step[data-state='done'] {
		border-block-start-color: var(--jv-ok);
	}
	.step[aria-current='step'] {
		border-block-start-color: var(--jv-accent);
		background: var(--jv-wash);
		color: var(--jv-text-bright);
	}
	.step:hover {
		background: var(--jv-wash);
	}
	.step:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.n {
		color: var(--jv-accent);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
	}
	.step .meta {
		color: var(--jv-text-faint);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-control);
		text-transform: uppercase;
	}
	.state,
	.acts,
	.ask {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-3);
		margin: 0 0 var(--jv-space-4);
	}
	.state span,
	.all,
	.proposal p {
		margin: 0;
		color: var(--jv-text-dim);
		font: var(--jv-fs-md) var(--jv-font-body);
	}
	.all {
		margin-block-end: var(--jv-space-4);
	}
	.proposal {
		display: grid;
		gap: var(--jv-space-2);
		margin-block-end: var(--jv-space-4);
		padding-inline-start: var(--jv-space-3);
		border-inline-start: 1px solid var(--jv-accent);
	}
	.proposal p {
		white-space: pre-wrap;
	}
	.unsaved {
		margin: 0;
		color: var(--jv-danger-text);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
</style>
