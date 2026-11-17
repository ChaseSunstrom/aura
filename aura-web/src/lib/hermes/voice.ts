// Voice over the relay: push-to-talk in, a spoken reply out.
//
// The browser records 16 kHz PCM while the key or the button is held, posts
// it as a WAV data URL to `/hermes/api/audio/transcribe` -- where the adapter
// has the speaker gate hear the same audio -- and submits the transcript as
// the turn, with the gate's ticket when it recognised a voice. A sentence that starts with a goal trigger is a
// goal, not a prompt: "keep working on this until the tests pass" dispatches
// `goal` with the whole sentence, as the protocol document says.
//
// The pure parts — the trigger test, the state the reactor wears, the base64
// plumbing — are here so they run under Vitest; `PushToTalk` is the one class
// that needs a browser.

import { PcmCapture, wavBlob } from '$lib/audio/pcm';

export const GOAL_TRIGGERS = [
	'keep working on',
	'keep going until',
	"don't stop until",
	'get this done',
	'set a goal'
] as const;

/** Curly apostrophes and stray whitespace, so the trigger test is about words. */
function normalise(text: string): string {
	return String(text ?? '')
		.replace(/[‘’ʼ]/g, "'")
		.replace(/\s+/g, ' ')
		.trim()
		.toLowerCase();
}

/** Whether a transcript is a goal to dispatch rather than a prompt to submit. */
export function isGoalPhrase(text: string): boolean {
	const t = normalise(text);
	return GOAL_TRIGGERS.some((trigger) => t.startsWith(trigger));
}

/**
 * The goal to set, from the sentence.
 *
 * "set a goal: tidy the notes" and "set a goal to tidy the notes" mean the
 * goal is what follows; every other trigger IS the goal ("keep going until the
 * build is green" is a fine standing instruction as written).
 */
export function goalFromPhrase(text: string): string {
	const raw = String(text ?? '').trim();
	const t = normalise(raw);
	if (t.startsWith('set a goal')) {
		const rest = raw.slice('set a goal'.length).replace(/^\s*(?:to|of|:|-|—)?\s*/i, '').trim();
		return rest || raw;
	}
	return raw;
}

/** What the page says to an empty transcript, locally, without submitting. */
export const DIDNT_CATCH = "I didn't catch that.";

export function transcriptIsEmpty(transcript: string | null | undefined): boolean {
	return !String(transcript ?? '').trim();
}

/**
 * The name alone, as Whisper writes it ("Hey, Jarvis.", "Jervis", "Hey Aura."): the open mic takes
 * it as the wake, not a turn (T-0769; AURA's name since T-0861, both themes' names so a switch
 * mid-sentence is not missed). Does not cover a wake phrase said with the command after it: that
 * is a turn, and the model knows it is being addressed. Near-misses (Laura, Ora, aurora) are not
 * the name.
 */
const WAKE_ONLY = /^(?:(?:hey|hi|okay|ok)[\s,]+)?(?:jarvis|jervis|javis|aura)[\s.,!?]*$/i;

export function isWakeOnly(transcript: string | null | undefined): boolean {
	return WAKE_ONLY.test(String(transcript ?? '').trim());
}

/**
 * A stop said on its own ("stop", "cancel that", "never mind", with or without the name): the
 * one thing heard mid-turn that ends the turn. Anything else is submitted and Hermes folds it
 * into the turn that is running (T-0794): a remark used to end a long job and start over.
 * Does not cover a stop inside a sentence ("stop the timer"): that is a request, and is folded.
 */
const NAME = '(?:jarvis|jervis|javis|aura)';
const STOP = new RegExp(
	`^(?:(?:hey|okay|ok)[\\s,]+)?(?:${NAME}[\\s,]+)?(?:that's\\s+)?(?:stop|cancel|never\\s?mind|hold on|wait|shut up|be quiet|quiet|enough)(?:[\\s,]+(?:stop|it|that|please|now))*(?:[\\s,]+${NAME})?[\\s.,!?]*$`,
	'i'
);

export function isStop(transcript: string | null | undefined): boolean {
	return STOP.test(String(transcript ?? '').trim());
}

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';

export interface OrbInputs {
	recording: boolean;
	running: boolean;
	gotToken: boolean;
	speaking: boolean;
	warming: boolean;
	error: boolean;
}

/**
 * Which state the reactor wears.
 *
 * An error outranks everything; recording is listening; a reply being played
 * is speaking; a turn with no token yet — the model warming up included — is
 * thinking; a turn that is streaming text is still thinking on the instrument
 * (the text is the motion there). Warming shows as thinking with the pill
 * saying why: the instrument has five states and "warming" is the amber one.
 */
export function orbStateFor(i: OrbInputs): OrbState {
	if (i.error) return 'error';
	if (i.recording) return 'listening';
	if (i.speaking) return 'speaking';
	if (i.running || i.warming) return 'thinking';
	return 'idle';
}

/** The word in the bar for the state. */
export function orbLabel(state: OrbState, warming: boolean): string {
	if (warming && state === 'thinking') return 'WARMING UP';
	switch (state) {
		case 'listening':
			return 'LISTENING';
		case 'thinking':
			return 'PROCESSING';
		case 'speaking':
			return 'RESPONDING';
		case 'error':
			return 'ERROR';
		default:
			return 'STANDBY';
	}
}

/** The bytes of a `data:audio/…;base64,…` URL, for `decodeAudioData`. */
export function dataUrlToBytes(dataUrl: string): ArrayBuffer {
	const comma = dataUrl.indexOf(',');
	if (!dataUrl.startsWith('data:') || comma === -1) throw new Error('not a data URL');
	const base64 = dataUrl.slice(comma + 1);
	const binary = typeof atob === 'function' ? atob(base64) : Buffer.from(base64, 'base64').toString('binary');
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	return bytes.buffer;
}

/** The MIME type a data URL declares. */
export function dataUrlMime(dataUrl: string): string {
	const match = /^data:([^;,]+)/.exec(dataUrl);
	return match ? match[1] : '';
}

/** A Blob as a base64 data URL, the shape the relay takes. Browser only. */
export function blobToDataUrl(blob: Blob): Promise<string> {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onerror = () => reject(reader.error ?? new Error('could not read the recording'));
		reader.onload = () => resolve(String(reader.result));
		reader.readAsDataURL(blob);
	});
}

/**
 * The recording format the relay is sent from a browser: WAV, because the
 * speaker gate embeds WAV or raw PCM and nothing else, and it has to hear the
 * same audio the transcriber hears (`$lib/audio/pcm`).
 */
export const RECORDING_MIME = 'audio/wav';

/**
 * Push-to-talk: hold, speak, release.
 *
 * `start()` opens the microphone; `stop()` resolves with the recording as a
 * WAV. Nothing leaves the browser until the caller posts it, so the button is
 * the privacy boundary as well as the control.
 */
export class PushToTalk {
	private capture = new PcmCapture();

	get recording(): boolean {
		return this.capture.recording;
	}

	/** The stream, for a level meter. */
	get mediaStream(): MediaStream | null {
		return this.capture.mediaStream;
	}

	async start(): Promise<void> {
		await this.capture.start();
	}

	/** End the recording; resolves with what was heard. */
	async stop(): Promise<Blob> {
		// Always through the capture: a stop while the microphone is still opening must end
		// that opening's recording, or it records on into every later utterance (pcm.ts).
		const pcm = await this.capture.stop();
		return pcm.length ? wavBlob(pcm) : new Blob([], { type: RECORDING_MIME });
	}

	/** Let the microphone go. */
	release(): void {
		this.capture.release();
	}
}
