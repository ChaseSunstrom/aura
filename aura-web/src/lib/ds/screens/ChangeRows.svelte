<!--
@component
What a settings change would do, as both cards that ask about one draw it: the
value now over the value it becomes, or, for a secret, one sentence and neither
value. The settings proposal card (`Approvals.svelte`, T-1316) and the approval
card (`shell/ApprovalCard.svelte`, T-1317) both use it, so the two cannot come
to read differently. With `past`, the same two rows say what a change already
made did (Recent changes on the Settings screen, T-0700).

Does not cover: deciding what is a secret (`secretChange` in
`settings-proposals.ts`; the caller passes the answer), or wording a value (the
caller passes text). A secret's `before` and `after` are not read at all.
-->
<script lang="ts">
	interface Props {
		secret: boolean;
		before: string;
		after: string;
		/** The card's own prefix: `proposal` gives `proposal-before`, `proposal-after`, `proposal-secret`. */
		testid: string;
		/** A change already made (Recent changes, T-0700): "From / To" rather than "Now / Becomes". */
		past?: boolean;
	}
	let { secret, before, after, testid, past = false }: Props = $props();
</script>

{#if secret}
	<!-- Neither value, masked or not: a mask's length and a "not set" both say something about a secret. -->
	<p class="secret" data-testid="{testid}-secret">
		{past ? 'A secret: neither value is shown, and it can’t be put back.' :'A secret: it changes to a new value. Neither value is shown, and it can’t be undone.'}
	</p>
{:else}
	<dl>
		<dt>{past ? 'From' : 'Now'}</dt>
		<dd data-testid="{testid}-before">{before}</dd>
		<dt>{past ? 'To' : 'Becomes'}</dt>
		<dd class="after" data-testid="{testid}-after">{after}</dd>
	</dl>
{/if}

<style>
	.secret {
		margin: 0;
		color: var(--jv-text);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	/* One value under the other, so the eye compares them the way it reads a diff. */
	dl {
		display: grid;
		grid-template-columns: max-content minmax(0, 1fr);
		gap: var(--jv-space-2) var(--jv-space-4);
		align-items: baseline;
		margin: 0;
	}
	dt {
		color: var(--jv-text-faint);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-2xs);
		letter-spacing: var(--jv-track-label);
		text-transform: uppercase;
	}
	dd {
		margin: 0;
		/* A long list stays inside the card, at 390 too. */
		max-block-size: var(--jv-measure-card);
		overflow: auto;
		color: var(--jv-text-dim);
		font-family: var(--jv-font-chrome);
		font-size: var(--jv-fs-md);
		overflow-wrap: anywhere;
	}
	dd.after {
		color: var(--jv-text-bright);
		font-size: var(--jv-fs-lg);
		font-weight: var(--jv-weight-strong);
	}
</style>
