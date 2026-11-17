<!--
@component
One open request, as the Control board draws it: what would happen, why,
what it costs, then the yes (held when it cannot be undone), the no, "Show me
first" and the "Next time" rule. A question gets its offered answers and a
line to type one instead. A settings change also shows the value now and the
value it would become (`ask.change`, drawn by `screens/ChangeRows`); a secret
shows neither.

The card answers nothing itself: `onanswer` hands the host the request and
the choice, and the host routes it to the link that asked (Hermes, the house
or the phone).

"Approve all N" is drawn only when the host passes `batch` (its queue's
`batchable` ids) and `onbatch`; the card never decides which asks go in it.

Does not cover: several requests at once (the host shows the oldest and says
how many wait), or a rule the link did not offer (`rulesFor` drops it). "Ask why"
is a deny with a line of text: the host (`Shell.answer`) says the text to the
assistant after the no, as no approval link carries it.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { HoldButton, Label } from '$lib/ds';
	import Button from '$lib/ds/ui/Button.svelte';
	import Segmented from '$lib/ds/ui/Segmented.svelte';
	import ChangeRows from '../screens/ChangeRows.svelte';
	import { ASK_WHY, riskOf, type Ask, type Rule } from './approval';

	interface Props {
		ask: Ask;
		/** How many more wait behind this one. */
		more?: number;
		busy?: boolean;
		onanswer: (ask: Ask, choice: Rule['choice'] | 'deny', text?: string) => void;
		/** The queue's `batchable` ids, from a host that holds the whole queue; with `onbatch` draws "Approve all N". */
		batch?: readonly string[];
		onbatch?: (ids: readonly string[]) => void;
		testid?: string;
	}
	let { ask, more = 0, busy = false, onanswer, batch = [], onbatch, testid = 'approval-card' }: Props = $props();

	let rule = $state<Rule['choice']>('once');
	let showing = $state(false);
	let typed = $state('');
	// A new request starts from "Ask every time" with its detail folded: a rule
	// picked for the last one is not this one's. On the id alone: the shell hands
	// over a fresh `ask` object for the same request on every HUD change, and an
	// effect on the object wiped a half-typed answer while the reply streamed (T-0823).
	const askId = $derived(ask.id);
	$effect(() => {
		void askId;
		rule = 'once';
		showing = false;
		typed = '';
	});

	const titleId = $derived(`ask-${ask.id.replace(/[^a-z0-9]/gi, '-')}`);
	/** The yes, in the request's own verb: "Delete 3 old snapshots?" holds to "Delete". */
	// Only a server card's verb: a requester's own words would let "Check the weather" label a send.
	const verb = $derived.by(() => {
		if (!ask.carded) return 'Approve';
		const first = ask.what.trim().split(/\s+/)[0]?.replace(/[^\p{L}-]/gu, '') ?? '';
		return first && first.length <= 16 ? first : 'Approve';
	});
	const cap = $derived(
		ask.kind === 'clarify' ? `${wornName()} asks` : 'Needs your OK'
	);
	const yes = (): void => onanswer(ask, rule);
	const risk = $derived(riskOf(ask));
	const RISK = { low: 'Low risk', irreversible: 'Can’t be undone', phone: 'Confirm on phone' } as const;
	/** Ask why is a no, never a yes: the text asks the model to explain before it asks again. */
	const WHY = ASK_WHY;
</script>

<div class="card" role="alertdialog" aria-labelledby={titleId} aria-describedby="{titleId}-why" data-testid={testid}>
	<span class="corner" aria-hidden="true"></span>
	<div class="cap">
		<Label tone={ask.undoable || ask.kind === 'clarify' ? 'accent' : 'warn'}>{cap}{more ? ` · ${more} more waiting` : ''}</Label>
		{#if risk}<Label tone={risk === 'low' ? 'accent' : 'warn'} testid="approval-risk">{RISK[risk]}</Label>{/if}
	</div>
	<h2 id={titleId}>{ask.what}</h2>
	<!-- What would run, never folded: the headline is words, and only a server card's words are vouched for. -->
	{#if ask.action}<p class="action" data-testid="approval-action">Runs <code>{ask.action}</code></p>{/if}
	<div class="lines" id="{titleId}-why">
		{#if ask.why}<p>{ask.why}</p>{/if}
		{#if ask.impact}<p class="impact">{ask.impact}</p>{/if}
	</div>
	<!-- A settings change: what it is now and what it would become, never folded (T-1317). -->
	{#if ask.change}<ChangeRows secret={ask.change.secret} before={ask.change.before} after={ask.change.after} testid="approval" />{/if}

	{#if ask.kind === 'clarify'}
		{#if ask.options.length}
			<div class="options">
				{#each ask.options as option (option)}
					<Button tone="ghost" disabled={busy} onclick={() => onanswer(ask, 'once', option)}>{option}</Button>
				{/each}
			</div>
		{/if}
		<form
			class="typed"
			onsubmit={(e) => {
				e.preventDefault();
				if (typed.trim()) onanswer(ask, 'once', typed.trim());
			}}
		>
			<input aria-label="Your answer" placeholder="Or type an answer" bind:value={typed} disabled={busy} />
			<Button tone="primary" type="submit" disabled={busy || !typed.trim()}>Answer</Button>
		</form>
		<Button tone="quiet" disabled={busy} onclick={() => onanswer(ask, 'deny')}>Skip</Button>
	{:else}
		<div class="verbs">
			{#if ask.onPhone}
				<!-- The adapter takes this yes only with the phone's signature; a button here would be a lie. -->
				<p class="phone" data-testid="approval-on-phone">Confirm with your fingerprint on your phone.</p>
			{:else if ask.undoable}
				<Button tone="primary" disabled={busy} onclick={yes}>Approve</Button>
			{:else}
				<HoldButton label={verb} tone="warn" disabled={busy} onconfirm={yes} testid="approval-hold" />
			{/if}
			<Button tone="ghost" disabled={busy} onclick={() => onanswer(ask, 'deny')}>Deny</Button>
			<!-- Not on a phone ask: its deny goes to the phone's link, which takes no text. -->
			{#if !ask.onPhone}<Button tone="ghost" disabled={busy} onclick={() => onanswer(ask, 'deny', WHY)}>Ask why</Button>{/if}
		</div>
		{#if batch.length && onbatch}
			<Button tone="primary" disabled={busy} onclick={() => onbatch(batch)}>Approve all {batch.length}</Button>
		{/if}
		{#if ask.detail}
			<!-- Folded only behind a server card; anything else shows its parameters at once. -->
			{#if ask.carded}
				<Button tone="quiet" aria-expanded={showing} onclick={() => (showing = !showing)}>{showing ? 'Hide the detail' : 'Show me first →'}</Button>
			{/if}
			{#if showing || !ask.carded}<pre class="detail" data-testid="approval-detail">{ask.detail}</pre>{/if}
		{/if}
		{#if ask.rules.length > 1}
			<!-- On a phone the rule waits behind "Show me first" with the detail, so the common card
			     fits one screen (the owner, 2026-11-08); with no detail to show it is always there. -->
			<div class="rule" data-later={ask.detail && ask.carded && !showing ? '' : undefined}>
				<Label>Next time</Label>
				<Segmented label="Next time" options={ask.rules.map((r) => ({ value: r.choice, label: r.label }))} value={rule} disabled={busy} onchange={(v) => (rule = v)} />
			</div>
		{/if}
	{/if}
</div>

<style>
	.card {
		position: relative;
		display: grid;
		gap: var(--jv-space-3);
		padding: var(--jv-space-5);
		background: var(--jv-projection-backing);
		border-inline-start: 1px solid var(--jv-line);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
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
	@media (max-width: 720px) {
		.rule[data-later] {
			display: none;
		}
	}
	.cap {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-1) var(--jv-space-3);
		align-items: baseline;
	}
	h2 {
		margin: 0;
		font-family: var(--jv-font-display);
		font-size: var(--jv-fs-xl);
		font-weight: var(--jv-weight-display);
		line-height: 1.2;
		color: var(--jv-text-bright);
	}
	.lines {
		display: grid;
		gap: var(--jv-space-1);
		font-size: var(--jv-fs-sm);
		color: var(--jv-text-dim);
	}
	.lines p {
		margin: 0;
	}
	.impact {
		color: var(--jv-text);
	}
	.action {
		margin: 0;
		font-size: var(--jv-fs-xs);
		color: var(--jv-text-dim);
		overflow-wrap: anywhere;
	}
	.action code {
		font-family: var(--jv-font-chrome);
		color: var(--jv-text-bright);
	}
	.phone {
		margin: 0;
		font-size: var(--jv-fs-sm);
		color: var(--jv-warn);
	}
	.verbs,
	.options,
	.typed {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
		align-items: center;
	}
	.verbs > :global(:first-child) {
		flex: 1;
	}
	.typed input {
		flex: 1;
		min-inline-size: 0;
		min-block-size: var(--jv-touch);
		padding: 0 var(--jv-space-3);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		background: transparent;
		color: var(--jv-text);
		font: inherit;
	}
	.typed input:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	.detail {
		margin: 0;
		max-block-size: var(--jv-measure-card);
		overflow: auto;
		padding: var(--jv-space-2) var(--jv-space-3);
		border: 1px solid var(--jv-line-hair);
		border-radius: var(--jv-radius-sm);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-xs);
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		color: var(--jv-text-dim);
	}
	.rule {
		display: grid;
		gap: var(--jv-space-2);
		padding-block-start: var(--jv-space-3);
		border-block-start: 1px solid var(--jv-line-hair);
	}
</style>
