<!--
@component
First run (the owner, 2026-09-10), on the v2 design system: shown once, while
the console has never had a password and nobody said "later". The owner's name
(what JARVIS calls them and enrols their voice under) and the console password
(which locks the console and pairs the phone and the desktop), then on to the
voice if they like.

The password goes to `POST /api/console` with the name; the owner is then
named at the owner's level through `POST /api/voice/speaker/people`, under the
session that line just opened. "Later" marks first run done (`skip`), so the
sheet does not come back on every load.

Does not cover: changing the password afterwards (Settings → Account), or a
refusal from the house when naming the owner, which is said and left to
Settings → People.
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import Label from '../Label.svelte';
	import Button from '../ui/Button.svelte';
	import Field from '../ui/Field.svelte';
	import Gate from '../ui/Gate.svelte';

	interface Props {
		/** The owner is named and the password chosen; `enrol` is whether to go on to the voice. */
		onready: (name: string, enrol: boolean) => void;
		onlater: () => void;
		/** From `GET /api/console`: the shortest password the console takes. */
		minChars?: number;
	}
	let { onready, onlater, minChars = 10 }: Props = $props();

	let name = $state('');
	let password = $state('');
	let again = $state('');
	let enrol = $state(true);
	let busy = $state(false);
	let said = $state('');

	const problem = $derived.by(() => {
		if (!name.trim()) return 'Your name is what the house calls you and enrols your voice under.';
		if (password.length < minChars) return `The console password is at least ${minChars} characters.`;
		if (again !== password) return 'The two passwords differ.';
		return '';
	});

	async function go(e: SubmitEvent): Promise<void> {
		e.preventDefault();
		if (busy || problem) return;
		busy = true;
		said = '';
		const label = name.trim().split(/\s+/).join(' ');
		try {
			const res = await fetch('/api/console', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ password, name: label })
			});
			if (!res.ok) {
				const body = (await res.json().catch(() => ({}))) as { message?: string };
				throw new Error(body.message || `the console answered ${res.status}`);
			}
			// The owner, by name, at the owner's level. A refusal here is not the
			// end of setup: the password exists and People can name them later.
			const who = await fetch('/api/voice/speaker/people', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ label, level: 'owner' })
			});
			if (!who.ok) {
				const body = (await who.json().catch(() => ({}))) as { detail?: string; message?: string };
				said = `The password is set, but the house could not name you: ${body.detail || body.message || who.status}. Add yourself under Settings → People.`;
				onready(label, false);
				return;
			}
			password = '';
			again = '';
			onready(label, enrol);
		} catch (err) {
			said = err instanceof Error ? err.message : String(err);
		} finally {
			busy = false;
		}
	}

	async function later(): Promise<void> {
		if (busy) return;
		busy = true;
		try {
			await fetch('/api/console', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ skip: true })
			});
		} catch {
			/* the sheet closes either way; it comes back next load if the mark did not land */
		} finally {
			busy = false;
			onlater();
		}
	}
</script>

<Gate label="Set up {wornName()}" testid="setup-screen">
	<form class="form" onsubmit={go}>
		<Label>First run</Label>
		<h1>Who is this house’s owner?</h1>
		<p class="hint">
			Your name is what {wornName()} calls you and enrols your voice under. The console password locks this screen and pairs the phone and the desktop; it never leaves this
			server.
		</p>
		<Field label="Your name" placeholder="Your name" autocomplete="name" maxlength={40} bind:value={name} autofocus testid="setup-name" />
		<Field label="Console password" placeholder="Console password" type="password" autocomplete="new-password" bind:value={password} testid="setup-password" />
		<Field label="Console password again" placeholder="Again" type="password" autocomplete="new-password" bind:value={again} testid="setup-again" />
		<label class="check">
			<input type="checkbox" bind:checked={enrol} data-testid="setup-enrol" />
			<span>Enrol my voice now, so {wornName()} answers me and not the television</span>
		</label>
		<div class="row">
			<Button type="submit" tone="primary" disabled={busy || Boolean(problem)} testid="setup-go">Set up</Button>
			<Button disabled={busy} testid="setup-later" onclick={() => void later()}>Later</Button>
		</div>
		<!-- A problem waits for something typed: before that it repeats the hint above. -->
		<p class="said" role="status" data-testid="setup-said">{said || (name || password || again ? problem : '')}</p>
	</form>
</Gate>

<style>
	.form {
		display: grid;
		gap: var(--jv-space-3);
	}
	h1 {
		margin: 0;
		color: var(--jv-text-bright);
		font: var(--jv-fs-2xl) var(--jv-font-display);
	}
	.hint,
	.said {
		margin: 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.said {
		min-block-size: var(--jv-touch);
		color: var(--jv-text);
	}
	.check {
		display: flex;
		align-items: center;
		gap: var(--jv-space-2);
		min-block-size: var(--jv-touch);
		cursor: pointer;
	}
	.check input {
		inline-size: var(--jv-space-5);
		block-size: var(--jv-space-5);
		accent-color: var(--jv-accent);
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
</style>
