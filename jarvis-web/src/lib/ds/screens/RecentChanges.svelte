<!--
@component
Recent changes on the Settings screen (T-0700): the settings that were changed
by asking, newest first, each saying when, who, the key and the value before
and after (`ChangeRows`, the two rows the approval cards draw), with Put back
on the newest change of a key. The list is the settings plugin's own log
(`jsettings history`); the rows and the secret rule are `changeRows` in
`settings-proposals.ts`.

Put back hands the key to the shell, which sends `jsettings undo` and says what
came of it in the status line: a security setting is not put back from here, it
becomes a proposal under Approvals.

Does not cover: more than one level (the plugin keeps a key's last value, so
Put back on the row an undo made is the redo); a secret, which is never put
back; the house's own configuration, which is not in this log; a change made
while the link is down, which shows once it is back.
-->
<script lang="ts">
	import { untrack } from 'svelte';

	import { wornName } from '$lib/ds/theme.svelte';
	import Button from '$lib/ds/ui/Button.svelte';
	import State from '$lib/ds/ui/State.svelte';
	import ChangeRows from './ChangeRows.svelte';
	import Section from './Section.svelte';
	import { changeRows } from './settings-proposals';

	interface Props {
		/** The plugin's two verbs; undefined while the link is down. `undo` says its own outcome and never throws. */
		io?: { load: () => Promise<unknown>; undo: (key: string) => Promise<void> };
		/** Bumped by the shell on every `settings.changed`, so the list reads again. */
		version?: number;
		hidden?: boolean;
	}
	let { io, version = 0, hidden = false }: Props = $props();

	/** A 24-grid stroke path: a clock wound back. */
	const ICON = 'M4 12a8 8 0 1 0 2.5-5.8M4 4v4h4M12 8v4l3 2';
	/** Rows drawn before "Show all": Settings is held to a short screen (`e2e/v2-screens.spec.ts`, DECISIONS row 137). */
	const FEW = 3;

	/** The plugin's answer; null until it has answered once. */
	let answer = $state<unknown>(null);
	let error = $state('');
	let all = $state(false);
	/** The key whose undo is on the wire; nothing else is sent meanwhile. */
	let acting = $state('');
	const rows = $derived(answer === null ? null : changeRows(answer, wornName()));
	const shown = $derived(!io ? 'offline' : error ? 'error' : rows === null ? 'loading' : rows.length ? 'ready' : 'empty');

	// A read that lands out of turn is dropped, or an older list would cover a newer one.
	let turn = 0;
	/** Quiet keeps what is drawn while it reads, and through a failed read. */
	async function read(quiet: boolean): Promise<void> {
		if (!io) return;
		const mine = ++turn;
		if (!quiet) {
			answer = null;
			error = '';
		}
		try {
			const got = (await io.load()) ?? [];
			if (mine === turn) {
				answer = got;
				error = '';
			}
		} catch (e) {
			if (mine === turn && !quiet) error = e instanceof Error ? e.message : String(e);
		}
	}
	// On mount, each time the link comes back, and on every change the shell hears.
	$effect(() => {
		void version;
		if (io) untrack(() => void read(answer !== null));
	});

	async function putBack(key: string): Promise<void> {
		if (acting || !io) return;
		acting = key;
		try {
			await io.undo(key);
		} finally {
			acting = '';
		}
	}
</script>

<Section title="Recent changes" icon={ICON} {hidden} testid="settings-changes">
	<State status={shown} empty="No setting has been changed by asking yet." {error} onretry={io ? () => void read(false) : undefined} testid="settings-changes-state">
		<ul class="changes">
			<!-- Keyed by place: one key can be changed twice within the stamp's second. -->
			{#each (rows ?? []).slice(0, all ? undefined : FEW) as r, i (i)}
				<li data-testid="change-row" data-key={r.key} aria-busy={acting === r.key}>
					<div class="head">
						<span class="name">{r.key}</span>
						<span class="meta" title={r.stamp} data-testid="change-who">{r.when}{r.when ? ' · ' : ''}{r.who}</span>
					</div>
					<div class="values"><ChangeRows past secret={r.secret} before={r.before} after={r.after} testid="change" /></div>
					{#if r.undoable}
						<span class="verb"><Button tone="ghost" disabled={!!acting} aria-label="Put back {r.key}" testid="change-undo" onclick={() => void putBack(r.key)}>Put back</Button></span>
					{/if}
				</li>
			{/each}
		</ul>
		{#if rows && rows.length > FEW && !all}
			<div><Button tone="quiet" testid="changes-all" onclick={() => (all = true)}>Show all {rows.length}</Button></div>
		{/if}
	</State>
</Section>

<style>
	.changes {
		display: grid;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	/* Who and what, the two values, the verb: side by side where there is room, stacked at 390. */
	li {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
		min-block-size: var(--jv-touch);
		padding-block: var(--jv-space-3);
		border-block-end: 1px solid var(--jv-line-hair);
	}
	/* A fixed share, so the values start on one line down the list whether or not a row has its verb. */
	.head {
		display: grid;
		flex: 0 1 40%;
		gap: var(--jv-space-1);
		min-inline-size: 0;
	}
	/* A key is one long word: it breaks rather than push the row wider than a phone. */
	.head .name {
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}
	.values {
		flex: 1 1 calc(var(--jv-space-7) * 3);
		min-inline-size: 0;
	}
	/* At the row's end, on its own line at 390 as on the row at 1440. */
	.verb {
		margin-inline-start: auto;
	}
</style>
