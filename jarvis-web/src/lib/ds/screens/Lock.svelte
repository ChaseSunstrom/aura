<!--
@component
The lock (the owner, 2026-09-10), on the v2 design system: after a quiet spell
the console is behind this until the console password or an enrolled voice
opens it. What it is and is not (the lock on the study door, not a security
boundary) is said in `lib/hud/lock.ts`. The legacy one frosted the room behind
it; this one is the console's own dark, with nothing showing through.

A passkey is offered first where one exists and this browser can use it
(T-0946: `POST /api/console/passkey`, a fingerprint or the device's PIN);
where it cannot, one line says why and the password is what is shown. The
password goes to `POST /api/console`, the same check Settings → Account
makes, and its refusals are shown as they come (wrong; too many attempts,
wait). The voice goes to `POST /api/voice/speaker/verify`: three seconds from
the microphone as 16 kHz 16-bit mono, the shape enrolment writes, read as
opened, refused, a guest, or "say it again".

Does not cover: a voice opening a signed-out browser (a voice opens no
session; the host passes `voice={false}` then).
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { PcmCapture } from '$lib/audio/pcm';
	import { writeQuery } from '$lib/enrolment';
	import { lockHint, voiceVerdict } from '$lib/hud/lock';
	import { readPasskeys, signInWithPasskey, type PasskeyState } from '$lib/hud/passkey';
	import Label from '../Label.svelte';
	import Button from '../ui/Button.svelte';
	import Field from '../ui/Field.svelte';
	import Gate from '../ui/Gate.svelte';

	interface Props {
		/** A console password exists; without one the lock cannot hold (the hint says so). */
		configured: boolean;
		/** Voice unlock is on (the preference); whether anyone is enrolled is asked here. */
		voice?: boolean;
		/** Who is primary: the name on the screen. */
		name?: string;
		/** Opened: by the password (no name), or by a recognised voice (that person's name). */
		onunlock: (by?: string | null) => void;
	}
	let { configured, voice = false, name = `${wornName()}`, onunlock }: Props = $props();

	let password = $state('');
	let busy = $state(false);
	let said = $state('');
	let listening = $state(false);
	/** Somebody is enrolled, so the voice can open the lock: asked of the house on mount. */
	let enrolled = $state(false);
	const byVoice = $derived(voice && enrolled);
	/** What the console and this browser say about passkeys; null until asked, or unasked. */
	let passkeys = $state<PasskeyState | null>(null);
	const byPasskey = $derived(Boolean(passkeys?.registered && passkeys.usable));

	$effect(() => {
		if (!configured) return;
		let live = true;
		void readPasskeys()
			.then((state) => live && (passkeys = state))
			.catch(() => live && (passkeys = null));
		return () => (live = false);
	});

	async function tryPasskey(): Promise<void> {
		if (busy) return;
		busy = true;
		said = '';
		const refused = await signInWithPasskey();
		busy = false;
		if (refused === null) onunlock();
		else said = refused;
	}

	$effect(() => {
		if (!voice) return;
		let live = true;
		void fetch('/api/voice/speaker')
			.then((res) => res.json() as Promise<{ enrolled?: boolean; people?: unknown[] }>)
			.then((body) => live && (enrolled = Boolean(body.enrolled) || (Array.isArray(body.people) && body.people.length > 0)))
			.catch(() => live && (enrolled = false));
		return () => (live = false);
	});

	async function tryPassword(e: SubmitEvent): Promise<void> {
		e.preventDefault();
		if (busy || !password) return;
		busy = true;
		said = '';
		try {
			const res = await fetch('/api/console', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ password })
			});
			if (res.ok) {
				password = '';
				onunlock();
				return;
			}
			const body = (await res.json().catch(() => ({}))) as { message?: string };
			said = body.message || (res.status === 429 ? 'Too many attempts. Wait a moment.' : 'That is not the console password.');
		} catch {
			said = 'The console did not answer.';
		} finally {
			busy = false;
			password = '';
		}
	}

	/** Three seconds from the microphone as 16-bit PCM at the capture rate. */
	async function listen(): Promise<ArrayBuffer | null> {
		const capture = new PcmCapture();
		try {
			await capture.start();
			await new Promise((r) => setTimeout(r, 3000));
			const pcm = await capture.stop();
			return pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength) as ArrayBuffer;
		} catch {
			capture.release();
			return null;
		}
	}

	async function tryVoice(): Promise<void> {
		if (busy || listening) return;
		listening = true;
		said = 'Listening…';
		const sample = await listen();
		listening = false;
		if (!sample) {
			said = 'The microphone is unavailable.';
			return;
		}
		busy = true;
		try {
			const res = await fetch(`/api/voice/speaker/verify${writeQuery()}`, {
				method: 'POST',
				headers: { 'content-type': 'application/octet-stream' },
				body: sample
			});
			const body = res.ok ? await res.json().catch(() => null) : null;
			const verdict = voiceVerdict(body);
			if (verdict === 'open') {
				onunlock(body?.verdict?.label ?? null);
				return;
			}
			said =
				verdict === 'unclear'
					? 'I did not catch that. Say a sentence.'
					: verdict === 'guest'
						? `${body?.verdict?.label ?? 'That voice'} is a guest here, and a guest cannot open the console.`
						: 'That is not an enrolled voice.';
		} catch {
			said = 'The verifier did not answer.';
		} finally {
			busy = false;
		}
	}
</script>

<Gate label="Locked" testid="lock-screen">
	<Label>{name}</Label>
	<h1>Locked</h1>
	<p class="hint">{lockHint(configured, byVoice, byPasskey)}</p>
	{#if byPasskey}
		<div class="row">
			<Button tone="primary" disabled={busy} testid="lock-passkey" onclick={() => void tryPasskey()}>Use a passkey</Button>
		</div>
	{:else if passkeys?.registered && passkeys.why}
		<p class="hint" data-testid="lock-passkey-why">{passkeys.why}</p>
	{/if}
	<form class="row" onsubmit={tryPassword}>
		<Field
			label="Console password"
			type="password"
			autocomplete="current-password"
			placeholder="Console password"
			bind:value={password}
			disabled={busy || !configured}
			autofocus={!byPasskey}
			testid="lock-password"
		/>
		<Button type="submit" tone={byPasskey ? undefined : 'primary'} disabled={busy || !configured || !password} testid="lock-unlock">Unlock</Button>
		{#if byVoice}
			<Button disabled={busy || listening} testid="lock-voice" onclick={() => void tryVoice()}>{listening ? 'Listening…' : 'Use my voice'}</Button>
		{/if}
	</form>
	{#if said}
		<p class="said" role="status" data-testid="lock-said">{said}</p>
	{/if}
</Gate>

<style>
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
		color: var(--jv-text);
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
</style>
