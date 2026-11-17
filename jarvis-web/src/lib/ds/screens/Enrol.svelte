<!--
@component
Enrolling a voice, on the v2 design system: a sheet over whatever screen opened
it (first run, or Settings → People). The house's phrases, one at a time, each
recorded from the microphone as 16 kHz 16-bit mono and sent to
`POST /api/voice/speaker/enrol`; each line is marked accepted or refused with
the house's reason, and the sheet says when the profile meets the minimum.

Does not cover: deleting a voice (People → Forget), or noise the house accepts
that a person would not call their voice.
-->
<script lang="ts">
	import { PcmCapture, durationMs } from '$lib/audio/pcm';
	import { thisSurface } from '$lib/hud/voice.svelte';
	import {
		beginSession,
		meetsMinimum,
		progress,
		rejectLocally,
		remaining,
		startRecording,
		startSending,
		withAccepted,
		withRejected,
		writeQuery,
		type EnrolmentSession,
		type SpeakerStatus
	} from '$lib/enrolment';
	import Button from '../ui/Button.svelte';
	import Sheet from '../ui/Sheet.svelte';

	interface Props {
		/** Whose voice: the person's name, as People has it. */
		label: string;
		/** Closed, with whether the profile now meets the minimum. */
		onclose: (enrolled: boolean) => void;
	}
	let { label, onclose }: Props = $props();

	let session = $state<EnrolmentSession | null>(null);
	let said = $state('');
	let recording = $state(false);
	let busy = $state(false);
	let open = $state(true);
	const capture = new PcmCapture();

	/** The phrase list and the minimum, from the house; the mic is let go on close. */
	$effect(() => {
		let live = true;
		void (async () => {
			try {
				const res = await fetch(`/api/voice/speaker?label=${encodeURIComponent(label)}`);
				if (!res.ok) throw new Error(`the house answered ${res.status}`);
				const status = (await res.json()) as SpeakerStatus;
				if (!live) return;
				session = beginSession(status);
				if (!session.slots.length) said = 'The house has no enrolment phrases to read.';
			} catch (e) {
				if (live) said = `Could not reach the house: ${e instanceof Error ? e.message : String(e)}`;
			}
		})();
		return () => {
			live = false;
			capture.release();
		};
	});

	const current = $derived(session ? session.slots[session.at] : null);
	const enrolled = $derived(session ? meetsMinimum(session) : false);

	async function press(): Promise<void> {
		if (!session || busy) return;
		if (!recording) {
			try {
				// The house is told a phrase is about to be read, so its own
				// listeners do not take the sentence for a command (M79).
				void fetch('/api/voice/speaker/enrolling', { method: 'POST' }).catch(() => {});
				await capture.start();
				recording = true;
				session = startRecording(session);
				said = '';
			} catch {
				said = 'The microphone is unavailable.';
			}
			return;
		}
		recording = false;
		const index = session.at;
		const pcm = await capture.stop();
		const problem = rejectLocally(pcm);
		if (problem) {
			session = withRejected(session, index, problem);
			return;
		}
		busy = true;
		session = startSending(session, index, durationMs(pcm.length));
		try {
			const res = await fetch(`/api/voice/speaker/enrol${writeQuery(label, thisSurface())}`, {
				method: 'POST',
				headers: { 'content-type': 'application/octet-stream' },
				body: pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength) as ArrayBuffer
			});
			if (res.ok) session = withAccepted(session, index);
			else {
				const body = (await res.json().catch(() => ({}))) as { detail?: string; message?: string };
				session = withRejected(session, index, body.detail || body.message || `the house answered ${res.status}`);
			}
		} catch {
			session = withRejected(session, index, 'the house did not answer');
		} finally {
			busy = false;
		}
	}
	const MARK = { accepted: '✓', rejected: '✕' } as Record<string, string>;
</script>

<Sheet bind:open label="Enrolling {label}" testid="enrol-screen" onclose={() => onclose(enrolled)}>
	{#if session && current}
		<p class="hint">
			Read each line in your own voice, at the distance you usually speak from.
			{remaining(session) > 0 ? `${remaining(session)} more to go.` : 'That is enough; more widens the profile.'}
		</p>
		<p class="phrase" data-testid="enrol-phrase">“{current.prompt}”</p>
		<div class="bar" aria-hidden="true"><i style:transform="scaleX({progress(session)})"></i></div>
		<ol class="slots">
			{#each session.slots as slot, i (i)}
				<li data-state={slot.state} aria-current={i === session.at ? 'step' : undefined}>
					<span aria-hidden="true">{MARK[slot.state] ?? (i === session.at ? '›' : '·')}</span>
					{slot.prompt}
					{#if slot.detail}<span class="detail">: {slot.detail}</span>{/if}
				</li>
			{/each}
		</ol>
		<div class="row">
			<Button tone="primary" disabled={busy} testid="enrol-record" onclick={() => void press()}>{recording ? 'Stop' : busy ? 'Sending…' : 'Record this line'}</Button>
			<Button testid="enrol-done" onclick={() => (open = false)}>{enrolled ? 'Done' : 'Later'}</Button>
		</div>
	{:else}
		<p class="hint" role="status">{said || 'Asking the house for the phrases…'}</p>
		<div class="row"><Button testid="enrol-done" onclick={() => (open = false)}>Close</Button></div>
	{/if}
	{#if said && session}
		<p class="hint" role="status" data-testid="enrol-said">{said}</p>
	{/if}
</Sheet>

<style>
	.hint {
		margin: 0 0 var(--jv-space-3);
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.phrase {
		margin: 0 0 var(--jv-space-3);
		color: var(--jv-text-bright);
		font: var(--jv-fs-xl) var(--jv-font-display);
	}
	.bar {
		block-size: 1px;
		margin-block-end: var(--jv-space-3);
		background: var(--jv-line-hair);
	}
	.bar i {
		display: block;
		block-size: 100%;
		background: var(--jv-accent);
		transform-origin: left;
		transition: transform var(--jv-dur-fast) var(--jv-ease-out);
	}
	.slots {
		display: grid;
		gap: var(--jv-space-1);
		margin: 0 0 var(--jv-space-3);
		padding: 0;
		list-style: none;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	.slots [data-state='accepted'] {
		color: var(--jv-ok);
	}
	.slots [data-state='rejected'],
	.detail {
		color: var(--jv-danger-text);
	}
	.slots [aria-current='step'] {
		color: var(--jv-text-bright);
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--jv-space-2);
	}
	@media (prefers-reduced-motion: reduce) {
		.bar i {
			transition: none;
		}
	}
</style>
