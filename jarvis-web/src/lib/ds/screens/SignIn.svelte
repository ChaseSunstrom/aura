<!--
@component
The console password, once, for every setting behind it (the owner,
2026-09-25: "it should be like that for ALL settings, initially, instead of
asking every time"). Signed in, enrolling a voice and pairing work for a month
on this browser (`consoleAuth.ts`); with no password set, the same row
chooses one. The password goes to `POST /api/console`; the session is an
httpOnly cookie the page never holds.

Signed in, this is also where passkeys live (T-0946): the ones on this console,
one added from this device, one removed. A passkey is a second key to the same
door; the password stays.

Does not cover: the lock screen (which signs in the same way, then unlocks).
-->
<script lang="ts">
	import { onMount } from 'svelte';

	import Button from '$lib/ds/ui/Button.svelte';
	import Field from '$lib/ds/ui/Field.svelte';
	import State from '$lib/ds/ui/State.svelte';
	import { addPasskey, readPasskeys, removePasskey, signInWithPasskey, type PasskeyState } from '$lib/hud/passkey';
	import Label from '../Label.svelte';

	interface Props {
		onchange?: (signedIn: boolean) => void;
	}
	let { onchange }: Props = $props();

	let known = $state<'loading' | 'ready' | 'offline'>('loading');
	let configured = $state(true);
	let signedIn = $state(false);
	let minChars = $state(10);
	let password = $state('');
	let said = $state('');
	let busy = $state(false);
	let passkeys = $state<PasskeyState | null>(null);
	let passkeyName = $state('');
	let passkeySaid = $state('');
	const added = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });

	async function readKeys(): Promise<void> {
		passkeys = await readPasskeys().catch(() => null);
	}
	async function usePasskey(): Promise<void> {
		if (busy) return;
		busy = true;
		said = '';
		const refused = await signInWithPasskey();
		busy = false;
		if (refused !== null) return void (said = refused);
		signedIn = true;
		onchange?.(true);
		void readKeys();
	}
	async function add(e: SubmitEvent): Promise<void> {
		e.preventDefault();
		if (busy) return;
		busy = true;
		passkeySaid = '';
		const refused = await addPasskey(passkeyName);
		busy = false;
		if (refused !== null) return void (passkeySaid = refused);
		passkeyName = '';
		passkeySaid = 'Added. It opens the console from this device.';
		void readKeys();
	}
	async function remove(id: string): Promise<void> {
		passkeySaid = (await removePasskey(id).catch(() => false)) ? 'Removed.' : 'That passkey could not be removed.';
		void readKeys();
		// Removing the passkey this browser signed in with ends this session too: say so now,
		// not when the socket closes half a minute later.
		void read();
	}

	async function read(): Promise<void> {
		try {
			const res = await fetch('/api/console');
			if (!res.ok) throw new Error(String(res.status));
			const body = (await res.json()) as { configured: boolean; authenticated: boolean; minChars: number };
			configured = body.configured;
			signedIn = body.authenticated;
			minChars = body.minChars;
			known = 'ready';
			if (configured) void readKeys();
		} catch {
			known = 'offline';
		}
	}
	async function signIn(e: SubmitEvent): Promise<void> {
		e.preventDefault();
		if (!password || busy) return;
		busy = true;
		said = '';
		try {
			const res = await fetch('/api/console', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ password }) });
			password = '';
			if (res.ok) {
				signedIn = true;
				configured = true;
				onchange?.(true);
				void readKeys();
			} else {
				const body = (await res.json().catch(() => ({}))) as { message?: string };
				said = body.message || (res.status === 429 ? 'Too many attempts. Wait a moment.' : 'That is not the console password.');
			}
		} catch {
			said = 'The console did not answer. Try again in a moment.';
		} finally {
			busy = false;
		}
	}
	async function signOut(): Promise<void> {
		await fetch('/api/console', { method: 'DELETE' }).catch(() => undefined);
		signedIn = false;
		onchange?.(false);
	}
	let showKeys = $state(false);
	onMount(() => void read());
</script>

<State status={known} onretry={() => void read()} testid="signin-state">
	{#if signedIn}
		<div class="row">
			<p class="note" data-testid="console-signed-in">Signed in. Every setting is open for a month on this browser.</p>
			<!-- Closed until asked for or until one exists: Settings is held to 2.4 screens
			     (e2e/v2-screens.spec.ts) and the open block put AURA's at 2.55. -->
			{#if configured && passkeys && !passkeys.passkeys.length}
				<Button tone="ghost" aria-expanded={showKeys} testid="passkeys-open" onclick={() => (showKeys = !showKeys)}>Passkeys</Button>
			{/if}
			<Button tone="ghost" testid="console-sign-out" onclick={() => void signOut()}>Sign out</Button>
		</div>
		{#if configured && passkeys && (showKeys || passkeys.passkeys.length)}
			<div class="keys" data-testid="passkeys">
				<Label>Passkeys</Label>
				{#if passkeys.passkeys.length}
					<ul>
						{#each passkeys.passkeys as key (key.id)}
							<li>
								<span class="name">{key.name}</span>
								<span class="when">{added.format(key.added)}</span>
								<Button tone="ghost" aria-label="Remove the passkey {key.name}" testid="passkey-remove" onclick={() => void remove(key.id)}>Remove</Button>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="note">None yet. A passkey opens the console with this device's fingerprint, face or PIN; the password still works.</p>
				{/if}
				{#if passkeys.usable}
					<form class="row" onsubmit={add}>
						<Field label="A name for this device's passkey" placeholder="Laptop" maxlength={40} bind:value={passkeyName} disabled={busy} testid="passkey-name" />
						<Button type="submit" disabled={busy} testid="passkey-add">Add a passkey</Button>
					</form>
				{:else}
					<p class="note" data-testid="passkey-why">{passkeys.why}</p>
				{/if}
			</div>
		{/if}
	{:else}
		<form class="row" onsubmit={signIn}>
			<Field
				type="password"
				autocomplete={configured ? 'current-password' : 'new-password'}
				placeholder={configured ? 'Console password' : `Choose a password (${minChars}+ characters)`}
				label={configured ? 'Console password' : 'Choose a console password'}
				bind:value={password}
				disabled={busy}
				testid="console-password"
			/>
			<Button tone="primary" type="submit" disabled={busy || !password} testid="console-sign-in">{configured ? 'Sign in' : 'Set password'}</Button>
			{#if passkeys?.registered && passkeys.usable}
				<Button disabled={busy} testid="console-passkey" onclick={() => void usePasskey()}>Use a passkey</Button>
			{/if}
		</form>
		{#if said}<p class="said" role="alert">{said}</p>{/if}
	{/if}
	<!-- Outside both: removing the passkey this browser signed in with signs it out, and the
	     line that says it was removed has to outlive that. -->
	{#if passkeySaid}<p class="note" role="status" data-testid="passkey-said">{passkeySaid}</p>{/if}
</State>

<style>
	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-2) var(--jv-space-4);
	}
	.note {
		flex: 1 1 var(--jv-measure-card);
		margin: 0;
		color: var(--jv-text-dim);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.keys {
		display: grid;
		gap: var(--jv-space-2);
		margin-top: var(--jv-space-2);
	}
	.keys ul {
		margin: 0;
		padding: 0;
		list-style: none;
	}
	.keys li {
		display: flex;
		align-items: center;
		gap: var(--jv-space-4);
		padding: var(--jv-space-1) 0;
		border-bottom: 1px solid var(--jv-line-hair);
	}
	.name {
		flex: 1 1 auto;
		min-width: 0;
		overflow-wrap: anywhere;
		color: var(--jv-text);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
	.when {
		color: var(--jv-text-faint);
		font: var(--jv-fs-xs) var(--jv-font-body);
		font-variant-numeric: tabular-nums;
	}
	.said {
		margin: var(--jv-space-2) 0 0;
		color: var(--jv-warn);
		font: var(--jv-fs-sm) var(--jv-font-body);
	}
</style>
