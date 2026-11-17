<!--
@component
Approvals (Control.dc.html): everything waiting for the person's OK, from
Hermes, the house and the phone, each as the card the core draws: what, why,
impact, the yes (held when it cannot be undone or no server card vouches for
it), deny, "Show me first" and the rule for next time. Under it, what waits to
be remembered (T-1503): each proposal whole, where it would be stored, who
proposed it and what that turn had read, with Drop and Keep, and over two or
more a Keep all (a cardful, 8, in one ask, so one card shows each) and a Drop
all (what is shown, T-1505). Keep stores nothing from here: it asks, and the
yes is said on the card that then shows under "Waiting". Then every settings
change the settings plugin holds as a proposal (T-1316): the key, the value now
and the value it becomes, Apply and Discard; a secret draws neither value. Then
every standing "always" answer still in force, each with a Revoke that asks
first (T-0570).

Does not cover: what was answered before (the audit log), a request answered
elsewhere, which leaves this list when its link says so, or a grant revoked
elsewhere while this screen is open (it goes on Retry or the next visit). A
settings proposal made while the screen is open shows on the next visit too:
the plugin announces none. A memory proposal made by a routine while the screen
is open is heard within about five seconds (the gateway's look at the queue,
T-1506); one made, kept or dropped in a conversation is heard at once. A setting changed by asking in words is never a
proposal: it takes the card under "Waiting", which draws the same two values
with the same rows (`ChangeRows`, T-1317), or no card for a plain setting.
-->
<script lang="ts">
	import { HoldButton, Label } from '$lib/ds';
	import { wornName } from '$lib/ds/theme.svelte';
	import { tick, untrack } from 'svelte';

	import Button from '$lib/ds/ui/Button.svelte';
	import State from '$lib/ds/ui/State.svelte';
	import ApprovalCard from '../shell/ApprovalCard.svelte';
	import { batchable, type Ask, type Grant, type Rule } from '../shell/approval';
	import type { Status } from '$lib/ui/status';
	import ChangeRows from './ChangeRows.svelte';
	import Screen from './Screen.svelte';
	import Section from './Section.svelte';
	import { KEEP_BATCH, memoryQueue, NO_MEMORY, type MemoryIo, type MemoryView } from './memory-proposals';
	import { NO_PROPOSALS, proposalsQueue, type ProposalsIo, type ProposalsView } from './settings-proposals';

	interface Props {
		asks: readonly Ask[];
		/** Offline when neither link that raises requests is up. */
		status: Status;
		busy?: boolean;
		onanswer: (ask: Ask, choice: Rule['choice'] | 'deny', text?: string) => void;
		/** Approves these ids, each once: the host re-checks every one before it answers. */
		onbatch?: (ids: readonly string[]) => void;
		/** The standing answers, from JARVIS' guard; absent when JARVIS is not connected. */
		loadgrants?: () => Promise<Grant[]>;
		onrevoke?: (baseKey: string) => Promise<void>;
		/** The settings plugin's waiting changes and their two answers; absent while JARVIS is not connected. */
		proposals?: ProposalsIo;
		/** Bumped on every `settings.proposals`, so the list is read again (T-1322). */
		proposalsChanged?: number;
		/** What waits to be remembered, and its Drop (T-1503); absent while JARVIS is not connected. */
		memory?: Pick<MemoryIo, 'load' | 'drop'>;
		/** Bumped on every `memory.proposals`, so that list is read again. */
		memoryChanged?: number;
		/** Hand JARVIS a sentence. A Keep is asked, never done here: the approval card is where the yes is said. */
		onask?: (text: string) => void;
		/** A turn is running: nothing more is asked until it is over. */
		working?: boolean;
	}
	let {
		asks,
		status,
		busy = false,
		onanswer,
		onbatch,
		loadgrants,
		onrevoke,
		proposals,
		proposalsChanged = 0,
		memory,
		memoryChanged = 0,
		onask,
		working = false
	}: Props = $props();
	const batch = $derived(batchable(asks));
	const shown = $derived(status === 'ready' && !asks.length ? 'empty' : status);

	let grants = $state<Grant[] | null>(null);
	let grantsError = $state('');
	let confirming = $state<string | null>(null);
	async function load(): Promise<void> {
		if (!loadgrants) return;
		grants = null;
		grantsError = '';
		try {
			grants = await loadgrants();
		} catch (e) {
			grantsError = e instanceof Error ? e.message : String(e);
		}
	}
	$effect(() => {
		if (status === 'ready') void load();
	});
	/** The confirm swaps the button out from under the focus: hand it on, to Keep (the safe one) and back. */
	async function ask(baseKey: string | null, row: string): Promise<void> {
		confirming = baseKey;
		await tick();
		const target = baseKey ? 'approvals-grant-revoke-no' : 'approvals-grant-revoke';
		document.querySelector<HTMLElement>(`[data-grant="${CSS.escape(row)}"] [data-testid="${target}"]`)?.focus();
	}
	async function revoke(baseKey: string): Promise<void> {
		confirming = null;
		try {
			await onrevoke?.(baseKey);
		} catch (e) {
			grantsError = e instanceof Error ? e.message : String(e);
			return;
		}
		await load();
	}
	const grantsShown = $derived(
		!loadgrants || status === 'offline' ? 'offline' : grantsError ? 'error' : grants === null ? 'loading' : grants.length ? 'ready' : 'empty'
	);

	let pview = $state<ProposalsView>(NO_PROPOSALS);
	const gone = (): Promise<never> => Promise.reject(new Error(`${wornName()} is not connected`));
	const queue = proposalsQueue(
		{
			load: () => proposals?.load() ?? gone(),
			apply: (id) => proposals?.apply(id) ?? gone(),
			discard: (id) => proposals?.discard(id) ?? gone()
		},
		(v) => (pview = v)
	);
	// Read again each time the link comes back: a proposal may have been made, or answered, meanwhile.
	$effect(() => {
		if (status === 'ready' && proposals) void queue.read();
	});
	// The plugin announced that the list changed: read it again, keeping what is drawn meanwhile.
	let heard = untrack(() => proposalsChanged); // the open itself reads: only a later bump re-reads
	$effect(() => {
		const n = proposalsChanged;
		if (n === heard) return;
		heard = n;
		if (status === 'ready' && proposals) void queue.changed();
	});
	const proposalsShown = $derived(
		!proposals || status === 'offline' ? 'offline' : pview.error ? 'error' : pview.cards === null ? 'loading' : pview.cards.length ? 'ready' : 'empty'
	);
	/** The answered card leaves with the focus on it: hand the focus to the next control in the section. */
	async function answerProposal(id: string, verb: 'apply' | 'discard'): Promise<void> {
		await queue.act(id, verb);
		await tick();
		if (document.activeElement === document.body) document.querySelector<HTMLElement>('[data-testid="approvals-proposals"] button')?.focus();
	}
	let mview = $state<MemoryView>(NO_MEMORY);
	const mqueue = memoryQueue(
		{
			load: () => memory?.load() ?? gone(),
			drop: (id) => memory?.drop(id) ?? gone(),
			ask: (text) => onask?.(text)
		},
		(v) => (mview = v)
	);
	// Read on open and each time the link comes back: a routine proposes while no console hears it.
	$effect(() => {
		if (status === 'ready' && memory) void mqueue.read();
	});
	let memoryHeard = untrack(() => memoryChanged); // as `heard`: only a later bump re-reads
	$effect(() => {
		const n = memoryChanged;
		if (n === memoryHeard) return;
		memoryHeard = n;
		if (status === 'ready' && memory) void mqueue.changed();
	});
	// The turn a Keep was asked in is over: what still waits was not kept, and can be asked again.
	let wasWorking = untrack(() => working);
	$effect(() => {
		const now = working;
		if (wasWorking && !now && status === 'ready' && memory) void mqueue.settled();
		wasWorking = now;
	});
	const memoryShown = $derived(
		!memory || status === 'offline' ? 'offline' : mview.error ? 'error' : mview.cards === null ? 'loading' : mview.cards.length ? 'ready' : 'empty'
	);
	/** As `answerProposal`: the dropped card leaves with the focus on it. */
	async function dropMemory(id: string): Promise<void> {
		await mqueue.drop(id);
		await tick();
		if (document.activeElement === document.body) document.querySelector<HTMLElement>('[data-testid="approvals-memory"] button')?.focus();
	}
	/** Drop all: the list goes with the focus on its button, so the focus goes to the section's next control. */
	async function dropAllMemory(): Promise<void> {
		await mqueue.dropAll();
		await tick();
		if (document.activeElement === document.body) document.querySelector<HTMLElement>('[data-testid="approvals-proposals"] button')?.focus();
	}
	/** How many a Keep all would name now: a cardful at most (T-1505). */
	const keepAllCount = $derived(mview.cards ? mview.cards.filter((c) => !mview.asked.includes(c.id)).length : 0);
	const SCOPE: Readonly<Record<string, string>> = { 'at-home': 'at home', 'after-outside': 'after outside text' };
	const day = (s: number) => new Date(s * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' });
</script>

<Screen title="Approvals" lede="What is waiting for your OK." testid="screen-approvals">
	<Section title="Waiting" count={asks.length} lede="Also showing on your phone and watch.">
		<State status={shown} empty="Nothing is waiting for your OK." testid="approvals-state">
			{#if batch.length && onbatch}
				<Button tone="primary" disabled={busy} onclick={() => onbatch(batch)} testid="approvals-batch">Approve all {batch.length}</Button>
			{/if}
			<ul class="cards">
				{#each asks as ask (ask.id)}
					<li><ApprovalCard {ask} {busy} {onanswer} testid="approvals-card" /></li>
				{/each}
			</ul>
		</State>
	</Section>
	<Section
		title="To remember"
		count={mview.cards?.length}
		lede="What {wornName()} wants to remember. Nothing is stored until you keep it and say yes on its card."
		testid="approvals-memory"
	>
		<!-- Outside the state, as the settings changes' reason is. -->
		{#if mview.failed}<p class="failed" role="alert" data-testid="memory-failed">{mview.failed}</p>{/if}
		<State
			status={memoryShown}
			empty="Nothing is waiting to be remembered."
			error={mview.error}
			onretry={memory ? () => void mqueue.read() : undefined}
			testid="approvals-memory-state"
		>
			{#if (mview.cards?.length ?? 0) > 1}
				<!-- Over the whole list (T-1505): one ask for a cardful, so one card shows each fact; one drop for what is shown. -->
				<div class="verbs all" aria-busy={mview.acting === 'all'}>
					<Button
						disabled={!onask || working || !keepAllCount || !!mview.acting}
						onclick={() => mqueue.keepAll()}
						testid="memory-keep-all"
						>{keepAllCount > KEEP_BATCH ? `Keep the first ${KEEP_BATCH}` : `Keep all ${keepAllCount || mview.cards?.length}`}</Button
					>
					<Button tone="quiet" disabled={!!mview.acting} onclick={() => void dropAllMemory()} testid="memory-drop-all">Drop all {mview.cards?.length}</Button>
				</div>
			{/if}
			<ul class="cards">
				{#each mview.cards ?? [] as c (c.id)}
					{@const asked = mview.asked.includes(c.id)}
					<li class="proposal memory" data-testid="memory-card" data-proposal={c.id} aria-busy={mview.acting === c.id}>
						<span class="corner" aria-hidden="true"></span>
						<div class="cap">
							<Label tone="violet" testid="memory-where">For {c.where}</Label>
							{#if c.instruction}<Label tone="warn" testid="memory-instruction">Worded as an instruction</Label>{/if}
							{#if c.when}<span title={c.stamp}><Label>{c.when}</Label></span>{/if}
						</div>
						<!-- Whole, never clipped: this sentence is what would be stored. -->
						<p class="fact" data-testid="memory-text">{c.text}</p>
						<p class="what" data-testid="memory-source">{c.source}.</p>
						{#if c.instruction}
							<p class="what" data-testid="memory-instruction-note">
								If you keep it, {wornName()} reads it back as your own words in every later conversation.
							</p>
						{/if}
						{#if asked}<p class="asked" role="status" data-testid="memory-asked">Asked {wornName()} to keep it. Say yes on its card under Waiting.</p>{/if}
						<div class="verbs">
							<Button
								tone="primary"
								disabled={!onask || working || asked || !!mview.acting}
								aria-label="Keep: {c.text}"
								onclick={() => mqueue.keep(c.id)}
								testid="memory-keep">Keep</Button
							>
							<Button tone="ghost" disabled={!!mview.acting} aria-label="Drop: {c.text}" onclick={() => void dropMemory(c.id)} testid="memory-drop">Drop</Button>
						</div>
					</li>
				{/each}
			</ul>
		</State>
	</Section>
	<Section
		title="Settings changes"
		count={pview.cards?.length}
		lede="A setting an app asked to change. Nothing is written until you apply it."
		testid="approvals-proposals"
	>
		<!-- Outside the state: the reason must still be read when the refused card was the last one. -->
		{#if pview.failed}<p class="failed" role="alert" data-testid="proposal-failed">{pview.failed}</p>{/if}
		<State
			status={proposalsShown}
			empty="No settings change is waiting."
			error={pview.error}
			onretry={proposals ? () => void queue.read() : undefined}
			testid="approvals-proposals-state"
		>
			<ul class="cards">
				{#each pview.cards ?? [] as c (c.id)}
					<li class="proposal" data-testid="proposal-card" aria-busy={pview.acting === c.id}>
						<span class="corner" aria-hidden="true"></span>
						<div class="cap">
							<Label tone="warn">Waiting for your OK</Label>
							{#if c.applies}<Label>{c.applies}</Label>{/if}
						</div>
						<h3>{c.key}</h3>
						{#if c.what}<p class="what">{c.what}</p>{/if}
						<ChangeRows secret={c.secret} before={c.before} after={c.after} testid="proposal" />
						<div class="verbs">
							{#if c.secret}
								<HoldButton label="Apply" tone="warn" disabled={!!pview.acting} onconfirm={() => void answerProposal(c.id, 'apply')} testid="proposal-apply" />
							{:else}
								<Button tone="primary" disabled={!!pview.acting} aria-label="Apply {c.key}" onclick={() => void answerProposal(c.id, 'apply')} testid="proposal-apply">Apply</Button>
							{/if}
							<Button tone="ghost" disabled={!!pview.acting} aria-label="Discard {c.key}" onclick={() => void answerProposal(c.id, 'discard')} testid="proposal-discard">Discard</Button>
						</div>
					</li>
				{/each}
			</ul>
		</State>
	</Section>
	<Section
		title="Standing approvals"
		count={grants?.length}
		lede="What {wornName()} may do without asking, because you said always. Each one lapses on its own."
		testid="approvals-grants"
	>
		<State status={grantsShown} empty="Nothing stands: {wornName()} asks every time." error={grantsError} onretry={load} testid="approvals-grants-state">
			<ul class="rows">
				{#each grants ?? [] as g (g.base_key)}
					<li data-testid="approvals-grant" data-grant={g.base_key}>
						<div class="grant">
							<span class="name">{g.tool || g.base_key}</span>
							<span class="meta">
								{['since ' + day(g.granted_at), 'until ' + day(g.expires_at), ...g.scopes.map((s) => SCOPE[s] ?? s)].join(' · ')}
							</span>
						</div>
						{#if confirming === g.base_key}
							<span class="confirm">
								<Button tone="danger" testid="approvals-grant-revoke-yes" onclick={() => void revoke(g.base_key)}>Revoke</Button>
								<Button tone="quiet" testid="approvals-grant-revoke-no" onclick={() => void ask(null, g.base_key)}>Keep</Button>
							</span>
						{:else}
							<Button tone="ghost" aria-label="Revoke {g.tool || g.base_key}" testid="approvals-grant-revoke" onclick={() => void ask(g.base_key, g.base_key)}>Revoke</Button>
						{/if}
					</li>
				{/each}
			</ul>
		</State>
	</Section>
</Screen>

<style>
	.cards {
		display: grid;
		gap: var(--jv-space-4);
		margin: 0;
		padding: 0;
		list-style: none;
	}
	/* The approval card's look (shell/ApprovalCard): light on a backing, a hairline and a bracket, no fill. */
	.proposal {
		position: relative;
		display: grid;
		gap: var(--jv-space-3);
		min-inline-size: 0;
		padding: var(--jv-space-5);
		background: var(--jv-projection-backing);
		border-inline-start: 1px solid var(--jv-line);
		text-shadow: var(--jv-glow-text);
	}
	.corner {
		position: absolute;
		inset: 0 auto auto 0;
		inline-size: var(--jv-projection-bracket);
		block-size: var(--jv-projection-bracket);
		border-inline-start: var(--jv-rule-live) solid var(--jv-accent);
		border-block-start: var(--jv-rule-live) solid var(--jv-accent);
	}
	/* Violet is memory: the bracket says what kind of thing waits. */
	.memory .corner {
		border-color: var(--jv-violet);
	}
	.fact {
		margin: 0;
		padding-inline-start: var(--jv-space-3);
		border-inline-start: 1px solid var(--jv-line-soft);
		color: var(--jv-text-bright);
		font: var(--jv-fs-md) var(--jv-font-body);
		overflow-wrap: anywhere;
	}
	.asked {
		margin: 0;
		color: var(--jv-warn);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.cap {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-1) var(--jv-space-3);
		align-items: baseline;
	}
	h3 {
		margin: 0;
		color: var(--jv-text-bright);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-md);
		font-weight: var(--jv-weight-label);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
		overflow-wrap: anywhere;
	}
	.what,
	.failed {
		margin: 0;
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.failed {
		color: var(--jv-danger-text);
	}
	.verbs {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
		align-items: center;
	}
	.verbs.all {
		margin-block-end: var(--jv-space-3);
	}
	.grant {
		display: grid;
		flex: 1 1 0;
		gap: var(--jv-space-1);
		min-inline-size: 0;
	}
	.confirm {
		display: flex;
		gap: var(--jv-space-1);
	}
</style>
