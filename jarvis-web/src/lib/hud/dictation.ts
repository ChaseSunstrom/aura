/**
 * Hold-to-dictate, the page's half (T-1258). The desktop app's key (`jarvis-desktop-app` shell.rs
 * `dictate_key`) tells the popup -- the console's own page, hidden, so the focus stays in the
 * window the words are for -- to record while it is held, and to stop and answer when it comes up.
 * The words go back the only way this page can reach the app: in its document title
 * (`tests/contracts/dictation.json`), which the app takes for the one press it armed and sends the
 * agent to type. The page types nothing and the model hears nothing: this is a separate recorder
 * from the voice turn's, and what it hears never becomes a prompt.
 *
 * What this does not cover: whether the webview records while its window is hidden is the
 * platform's (WebKitGTK, WebView2); a recorder that cannot open answers "nothing", never a guess.
 */
import { PushToTalk, blobToDataUrl, transcriptIsEmpty } from '$lib/hermes/voice';

/** The app dispatches this on the popup window (`dictation.rs` `arm_script`); `detail` is `{ down, id }`. */
export const DICTATE_EVENT = 'jarvis:dictate';

/** A hold shorter than this is a tap: the owner brushed the key, and nothing is sent anywhere. */
export const MIN_HOLD_MS = 350;

/**
 * How long the answer stays in the title: long enough for the app to see it (a title change is
 * reported at once), short enough that the words do not linger in a window list or a taskbar.
 */
export const DICTATED_SHOWN_MS = 1500;

/** What the app mints (`dictation.rs`): letters and digits only, so it can sit in a title. */
const ID = /^[a-z0-9]{1,32}$/;

/**
 * The app dispatches this once it has taken the answer (`dictation.rs` `ack_script`), so the words
 * leave the title at once instead of after `DICTATED_SHOWN_MS`; `detail` is `{ id }`.
 */
export const DICTATED_EVENT = 'jarvis:dictated';

/** True when `segment` is the answer of the press the app acknowledged. */
export const acknowledges = (segment: string, detail: { id?: string } | null | undefined): boolean =>
	typeof detail?.id === 'string' && segment.startsWith(`typed:${detail.id}:`);

/**
 * The title segment that carries the answer: `typed:<id>:<text>`, the text through
 * encodeURIComponent so the title's ` · ` separator, a space or an `&` can never be in it. A null
 * or blank text is the empty answer: the app types nothing.
 */
export const dictatedSegment = (id: string, text: string | null): string =>
	`typed:${id}:${text && text.trim() ? encodeURIComponent(text) : ''}`;

export interface DictationDeps {
	recorder: { start(): Promise<void>; stop(): Promise<Blob>; release(): void };
	/** The words the adapter heard, or null for a refusal, a failure or silence. */
	transcribe(blob: Blob): Promise<string | null>;
	/** The answer for a press: the words, or null for "type nothing". */
	emit(id: string, text: string | null): void;
	/** False while the console is locked, in setup, enrolling a voice, or not the popup. */
	allowed(): boolean;
	now(): number;
}

interface Hold {
	id: string;
	at: number;
	/** The microphone opening; a key let go before it settles waits for it. */
	opening: Promise<boolean>;
}

/** One press at a time, from the app's `down` to its `up`; every press ends in exactly one answer. */
export class DictationFlow {
	#hold: Hold | null = null;

	constructor(private deps: DictationDeps) {}

	async handle(detail: { down?: boolean; id?: string } | null | undefined): Promise<void> {
		const id = detail?.id;
		if (typeof id !== 'string' || !ID.test(id)) return;
		if (detail?.down === true) {
			// A repeat of the hold in progress is nothing new. Any other id replaces the hold: the app
			// only arms a press once the last is over, so the old one lost its `up`, and keeping it
			// would leave the recorder open and wedge every press after.
			if (this.#hold?.id === id) return;
			if (this.#hold) this.deps.recorder.release();
			const at = this.deps.now();
			const opening = this.deps.allowed() ? this.deps.recorder.start().then(() => true, () => false) : Promise.resolve(false);
			this.#hold = { id, at, opening };
		} else if (detail?.down === false && this.#hold?.id === id) {
			const hold = this.#hold;
			this.#hold = null;
			this.deps.emit(id, await this.#words(hold));
		}
	}

	async #words(hold: Hold): Promise<string | null> {
		const recording = await hold.opening;
		if (!recording) return null;
		const tap = this.deps.now() - hold.at < MIN_HOLD_MS;
		try {
			const blob = await this.deps.recorder.stop();
			if (tap || blob.size === 0) return null;
			const text = await this.deps.transcribe(blob);
			return transcriptIsEmpty(text) ? null : String(text).trim();
		} catch {
			return null;
		} finally {
			// Not while a newer press has the recorder: its release is its own.
			if (!this.#hold) this.deps.recorder.release();
		}
	}
}

/**
 * The adapter's transcriber, under the same speaker gate as a spoken turn: the audio always goes,
 * with the surface it was heard on, and a voice the gate dropped or a transcription that failed
 * gives nothing. Never throws: a refused dictation is a null, not an error the page has to show.
 */
export async function transcribeForDictation(
	blob: Blob,
	deps: { fetch?: typeof fetch; surface: string; toDataUrl?: (blob: Blob) => Promise<string> }
): Promise<string | null> {
	try {
		const dataUrl = await (deps.toDataUrl ?? blobToDataUrl)(blob);
		const res = await (deps.fetch ?? fetch)('/hermes/api/audio/transcribe', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ data_url: dataUrl, mime_type: blob.type || 'audio/wav', surface: deps.surface })
		});
		if (!res.ok) return null;
		const body = (await res.json()) as { ok?: boolean; transcript?: string; speaker?: { dropped?: boolean } | null };
		if (body.ok === false || body.speaker?.dropped) return null;
		return transcriptIsEmpty(body.transcript) ? null : String(body.transcript).trim();
	} catch {
		return null;
	}
}

/** The flow wired to this page: its own recorder, the adapter, and an answer given to `emit`. */
export function pageDictation(o: { surface: string; allowed: () => boolean; emit: (id: string, text: string | null) => void }): DictationFlow {
	const recorder = new PushToTalk();
	return new DictationFlow({
		recorder,
		transcribe: (blob) => transcribeForDictation(blob, { surface: o.surface }),
		emit: o.emit,
		allowed: o.allowed,
		now: () => Date.now()
	});
}
