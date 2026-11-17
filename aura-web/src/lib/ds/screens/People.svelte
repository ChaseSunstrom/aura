<!--
@component
Settings → People and voices, on the v2 design system: everyone the house knows
by name, their level (owner, member, guest), whether their voice is enrolled,
and rename, enrol and forget; and a name to add. Read from and written to
jarvis-core through `/api/voice/speaker` (the console's relay; GET for a
signed-in console, writes under the console-password session).

A new name keeps the voice and the level (the owner, 2026-09-25: "I cant edit
the names of enrolled voices"). A refusal is said under the list, never as a
toast.

Does not cover: the voiceprints themselves (the house keeps them; nothing here
reads one), or who may change a level (the house refuses a non-owner).
-->
<script lang="ts">
	import { cleanLabel, labelProblem, type SpeakerStatus } from '$lib/enrolment';
	import Button from '../ui/Button.svelte';
	import Field from '../ui/Field.svelte';
	import Segmented from '../ui/Segmented.svelte';
	import State from '../ui/State.svelte';

	interface Person {
		label: string;
		level?: string | null;
		enrolled?: boolean;
		samples?: number;
	}

	interface Props {
		/** Open the enrolment sheet for this person; the host owns it. */
		onenrol: (label: string) => void;
		/** Bumped by the host when an enrolment closed, so the list re-reads. */
		version?: number;
	}
	let { onenrol, version = 0 }: Props = $props();

	let status = $state<(SpeakerStatus & { levels?: string[]; people?: Person[] }) | null>(null);
	let failed = $state('');
	let name = $state('');
	let busy = $state('');
	let said = $state('');
	let renaming = $state('');
	let newName = $state('');

	/** The house's words are lower case ids; a choice is a word in sentence case. */
	const LEVEL_WORD: Record<string, string> = { owner: 'Owner', member: 'Member', guest: 'Guest' };
	const levels = $derived((status?.levels ?? ['owner', 'member', 'guest']).map((l) => ({ value: l, label: LEVEL_WORD[l] ?? l })));
	const people = $derived((status?.people ?? []) as Person[]);
	const look = $derived(failed ? 'error' : status === null ? 'loading' : people.length ? 'ready' : 'empty');
	const max = $derived(status?.max_label_chars ?? 40);

	async function read(): Promise<void> {
		try {
			const res = await fetch('/api/voice/speaker');
			if (!res.ok) throw new Error(`the house answered ${res.status}`);
			status = await res.json();
			failed = '';
		} catch (e) {
			failed = e instanceof Error ? e.message : String(e);
		}
	}
	$effect(() => {
		void version;
		void read();
	});

	async function write(what: string, doIt: () => Promise<Response>): Promise<boolean> {
		if (busy) return false;
		busy = what;
		try {
			const res = await doIt();
			if (!res.ok) {
				const body = (await res.json().catch(() => ({}))) as { detail?: string; message?: string };
				throw new Error(body.detail || body.message || `the house answered ${res.status}`);
			}
			status = await res.json();
			said = '';
			return true;
		} catch (e) {
			said = `Couldn’t ${what}: ${e instanceof Error ? e.message : String(e)}`;
			return false;
		} finally {
			busy = '';
		}
	}
	const post = (body: Record<string, string>) =>
		fetch('/api/voice/speaker/people', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

	function rename(from: string): void {
		const problem = labelProblem(newName, status);
		if (problem) {
			said = problem;
			return;
		}
		const to = cleanLabel(newName, status);
		if (to === from) {
			renaming = '';
			return;
		}
		void write(`rename ${from}`, () => post({ label: from, rename: to })).then((ok) => ok && (renaming = ''));
	}
	function add(e: SubmitEvent): void {
		e.preventDefault();
		const problem = labelProblem(name, status);
		if (problem) {
			said = problem;
			return;
		}
		const label = cleanLabel(name, status);
		void write(`add ${label}`, () => post({ label, level: 'member' })).then((ok) => ok && (name = ''));
	}
</script>

<State status={look} error={failed} empty="Nobody is named yet. Add a person, then enrol their voice." onretry={() => void read()} testid="people-state">
	<ul class="rows people">
		{#each people as p (p.label)}
			<li data-testid="person-{p.label}">
				{#if renaming === p.label}
					<form
						class="line"
						onsubmit={(e) => {
							e.preventDefault();
							rename(p.label);
						}}
					>
						<Field label="New name for {p.label}" bind:value={newName} maxlength={max} testid="rename-input-{p.label}" />
						<Button type="submit" tone="primary" disabled={Boolean(busy)} testid="rename-save-{p.label}">Save</Button>
						<Button tone="quiet" testid="rename-cancel-{p.label}" onclick={() => (renaming = '')}>Cancel</Button>
					</form>
				{:else}
					<span class="name">{p.label}</span>
				{/if}
				<span class="meta">{p.enrolled ? `Voice enrolled (${p.samples ?? 0})` : 'No voice yet'}</span>
				<div class="line">
					<Segmented
						label="{p.label}’s level"
						options={levels}
						value={p.level ?? ''}
						disabled={Boolean(busy)}
						testid="level-{p.label}"
						onchange={(level) => void write(`make ${p.label} a ${level}`, () => post({ label: p.label, level }))}
					/>
					<Button tone="quiet" disabled={Boolean(busy)} testid="rename-{p.label}" onclick={() => ((renaming = p.label), (newName = p.label))}>Rename</Button>
					<!-- "Add more", not "Enrol more": the longer word wrapped this row and put Settings past its
					     2.4 screens (v2-screens.spec); enrolling again adds to the voice, it does not replace it (T-0856). -->
					<Button
						testid="enrol-{p.label}"
						title={p.enrolled ? `Add more of ${p.label}'s voice` : undefined}
						aria-label={p.enrolled ? `Add more of ${p.label}'s voice` : undefined}
						onclick={() => onenrol(p.label)}>{p.enrolled ? 'Add more' : 'Enrol voice'}</Button
					>
					<Button
						tone="quiet"
						disabled={Boolean(busy)}
						testid="forget-{p.label}"
						onclick={() => void write(`forget ${p.label}`, () => fetch(`/api/voice/speaker?label=${encodeURIComponent(p.label)}`, { method: 'DELETE' }))}>Forget</Button
					>
				</div>
			</li>
		{/each}
	</ul>
</State>
{#if look !== 'error' && look !== 'loading'}
	<form class="line add" onsubmit={add}>
		<Field label="A new person’s name" placeholder="A name" bind:value={name} maxlength={max} testid="people-name" />
		<Button type="submit" disabled={Boolean(busy) || !name.trim()} testid="people-add">Add a member</Button>
	</form>
{/if}
{#if said}
	<p class="said" role="status" data-testid="people-said">{said}</p>
{/if}

<style>
	.people > li {
		display: grid;
		gap: var(--jv-space-1);
		padding-block: var(--jv-space-2);
	}
	.line {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2);
	}
	.add {
		margin-block-start: var(--jv-space-2);
	}
	.said {
		margin: var(--jv-space-2) 0 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
</style>
