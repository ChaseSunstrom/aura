// The microphone and the speaker, as one thing the HUD owns.
//
// The old voice screen carried these three hundred lines inside its page:
// push-to-talk, the transcriber round trip, the sentence-by-sentence speech
// queue, the level meter and the turn's clock. They are the two things only a
// browser can do — hear and speak — and they belong to no panel: the voice
// panel draws the level and the button, the reactor breathes with the same
// level, and a turn started by voice lands in the chat panel like any other.
// So this is the controller all three read, with runes so a component can.
//
// The socket, the session and the event stream stay the layout's `HermesLink`;
// this only asks it to interrupt and to submit.

import { VOICE_CAPTURE } from '$lib/audio/pcm';
import { startWake } from '$lib/audio/wake';
import { Player, type PcmStream } from '$lib/audio/playback';
import { BargeIn, TAIL_MS } from './bargeIn';
import { ackNotes } from './earcon';
import type { HermesLink } from '$lib/hermes/link';
import type { TurnState } from '$lib/hermes/hud';
import { SpeechQueue, cutStream, speakable, takeSentences, type Clip } from '$lib/hermes/speech';
import { priorityOf } from '$lib/hermes/speechQueue';
import { DIDNT_CATCH, PushToTalk, blobToDataUrl, dataUrlToBytes, isStop, isWakeOnly, transcriptIsEmpty } from '$lib/hermes/voice';
import { phoneHears, phoneWords } from './phone';
import { recordTurn, type TurnLatency } from './data/latency';
import { refusal, type PrivateChat } from './private.svelte';

/** The adapter's `speaker` on a transcribe reply: the gate's verdict, for the room to show. */
export interface SpeakerVerdict {
	accepted: boolean;
	label: string | null;
	level: 'owner' | 'member' | 'guest' | null;
	reason: string;
	nearest: string | null;
	dropped: boolean;
	/** How close the nearest enrolled voice was (cosine). */
	similarity?: number | null;
	/** Set when the refusal was close enough to someone to be claimed as theirs. */
	claim?: string;
}

/** A refused voice the adapter is holding for the owner to claim (`/api/speaker/unrecognised`). */
export interface Unrecognised {
	id: string;
	nearest: string;
	similarity: number;
	surface: 'console' | 'android' | 'car' | 'desktop';
	at: number;
}

/** Where a refused voice was heard, in words. */
export function heardWhere(surface: Unrecognised['surface']): string {
	return { car: 'in the car', android: 'on the phone', desktop: 'on the desktop', console: 'here' }[surface] ?? 'here';
}

/** Which microphone this page is: the phone's home screen, or a console. */
export function thisSurface(): Unrecognised['surface'] {
	return (globalThis as { JarvisPhone?: unknown }).JarvisPhone ? 'android' : 'console';
}

const WAKE_KEY = 'jarvis:wake-word';
const UNSPOKEN = 'This reply could not be spoken: the voice is not answering.';
/** After the wake word failed, how long before an utterance's end tries it again. */
const WAKE_RETRY_MS = 60_000;

function readWakeOn(): boolean {
	if (thisSurface() === 'android') return false;
	try {
		return localStorage.getItem(WAKE_KEY) !== '0';
	} catch {
		return true;
	}
}

/** What the console says for a voice the gate refused and the adapter dropped. */
export const NOT_RECOGNISED = "I don't recognise that voice.";
/** How long the core listens for the command after the wake phrase alone (T-0769). */
export const AWAKE_MS = 8000;
import { fresh as freshOpenMic, step as openMicStep, type OpenMicState } from './openmic';

/** The clock's marks, as `performance.now()` stamps: `voiceStages` reads them. */
export type Stamps = Record<string, number | undefined>;

const READ_ALOUD_KEY = 'jarvis:read-aloud';

/** Read aloud unless this browser chose Written; the default too where nothing can be read. */
function readAloudRemembered(): boolean {
	try {
		return localStorage.getItem(READ_ALOUD_KEY) !== '0';
	} catch {
		return true;
	}
}

export class VoiceController {
	/** The private conversation (T-0462): while it is on, what is said goes there, not to Hermes. */
	privately: PrivateChat | null = null;
	recording = $state(false);
	/** The wake phrase was heard alone: the core shows listening until the command (T-0769). */
	awake = $state(false);
	private awakeTimer: ReturnType<typeof setTimeout> | null = null;
	transcribing = $state(false);
	speaking = $state(false);
	/** The microphone answered: permission is granted and a device exists. */
	micReady = $state(false);
	/** Why it is not, in words somebody can act on. */
	micError = $state('');
	/** The microphone level while recording, the speaker's while speaking, 0–1. */
	level = $state(0);
	/**
	 * Voices the gate refused in the last minutes that were close to someone enrolled:
	 * this page's and the car's and the phone's. "That was me" files one as a print for
	 * its microphone (the owner, 2026-10-07: enrolled here, refused in the car).
	 */
	unrecognised = $state<Unrecognised[]>([]);

	async refreshUnrecognised(): Promise<void> {
		try {
			const res = await fetch('/hermes/api/speaker/unrecognised');
			if (!res.ok) return;
			const body = (await res.json()) as { unrecognised?: Unrecognised[] };
			this.unrecognised = body.unrecognised ?? [];
		} catch {
			// The list is a convenience; a failed refresh leaves the last one.
		}
	}

	async claimMine(id: string): Promise<void> {
		try {
			const res = await fetch(`/hermes/api/speaker/unrecognised/${encodeURIComponent(id)}/mine`, { method: 'POST' });
			const body = (await res.json().catch(() => ({}))) as { ok?: boolean; error?: string; source?: string };
			this.notice = body.ok
				? `Learned. I'll know your voice ${heardWhere((body.source ?? 'console') as Unrecognised['surface'])} from now on.`
				: `Couldn't learn that one: ${body.error ?? `the adapter answered ${res.status}`}`;
		} catch (e) {
			this.notice = `Couldn't learn that one: ${e instanceof Error ? e.message : String(e)}`;
		}
		await this.refreshUnrecognised();
	}
	/** What was heard, once the transcriber answered. */
	transcript = $state('');
	/** A line the controller says itself: "I didn't catch that", a transcriber failure. */
	notice = $state('');
	/** Who the speaker gate heard on the last turn, when it had a say. */
	speaker = $state<SpeakerVerdict | null>(null);
	stamps = $state<Stamps>({});
	/** The last voice turns' stages (`data/latency.ts`): where the time goes. */
	latency = $state<TurnLatency[]>([]);
	#speakReplies = $state(readAloudRemembered());
	/**
	 * Speak replies off the core as well as on it: on unless this browser chose Written in
	 * Settings (the owner, 2026-11-09: "have it speak by default"), and remembered per
	 * browser like the open mic. Does not cover the car page, which never speaks (Shell).
	 */
	get speakReplies(): boolean {
		return this.#speakReplies;
	}
	set speakReplies(on: boolean) {
		this.#speakReplies = on;
		try {
			localStorage.setItem(READ_ALOUD_KEY, on ? '1' : '0');
		} catch {
			/* private window: not remembered */
		}
	}
	/**
	 * The primary persona's voice: what a reply is spoken in. A subagent's
	 * report is spoken in its own (`sayAs`), so who is talking can be told
	 * without looking (the overhaul brief, Phase 6).
	 */
	voice = $state('jarvis');
	/**
	 * Which engine spoke the last line: `cosyvoice` from the shim, `hermes`
	 * when the adapter fell back to hermes serve (the shim warming, or down).
	 * Empty until something has been said. The top bar shows it (V3 PART 2:
	 * "surface which engine is speaking in the HUD").
	 */
	engine = $state('');
	/**
	 * The mic is open: the room listens on its own and an utterance is sent
	 * when it ends (`openmic.ts`), so nobody holds Space (the owner,
	 * 2026-09-09). Off while a reply is being spoken -- the speaker is in the
	 * same room as the microphone, and a voice that answered itself would be
	 * the defect. Space and the mic button still work as they did.
	 */
	openMic = $state(false);
	/** How much quiet ends an utterance: `voice.vad_hangover_ms`, set by the host. */
	hangoverMs = 550;
	/** Why the open mic could not listen, if it could not. */
	openMicError = $state('');
	/**
	 * Why the mic is not listening when nobody asked it to: the open mic restoring itself at
	 * load on a device with no microphone (or one the browser blocks). Not an error toast --
	 * that showed on every load of a desktop with no mic (T-0321) -- but the mic button's own
	 * explanation. Cleared when the mic opens.
	 */
	micUnavailable = $state('');
	private restoring = false;
	/**
	 * The open mic waits for the worn theme's wake word (T-0875): only an utterance the wake
	 * opened is sent, so room talk never reaches the house. Remembered per device; on unless
	 * this is the phone, whose own service listens for the name. Off, every utterance is sent.
	 */
	wakeOn = $state(readWakeOn());
	/** The wake word is running on the open mic now (its models loaded). */
	wakeListening = $state(false);
	/** Why the wake word could not run: the open mic then sends every utterance. */
	wakeError = $state('');
	/** The worn theme's wake model and threshold (Shell sets it), or null for none. */
	wakeWord = $state.raw<{ model: string; threshold: number } | null>(null);
	private wakeStop: (() => void) | null = null;
	private arming = false;
	private wakeRetryAt = 0;
	/** The wake opened the open mic's utterance in flight: it fired during it or was awake when it began. */
	private opened = false;
	/**
	 * Replies will be spoken: the host's answer, set once it exists. The
	 * acknowledgement earcon plays only then, so a muted, typing owner hears
	 * nothing; it does not know whether this turn's reply will be short.
	 */
	ackWanted: () => boolean = () => false;

	private readonly ptt = new PushToTalk();
	/** The stream the open mic listens on between utterances; the recorder opens its own. */
	private ambient: MediaStream | null = null;
	private vad: OpenMicState = freshOpenMic();
	/** The utterance in flight was the open mic's: a blip is thrown away silently. */
	private openUtterance = false;
	private readonly player = new Player();
	private pressedAt = 0;
	private levelCtx: AudioContext | null = null;
	private levelAnalyser: AnalyserNode | null = null;
	private levelData: Uint8Array<ArrayBuffer> | null = null;
	private speech: SpeechQueue | null = null;
	/** Characters of the current turn's text already handed to the queue. */
	private spokenLen = 0;
	/** Which turn (by its start stamp) the queue is speaking. */
	private speakingTurn: number | null = null;
	private raf = 0;

	constructor(private readonly link: HermesLink) {
		// The first reply sample handed to the speaker, not the queue waking:
		// `onActive` fires before the first sentence is even synthesised.
		this.player.onSound = () => this.stamp('first-audio');
	}

	// --- the clock ------------------------------------------------------------

	stamp(type: string, at = performance.now()): void {
		if (this.stamps[type] !== undefined) return;
		this.stamps = { ...this.stamps, [type]: at };
		this.latency = recordTurn(this.latency, this.stamps);
	}

	resetClock(): void {
		this.stamps = {};
	}

	/**
	 * The link's stamps, folded into ours: the first token and the end of the
	 * turn are the reducer's marks, taken when the events arrived.
	 */
	followTurn(turn: TurnState | null | undefined): void {
		if (!turn) return;
		if (turn.firstTokenAt && this.stamps['first-token'] === undefined && this.stamps['submit'] !== undefined) {
			this.stamp('first-token');
		}
		if (turn.finishedAt && !turn.running && this.stamps['submit'] !== undefined && this.stamps['complete'] === undefined) {
			this.stamp('complete');
		}
	}

	// --- the microphone -------------------------------------------------------

	/** Ask for the microphone once, so a refusal shows on the button and not at the first press. */
	async prime(): Promise<void> {
		try {
			const stream = await navigator.mediaDevices?.getUserMedia({ audio: VOICE_CAPTURE });
			stream?.getTracks().forEach((t) => t.stop());
			this.micReady = true;
			this.micError = '';
		} catch (e) {
			this.micError = micTrouble(e);
		}
	}

	async start(): Promise<void> {
		if (this.recording) return;
		// A press while the open mic listens: the press's utterance, not the room's.
		if (!this.openUtterance) this.unlisten();
		// Barge-in: a new push-to-talk stops the reply and the turn behind it. An open-mic
		// utterance only quiets the reply (its text still comes): the turn is interrupted once
		// its words are accepted (`heard`), since the onset may be a cough or the room (T-0705).
		if (this.openUtterance) this.speech?.stop();
		else {
			this.stopSpeaking();
			if (this.link.current.hud.turn.running) void this.link.interrupt().catch(() => {});
		}
		try {
			await this.ptt.start();
		} catch (e) {
			this.micError = micTrouble(e);
			this.micReady = false;
			return;
		}
		this.micReady = true;
		this.micError = '';
		this.recording = true;
		this.pressedAt = performance.now();
		this.stamps = {};
		this.stamp('ptt-start');
		this.notice = '';
		this.transcript = '';
		this.meter(this.ptt.mediaStream);
		this.tick();
	}

	async stop(): Promise<void> {
		if (!this.recording) return;
		this.recording = false;
		// Acknowledged on the release itself, before the recorder's blob or the
		// transcriber: the reactor sat at rest through that round trip, which
		// read as "it didn't hear me" (voi-08).
		this.transcribing = true;
		this.stamp('ptt-end');
		this.unmeter();
		try {
			await this.heard(await this.ptt.stop());
		} finally {
			this.transcribing = false;
			this.openUtterance = false;
			// Back to listening, if the mic is still open.
			if (this.openMic) void this.listen();
		}
	}

	private async heard(blob: Blob): Promise<void> {
		this.stamp('ptt-end');
		try {
			const dataUrl = await blobToDataUrl(blob);
			// The phone writes it down itself when its switch says so (phone.ts phoneHears).
			// A phone that fails leaves it to the server, which has the audio anyway.
			let said: string | undefined;
			if (phoneHears()) {
				said = await phoneWords(dataUrl).then(
					(words) => words.trim(),
					() => undefined
				);
				if (said !== undefined && transcriptIsEmpty(said)) {
					// Room tone the open mic caught never leaves the phone at all.
					this.stamp('stt-end');
					if (!this.openUtterance) this.notice = DIDNT_CATCH;
					return;
				}
			}
			const res = await fetch('/hermes/api/audio/transcribe', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				// The audio always goes, for the speaker gate to hear who spoke; the phone's
				// words go with it, which the adapter takes instead of a second Whisper.
				// A private turn is written down on the voice card only, never kept for a claim.
				body: JSON.stringify({
					data_url: dataUrl,
					mime_type: blob.type || 'audio/webm',
					surface: thisSurface(),
					transcript: said,
					private: this.privately?.on || undefined
				})
			});
			if (!res.ok) throw new Error(await refusal(res, 'transcribe'));
			const body = (await res.json()) as {
				ok?: boolean;
				transcript?: string;
				error?: string;
				speaker?: SpeakerVerdict | null;
				speaker_ticket?: string;
			};
			this.stamp('stt-end');
			if (body.ok === false) throw new Error(body.error || 'transcription failed');
			this.speaker = body.speaker ?? null;
			if (body.speaker?.dropped) {
				// The gate refused the voice and the adapter dropped the words
				// before they could become a prompt. Said here, in the console's
				// own line, because the house's refusal line is spoken by the
				// house's own speakers and this one may be somewhere else.
				this.notice = NOT_RECOGNISED;
				void this.refreshUnrecognised();
				return;
			}
			if (transcriptIsEmpty(body.transcript)) {
				// Silence. Said locally, and nothing is submitted -- unless the open
				// mic heard it, in which case room tone is the expected case and a
				// notice every time the fridge hums would be noise about noise.
				if (!this.openUtterance) this.notice = DIDNT_CATCH;
				return;
			}
			if (this.openUtterance && isWakeOnly(body.transcript)) {
				// The wake, not a turn: "Hey Jarvis" went to the model and the core never listened,
				// where the phone lights up and waits for the command (T-0769). Before the interrupt:
				// the name called while a long turn works ended it with nothing in its place (T-0730).
				this.wake();
				return;
			}
			// Only a stop ends the running turn. Anything else is submitted as it is and Hermes folds
			// it into that turn (its busy redirect): a passing remark used to kill a long job (T-0794).
			if (this.openUtterance && this.link.current.hud.turn.running && isStop(body.transcript)) await this.link.interrupt().catch(() => {});
			// Its own catch: a refused send after the words were heard must not read as a failed
			// transcription (T-1210).
			try {
				await this.submit(String(body.transcript), body.speaker_ticket ?? null, null, true);
			} catch (e) {
				this.notice = `Heard that, but could not send it: ${e instanceof Error ? e.message : String(e)}`;
			}
		} catch (e) {
			this.notice = `Could not transcribe that: ${e instanceof Error ? e.message : String(e)}`;
		}
	}

	// --- the open mic -----------------------------------------------------------

	/** Open or close the mic. Remembered per browser: it is the person's, not the session's. */
	async setOpenMic(on: boolean): Promise<void> {
		this.openMic = on;
		try {
			localStorage.setItem('jarvis:open-mic', on ? '1' : '0');
		} catch {
			/* private window: not remembered */
		}
		if (on) await this.listen();
		else this.quiet();
	}

	/**
	 * What the browser remembered, applied on mount. Open unless it was muted
	 * (the owner, 2026-09-18: the mic is open and can be muted, not the other
	 * way round). A private window that cannot remember gets the default too.
	 */
	async restoreOpenMic(): Promise<void> {
		let on = true;
		try {
			on = localStorage.getItem('jarvis:open-mic') !== '0';
		} catch {
			/* private window: the default */
		}
		if (!on) return;
		this.restoring = true;
		try {
			await this.setOpenMic(true);
		} finally {
			this.restoring = false;
		}
	}

	/** Turn the wake word on or off for this device; off, the open mic sends every utterance. */
	setWakeOn(on: boolean): void {
		this.wakeOn = on;
		try {
			localStorage.setItem(WAKE_KEY, on ? '1' : '0');
		} catch {
			/* private window: not remembered */
		}
		this.rearmWake();
	}

	/** The worn theme changed: listen for its name instead. */
	setWakeWord(word: { model: string; threshold: number } | null): void {
		if (word?.model === this.wakeWord?.model && word?.threshold === this.wakeWord?.threshold) return;
		this.wakeWord = word;
		this.rearmWake();
	}

	private rearmWake(): void {
		this.disarmWake();
		this.wakeError = '';
		this.wakeRetryAt = 0;
		if (this.ambient) void this.armWake();
	}

	/**
	 * Run the wake word on a stream of its own, kept across utterances (the open mic's ambient
	 * stream is let go at every onset, and the name is said right then). A failure leaves the
	 * open mic as it was before the wake word: every utterance sent, and no new attempt for
	 * WAKE_RETRY_MS (each one fetches the models again).
	 */
	private async armWake(): Promise<void> {
		const word = this.wakeWord;
		if (!this.wakeOn || !word || this.wakeStop || this.arming || thisSurface() === 'android') return;
		if (Date.now() < this.wakeRetryAt) return;
		this.arming = true;
		let stream: MediaStream | null = null;
		try {
			stream = await navigator.mediaDevices.getUserMedia({ audio: VOICE_CAPTURE });
			const stop = await startWake(stream, word, () => this.heardName(), (e) => this.wakeFailed(e));
			const s = stream;
			// Turned off, or the theme changed, while it loaded.
			if (!this.wakeOn || this.wakeWord !== word || !this.openMic || this.held || this.away) {
				stop();
				s.getTracks().forEach((t) => t.stop());
				return;
			}
			this.wakeStop = () => {
				stop();
				s.getTracks().forEach((t) => t.stop());
			};
			this.wakeListening = true;
			this.wakeError = '';
		} catch (e) {
			stream?.getTracks().forEach((t) => t.stop());
			// A load that fails for a name since replaced holds nothing back: the new name is armed at
			// once (the finally), not after WAKE_RETRY_MS for a word no longer worn.
			if (this.wakeWord === word) this.wakeFailed(e);
		} finally {
			this.arming = false;
			// The theme changed while it loaded: what loaded was let go, so listen for the new name.
			if (this.wakeWord !== word && this.openMic && !this.held && !this.away) void this.armWake();
		}
	}

	/** It could not start, or a step threw while running: fall open rather than hold every word back. */
	private wakeFailed(e: unknown): void {
		this.disarmWake();
		this.wakeError = e instanceof Error ? e.message : String(e);
		this.wakeRetryAt = Date.now() + WAKE_RETRY_MS;
	}

	private disarmWake(): void {
		this.wakeStop?.();
		this.wakeStop = null;
		this.wakeListening = false;
	}

	/** The wake word fired: the core listens, and the utterance in flight (the name's own) is sent. */
	private heardName(): void {
		this.wake();
		if (this.openUtterance) this.opened = true;
	}

	/**
	 * The lock's and the enrolment sheet's pair (Shell.svelte): stop listening without
	 * forgetting the choice, and listen again once neither holds it. `setOpenMic(false)` is the wrong tool here — it writes
	 * the mute to storage, so a lock that used it left the mic muted until the
	 * person found the button again (and across reloads).
	 */
	holdOpenMic(): void {
		// A flag, not just `unlisten()`: nothing may listen again until the hold is undone.
		this.held = true;
		// What it was recording is thrown away, not sent: words said as the lock falls, or the
		// phrase being read into the enrolment sheet, which went to JARVIS as a turn (T-0768).
		this.abort();
		this.quiet();
	}

	resumeOpenMic(): void {
		// Only a hold is undone: a resume with nothing held would open a second stream beside the
		// one `restoreOpenMic` was opening.
		if (!this.held) return;
		this.held = false;
		if (this.openMic) void this.listen();
	}
	private held = false;

	/**
	 * Release the open mic while the page is away: hidden on an Android device, or with a
	 * car linked. In an Android web view a held microphone puts the phone in call audio
	 * mode -- the car then shows a call on every connect and the music stays muted (the
	 * owner, 2026-10-06: "it thinks I'm in a call every time I connect, it won't start
	 * music"). The car has its own microphone path (`carLink.act({kind:'talk'})`).
	 * Separate from [holdOpenMic], whose lock this must not undo.
	 */
	setAway(away: boolean): void {
		if (this.away === away) return;
		this.away = away;
		if (away) this.quiet();
		else if (this.openMic) void this.listen();
	}
	private away = false;
	private readonly bargeIn = new BargeIn();
	/** The last frame the reply played, and whether the last frame was in a live reply. */
	private spokeAt = -Infinity;
	private wasLive = false;

	/** Listen between utterances: a stream of its own, metered, no recorder. */
	private async listen(): Promise<void> {
		if (!this.openMic || this.held || this.away || this.recording || this.ambient) return;
		let stream: MediaStream;
		try {
			stream = await navigator.mediaDevices.getUserMedia({ audio: VOICE_CAPTURE });
		} catch (e) {
			// Pressed: say so. Restored at load: the button explains, nothing pops up.
			if (this.restoring) this.micUnavailable = micTrouble(e);
			else this.openMicError = micTrouble(e);
			this.openMic = false;
			return;
		}
		// Went away (or off, or held) while the grant was pending, or an earlier ask already
		// landed: a stream kept here was held the whole drive, and the car showed a call (T-0993).
		if (!this.openMic || this.held || this.away || this.recording || this.ambient) {
			stream.getTracks().forEach((t) => t.stop());
			return;
		}
		this.ambient = stream;
		this.openMicError = '';
		this.micUnavailable = '';
		this.micReady = true;
		this.vad = freshOpenMic();
		this.meter(this.ambient);
		this.tick();
		void this.armWake();
	}

	/**
	 * The open mic turned off: by the button, a hold or the page going away. Not `unlisten`,
	 * which every utterance's onset also calls to hand the stream to the recorder: a cough after
	 * the name ended the wake there (T-0769).
	 */
	private quiet(): void {
		this.sleep();
		this.disarmWake();
		this.unlisten();
	}

	private wake(): void {
		this.awake = true;
		if (this.awakeTimer) clearTimeout(this.awakeTimer);
		this.awakeTimer = setTimeout(() => this.sleep(), AWAKE_MS);
	}

	private sleep(): void {
		this.awake = false;
		if (this.awakeTimer) clearTimeout(this.awakeTimer);
		this.awakeTimer = null;
	}

	private unlisten(): void {
		this.ambient?.getTracks().forEach((t) => t.stop());
		this.ambient = null;
		if (!this.recording) this.unmeter();
	}

	/** One frame of the open mic: the decision, and what it starts or stops. */
	private openMicFrame(now: number): void {
		const level = this.readLevel();
		if (this.speaking) this.spokeAt = now;
		// While a reply is live -- playing, or the tail of its last clip -- the speaker is in the
		// room too: only a voice clearly over its echo starts an utterance (`BargeIn`). The plain
		// detector took the echo in a gap for the person and cut the reply off (T-0705). A turn
		// that works with nothing playing has no echo: the plain detector listens, or a plain
		// voice mid-task was never heard (the owner, T-0902).
		const live = this.speaking || now - this.spokeAt < TAIL_MS;
		if (live && !this.recording) {
			if (!this.wasLive) this.bargeIn.reset(now);
			this.wasLive = true;
			if (this.ambient && this.bargeIn.hear(now, level, this.vad.floor)) {
				this.openUtterance = true;
				this.opened = this.awake;
				this.unlisten();
				void this.start();
			}
			return;
		}
		// Recording or quiet: the next live frame starts BargeIn afresh.
		this.wasLive = false;
		// A recording ends by the plain detector even while a reply plays on.
		const { state, event } = openMicStep(this.vad, level, now, this.hangoverMs);
		this.vad = state;
		if (event === 'onset' && !this.recording) {
			this.openUtterance = true;
			this.opened = this.awake;
			// The recorder opens its own stream; the ambient one is let go so a
			// browser with one device does not hold it twice.
			this.unlisten();
			void this.start();
		} else if ((event === 'end' || event === 'blip') && this.recording && this.openUtterance) {
			// Waiting for the name and it was not said: thrown away here, never transcribed.
			if (event === 'blip' || (this.wakeListening && !this.opened)) {
				this.abort();
				void this.listen();
			} else void this.stop();
		}
	}

	/** A press on the hold-to-talk control: down starts, up ends when it was a hold, a tap toggles. */
	press(): void {
		if (this.recording) void this.stop();
		else void this.start();
	}

	release(): void {
		if (this.recording && performance.now() - this.pressedAt > 350) void this.stop();
	}

	/**
	 * Throw the recording away without transcribing it.
	 *
	 * `stop()` is the successful end of a hold: it transcribes and submits.
	 * This is the other ending -- the press turned out to be the start of a
	 * drag on the orb, so nothing was said and nothing should be sent. Sending
	 * a second of room tone to the model and letting it answer is worse than
	 * silence, and `stop()` would do exactly that.
	 */
	abort(): void {
		if (!this.recording) return;
		this.recording = false;
		this.openUtterance = false;
		this.unmeter();
		void this.ptt.stop().catch(() => {});
		this.stamps = {};
		this.transcript = '';
		this.notice = '';
	}

	/**
	 * Say it in words: the same turn, entered from the box or from the
	 * transcriber. A transcriber's turn carries the speaker gate's ticket when
	 * a voice was recognised; a typed one never does, and is the key holder's.
	 * `voice` is true only from the transcriber: the mic spoke it, ticket or not.
	 */
	async submit(text: string, speakerTicket: string | null = null, photo: string | null = null, voice = false): Promise<string> {
		const trimmed = text.trim();
		if (!trimmed) return '';
		this.sleep();
		this.transcript = trimmed;
		if (this.stamps['submit'] === undefined) this.stamp('submit');
		if (this.privately?.on) {
			// Never to Hermes, whose sessions keep everything: no ticket, no ack, no
			// session, and no photo (the private chat has nowhere to keep one).
			void this.privately.ask(trimmed);
			return '';
		}
		const outcome = await this.link.submit(trimmed, speakerTicket, photo, voice);
		// Only for a turn that went, and never over a reply still being read: the
		// tail of the last one plays after its turn has ended, and a prefill sent
		// nothing (the review of T-0022).
		if (outcome?.kind === 'sent' && this.ackWanted() && !this.speaking) void this.player.tones(ackNotes);
		if (outcome?.kind === 'sent') this.notice = outcome.notice;
		else if (outcome?.kind === 'output') this.notice = outcome.output;
		else if (outcome?.kind === 'error') this.notice = outcome.message;
		else if (outcome?.kind === 'prefill') return outcome.message;
		return '';
	}

	// --- speaking the reply, as it streams -----------------------------------

	/**
	 * Hand a turn's text to the queue as sentences complete. Called from an
	 * effect on every reducer change; a new turn resets the read position and
	 * a turn this HUD did not start (a goal continuing) is still spoken.
	 */
	speakTurn(turn: TurnState | null | undefined, wanted: boolean): void {
		if (!turn) return;
		if (turn.startedAt !== null && turn.startedAt !== this.speakingTurn) {
			this.speech?.stop();
			this.speakingTurn = turn.startedAt;
			this.spokenLen = 0;
			this.speech = wanted ? this.newSpeech() : null;
		}
		if (!this.speech || turn.error) return;
		const full = turn.text;
		if (full.length <= this.spokenLen) return;
		// Text pauses longest while a tool runs: the narration before it ("Let me check that
		// page.") is said then, not held to the answer for a mark that may yet go on.
		const toolRunning = turn.tools.some((t) => t.state === 'running');
		const { lines, upTo } = cutStream(full, this.spokenLen, (turn.running || !turn.finishedAt) && !toolRunning);
		// Held (locked screen, enrolment): the read position still moves, so a reply that streams
		// through the hold is skipped, not replayed from its first word when the hold ends.
		if (!this.held) for (const line of lines) this.speech.push(line, this.voice);
		this.spokenLen = upTo;
	}

	/**
	 * A line from another speaker -- a subagent reporting in its persona's voice.
	 *
	 * A summary, not a narration: the first two sentences. The V3 brief's rule
	 * is that a house where six agents narrate themselves is unusable and a
	 * finished companion gets one spoken line; the owner's (2026-09-09) is that
	 * each subagent has its own voice. Both hold: one line, its own voice.
	 *
	 * Queued at companion rank behind whatever the primary still has to say,
	 * never over it -- two voices at once is noise. Dropped when `wanted` is
	 * false, so a chat layout that reads no replies aloud hears no subagent.
	 */
	sayAs(text: string, voice: string, wanted: boolean): void {
		const line = speakable(text).trim();
		// Dropped while held, not queued: a helper's report must not sound behind a lock (T-1159).
		if (!wanted || !line || this.held) return;
		if (!this.speech) {
			this.speech = this.newSpeech();
			this.speakingTurn = null;
		}
		const cut = takeSentences(line);
		const lines = cut.sentences.length ? cut.sentences.slice(0, 2) : [cut.rest.trim()];
		for (const sentence of lines) this.speech.push(sentence, voice, priorityOf('companion'));
	}

	/**
	 * A line the person asked to hear (`hud.say`): every sentence, at the
	 * reply's own rank, in whatever mode they are in. `sayAs` is a companion's
	 * two-sentence report; this is the whole thing, because they asked.
	 */
	say(text: string, voice: string): void {
		const line = speakable(text).trim();
		if (!line || this.held) return; // same hold as sayAs (T-1159)
		if (!this.speech) {
			this.speech = this.newSpeech();
			this.speakingTurn = null;
		}
		const cut = takeSentences(line);
		for (const sentence of [...cut.sentences, cut.rest.trim()].filter(Boolean)) this.speech.push(sentence, voice);
	}

	stopSpeaking(): void {
		this.speech?.stop();
		this.speech = null;
		this.speaking = false;
		this.spokenLen = 0;
		this.speakingTurn = null;
	}

	/** Voices the adapter said in JARVIS's instead (T-0807): each is told once, not every sentence. */
	private readonly missingVoices = new Set<string>();

	private saidIn(asked: string | undefined, used: string | null): void {
		if (!asked || !used || used === asked || this.missingVoices.has(asked)) return;
		this.missingVoices.add(asked);
		this.notice = `This house has no ${asked} voice yet, so ${used} is speaking instead.`;
	}

	private newSpeech(): SpeechQueue {
		// The queue's first line is the start of a reply: the adapter sends its first clause to
		// the voice alone (tests/contracts/first-clause.json), so the first word comes sooner.
		let startOfReply = true;
		return new SpeechQueue({
			synth: (text, voice) => {
				const first = startOfReply;
				startOfReply = false;
				return this.synthLine(text, voice, first);
			},
			player: this.player,
			onActive: (active) => {
				this.speaking = active;
				if (active) this.bargeIn.reset(performance.now());
				this.stamp(active ? 'speak-start' : 'speak-end');
				if (active) this.tick();
			}
		});
	}

	/**
	 * One line's audio, or null with a notice saying why it is not spoken. Streamed when the
	 * voice can; the whole line otherwise. A private line is said by the voice card or not at
	 * all: the other engines keep what they say (T-0462).
	 */
	private async synthLine(text: string, voice: string | undefined, first: boolean): Promise<Clip | null> {
		const privately = this.privately?.on === true;
		const streamed = await synthStream(text, voice, privately, first).catch(() => null);
		if (streamed) {
			this.engine = streamed.engine;
			this.saidIn(voice, streamed.voice);
			return streamed.clip;
		}
		if (privately) {
			this.notice = 'This private reply is not spoken: only the voice card speaks privately, and it is not answering.';
			return null;
		}
		// The queue drops a line that throws, so a voice that is down was a silent reply with
		// nothing on the screen to say why (T-0906: a day of 503 and 400).
		const said = await synthOne(text, voice).catch(() => null);
		if (!said) {
			this.notice = UNSPOKEN;
			return null;
		}
		this.engine = said.engine;
		this.saidIn(voice, said.voice);
		return said.bytes;
	}

	// --- the level ------------------------------------------------------------

	private meter(stream: MediaStream | null): void {
		if (!stream) return;
		try {
			this.levelCtx = new AudioContext();
			this.levelAnalyser = this.levelCtx.createAnalyser();
			this.levelAnalyser.fftSize = 256;
			this.levelCtx.createMediaStreamSource(stream).connect(this.levelAnalyser);
			this.levelData = new Uint8Array(this.levelAnalyser.fftSize);
		} catch {
			this.levelCtx = null;
		}
	}

	private unmeter(): void {
		void this.levelCtx?.close().catch(() => {});
		this.levelCtx = null;
		this.levelAnalyser = null;
		this.levelData = null;
	}

	private readLevel(): number {
		if (!this.levelAnalyser || !this.levelData) return 0;
		this.levelAnalyser.getByteTimeDomainData(this.levelData);
		let sum = 0;
		for (let i = 0; i < this.levelData.length; i++) {
			const v = (this.levelData[i] - 128) / 128;
			sum += v * v;
		}
		return Math.sqrt(sum / this.levelData.length);
	}

	/**
	 * The level, sampled per frame only while something is being heard or
	 * said: a loop that ran at rest would be the reactor's clock a second time
	 * and would keep a hidden tab awake (§8.3 rule 2).
	 */
	private tick(): void {
		if (this.raf) return;
		const step = () => {
			this.raf = 0;
			const now = performance.now();
			if (this.speaking) this.level = Math.min(this.player.level() * 2, 1);
			else if (this.recording) this.level = Math.min(this.readLevel() * 4, 1);
			else if (this.openMic && this.ambient) this.level = 0;
			else {
				this.level = 0;
				return;
			}
			// The open mic decides on the raw level while listening and while its
			// own utterance records; a held Space is the person's call to end.
			if (this.openMic && (this.ambient || (this.recording && this.openUtterance))) this.openMicFrame(now);
			this.raf = requestAnimationFrame(step);
		};
		this.raf = requestAnimationFrame(step);
	}

	dispose(): void {
		if (this.raf) cancelAnimationFrame(this.raf);
		this.raf = 0;
		this.quiet();
		this.unmeter();
		this.ptt.release();
		this.speech?.stop();
		this.player.stopAll();
	}
}

/**
 * One sentence, streamed as it is made: `/api/audio/speak-pcm` answers PCM
 * pieces, the first while the rest is still being synthesised. This is the
 * time-to-first-word path (the owner, 2026-09-10: "slow to process the TTS,
 * even slower than piper"; measured, the whole-line route took six tenths
 * of the line's own length before a byte). The pieces are gathered here,
 * eagerly, so a line queued ahead of its turn does not hold the voice's
 * lock open by leaving its body unread. Null when the route is not there
 * (another engine, an older adapter): the caller takes the whole-line one.
 */
async function synthStream(text: string, voice?: string, privately = false, firstClause = false): Promise<{ clip: PcmStream; engine: string; voice: string | null } | null> {
	const res = await fetch('/hermes/api/audio/speak-pcm', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ text, voice, private: privately || undefined, first_clause: firstClause || undefined })
	});
	if (res.status === 404 || res.status === 503) return null;
	if (!res.ok || !res.body) throw new Error(`speak-pcm answered ${res.status}`);
	const rate = Number(res.headers.get('x-sample-rate')) || 24000;
	const engine = res.headers.get('x-jarvis-tts-engine') || 'cosyvoice';
	// The voice that spoke: JARVIS's when the house has no clip for the one asked for.
	return { clip: { rate, pieces: gathered(res.body) }, engine, voice: res.headers.get('x-jarvis-voice') };
}

/**
 * Read a body to the end from the moment this is called, and hand the pieces
 * out in order to whoever iterates later -- or at once, if they are already
 * waiting. The reader runs ahead of the player on purpose (see synthStream).
 */
function gathered(body: ReadableStream<Uint8Array>): AsyncIterable<Uint8Array> {
	const pieces: Uint8Array[] = [];
	// One holder rather than three lets: the reader and the iterator are two
	// closures, and TypeScript narrows a `let` by the flow it can see.
	const st = { done: false, failed: null as unknown, wake: null as (() => void) | null };
	const notify = () => {
		const w = st.wake;
		st.wake = null;
		w?.();
	};
	const reader = body.getReader();
	(async () => {
		try {
			for (;;) {
				const { value, done: end } = await reader.read();
				if (end) break;
				if (value?.length) pieces.push(value);
				notify();
			}
		} catch (e) {
			st.failed = e;
		} finally {
			st.done = true;
			notify();
		}
	})();
	return {
		async *[Symbol.asyncIterator]() {
			let at = 0;
			for (;;) {
				if (at < pieces.length) {
					yield pieces[at++];
					continue;
				}
				if (st.done) {
					if (st.failed && !at) throw st.failed;
					return;
				}
				await new Promise<void>((resolve) => (st.wake = resolve));
			}
		}
	};
}

/** POST one sentence to the relay's synthesiser, in `voice`: the audio bytes and which engine made them. */
async function synthOne(text: string, voice?: string): Promise<{ bytes: ArrayBuffer; engine: string; voice: string | null } | null> {
	const res = await fetch('/hermes/api/audio/speak', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(voice ? { text, voice } : { text })
	});
	if (!res.ok) throw new Error(`speak answered ${res.status}`);
	// The contract says `audio`; the running adapter answers `data_url` (MP3,
	// not WAV). Both are read, and the codec is the decoder's problem.
	const body = (await res.json()) as { ok?: boolean; audio?: string; data_url?: string; error?: string; engine?: string };
	const clip = body.audio || body.data_url;
	if (!clip) throw new Error(body.error || 'no audio came back');
	// The shim names itself in the body; a fallback is named in a header.
	const engine = body.engine || res.headers.get('x-jarvis-tts-engine') || 'hermes';
	return { bytes: dataUrlToBytes(clip), engine, voice: res.headers.get('x-jarvis-voice') };
}

/** Why getUserMedia said no, in words somebody can act on (§11: what to do next). */
export function micTrouble(e: unknown): string {
	const name = (e as { name?: string } | null)?.name ?? '';
	if (name === 'NotAllowedError' || name === 'SecurityError') return 'The microphone is blocked. Allow it in the browser.';
	if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No microphone was found.';
	return 'The microphone is unavailable.';
}
