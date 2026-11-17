// Speaking the reply as it streams.
//
// The old path waited for `message.complete` and synthesised the whole answer
// in one shot, so a long reply (the Bitcoin summary) was silent until the last
// token. This splits the streamed text into sentences and synthesises each as
// soon as it is complete, one or two ahead, playing them back to back — so the
// first words are heard a second or two after they appear, not after the whole
// answer. What is read aloud is stripped of markdown, code and URLs (SOUL rule
// 10: a spoken reply does not read asterisks and link targets out loud).
//
// The splitter and the stripper are pure; `SpeechQueue` takes an injected
// synthesiser and player, so the ordering is tested in Node without audio.

import type { PcmStream } from '$lib/audio/playback';

export interface SentenceCut {
	/** Complete sentences, in order. */
	sentences: string[];
	/** The trailing, not-yet-complete remainder to carry to the next delta. */
	rest: string;
}

/**
 * Split `text` into complete sentences and the incomplete remainder.
 *
 * A sentence ends at `.`, `!`, `?` (followed by space or end) or a newline, and
 * must be at least `min` characters, so a decimal or an abbreviation does not
 * cut the sentence in two — a short fragment waits and joins the next.
 *
 * `open` says more text may follow (a reply still streaming): a mark at the
 * very end then waits for the next character, because "wallet." may become
 * "wallet.nu.fi" and "22." may become "22.5". A cut never falls inside a fenced
 * block: its lines went to the voice as sentences, and a URL tail with a session
 * id was spelled out (T-0729). Does not cover a fence the model never closes
 * while the stream is open; `speakable` says that one at the end.
 */
export function takeSentences(text: string, min = 20, open = false): SentenceCut {
	const src = String(text ?? '');
	const fences = [...src.matchAll(new RegExp(FENCE.source, 'gm'))].map((f) => f.index ?? 0);
	const sentences: string[] = [];
	let cut = 0;
	const re = open ? /[.!?]+(?=\s)|\n+/g : /[.!?]+(?=\s|$)|\n+/g;
	let m: RegExpExecArray | null;
	while ((m = re.exec(src))) {
		const end = m.index + m[0].length;
		if (fences.filter((at) => at >= cut && at < end).length % 2 === 1) {
			// Inside a block: go to the fence that closes it, or stop. Rescanning an open
			// block's every line on every delta was quadratic in its length.
			const close = fences.find((at) => at >= end);
			if (close === undefined) break;
			re.lastIndex = close;
			continue;
		}
		const candidate = src.slice(cut, end).trim();
		if (candidate.length >= min) {
			sentences.push(candidate);
			cut = end;
		}
	}
	return { sentences, rest: src.slice(cut) };
}

// A fence opens only at the start of a line (CommonMark; a backtick fence's info string has no
// backtick): three ticks in prose are not a block, and treating them as one held, then
// swallowed, the rest of the reply (T-0729 review).
const FENCE = /^[ \t]{0,3}(?:`{3,}[^`\n]*|~{3,}[^\n]*)$/;

/** Each fenced block, closed or cut off, as one line saying it is on screen. */
function sayBlocks(text: string): string {
	let inside = false;
	return text
		.split('\n')
		.map((line) => {
			if (!FENCE.test(line)) return inside ? '' : line;
			inside = !inside;
			return inside ? ' The code is on screen. ' : '';
		})
		.join('\n');
}

/**
 * The lines to queue from a reply that has grown to `full`, read from `from`;
 * `upTo` is where the next call reads from. While `open`, an incomplete tail
 * waits; once closed, it is said too. One function for the HUD's stream so the
 * NUFI table (tests/contracts/speech-nufi.json) tests the path it takes.
 */
export function cutStream(full: string, from: number, open: boolean): { lines: string[]; upTo: number } {
	const { sentences, rest } = takeSentences(full.slice(from), 20, open);
	const lines = sentences.map(speakable);
	if (!open) lines.push(speakable(rest));
	return { lines: lines.filter(Boolean), upTo: open ? full.length - rest.length : full.length };
}

/**
 * What is safe and sensible to read aloud: markdown structure and code removed,
 * link text kept, a fenced block said to be on screen, each list item ended so
 * the voice pauses, whitespace normalised. A bare URL is left for the adapter,
 * which says its host (spoken_form). A reply the eye reads as **bold** with a
 * `code` span is spoken as its words.
 */
export function speakable(text: string): string {
	return sayBlocks(String(text ?? ''))
		.replace(/`[^`]*`/g, ' ') // inline code
		.replace(/!\[[^\]]*\]\([^)]*\)/g, ' ') // images
		.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1') // links → their words
		.replace(/^\s{0,3}#{1,6}\s+/gm, '') // heading marks
		// "- Google\n- Facebook" is two sentences, not "Google Facebook" run together.
		.replace(/^[ \t]*(?:[-*+•]|\d+[.)])[ \t]+(.+?)[ \t]*$/gm, (_, item: string) => (/[.!?:;]$/.test(item) ? item : `${item}.`))
		.replace(/^[ \t]*>+[ \t]?/gm, '') // quote marks; "x > 5" is the adapter's to say
		// Stray inline marks. An underscore inside a word stays: the adapter reads "tok_1a2b3c4d"
		// as an id and snake_case as two words, and the console must not decide differently.
		.replace(/[*~`#|]|(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])/g, '')
		.replace(/\s+/g, ' ')
		.replace(/\s+([.,;:!?]+)(?=\s|$)/g, '$1') // ".env" keeps its space
		.trim();
}

/** What `SpeechQueue` needs of a player — a subset of `$lib/audio/playback` `Player`. */
export interface SpeechPlayer {
	playBytes(bytes: ArrayBuffer): Promise<void>;
	playPcm(stream: PcmStream): Promise<void>;
	stopAll(): void;
}

/** A line of speech: a whole encoded clip, or PCM arriving as it is made (`$lib/audio/playback`). */
export type Clip = ArrayBuffer | PcmStream;

/**
 * Synthesise one utterance to a clip, or `null` when it could not be made.
 * `voice` names whose voice: a persona's reference clip on the CosyVoice shim.
 * A queue carries lines from more than one speaker -- JARVIS's reply and a
 * subagent's report in its own voice -- so the voice rides with the line, not
 * with the queue. A streamed clip is started here, ahead, and read when its
 * turn comes: its pieces gather in `pieces` meanwhile, so the synthesiser is
 * never held open by a line that is still waiting to be played.
 */
export type Synthesiser = (text: string, voice?: string) => Promise<Clip | null>;

export interface SpeechQueueOptions {
	synth: Synthesiser;
	player: SpeechPlayer;
	/** How many utterances to synthesise ahead of the one playing. */
	ahead?: number;
	/** Called true when playback starts and false when the queue goes idle. */
	onActive?: (active: boolean) => void;
}

/**
 * A queue that synthesises sentences one or two ahead and plays them strictly
 * in the order they were pushed, even when a later synthesis finishes first.
 * `stop()` is barge-in: it silences playback and abandons the queue, and the
 * instance is not reused — the caller makes a fresh one for the next turn.
 */
export class SpeechQueue {
	private readonly synth: Synthesiser;
	private readonly player: SpeechPlayer;
	private readonly ahead: number;
	private readonly onActive?: (active: boolean) => void;

	private texts: string[] = [];
	private voices: (string | undefined)[] = [];
	private ranks: number[] = [];
	private synths: (Promise<Clip | null> | null)[] = [];
	private playIdx = 0;
	private running = false;
	private dead = false;

	constructor(opts: SpeechQueueOptions) {
		this.synth = opts.synth;
		this.player = opts.player;
		this.ahead = opts.ahead ?? 2;
		this.onActive = opts.onActive;
	}

	/** True while a sentence is playing or waiting to. */
	get active(): boolean {
		return this.running;
	}
	/** Utterances pushed but not yet played. */
	get pending(): number {
		return this.texts.length - this.playIdx;
	}

	/**
	 * Queue one sentence, in `voice` (the synthesiser's default when none).
	 * Ignored after `stop()`.
	 *
	 * `rank` is `speechQueue.ts`'s priority (lower speaks first; the primary's
	 * reply is 2). A line goes in behind everything of its rank or better that
	 * is still waiting, and ahead of what is lower -- so a subagent's report
	 * (3) waits for the reply's remaining sentences, and a reply that arrives
	 * while a report waits goes first. The sentence being played is never cut:
	 * the insertion point starts after the play head.
	 */
	push(text: string, voice?: string, rank = 2): void {
		const t = String(text ?? '').trim();
		if (this.dead || !t) return;
		let at = this.texts.length;
		while (at > this.playIdx + 1 && this.ranks[at - 1] > rank) at -= 1;
		this.texts.splice(at, 0, t);
		this.voices.splice(at, 0, voice);
		this.ranks.splice(at, 0, rank);
		this.synths.splice(at, 0, null);
		this.fill();
		void this.run();
	}

	/** Start synthesis for the play head and up to `ahead` beyond it. */
	private fill(): void {
		for (let i = this.playIdx; i < this.texts.length && i <= this.playIdx + this.ahead; i++) {
			if (this.synths[i] == null) this.synths[i] = this.safeSynth(this.texts[i], this.voices[i]);
		}
	}
	private async safeSynth(text: string, voice?: string): Promise<Clip | null> {
		try {
			return await this.synth(text, voice);
		} catch {
			return null;
		}
	}

	private async run(): Promise<void> {
		if (this.running || this.dead) return;
		this.running = true;
		this.onActive?.(true);
		try {
			while (this.playIdx < this.texts.length && !this.dead) {
				this.fill();
				const clip = await this.synths[this.playIdx];
				if (this.dead) break;
				if (clip) {
					try {
						if (clip instanceof ArrayBuffer) await this.player.playBytes(clip);
						else await this.player.playPcm(clip);
					} catch {
						// A clip that will not decode is skipped, not fatal to the rest.
					}
				}
				if (this.dead) break;
				this.playIdx += 1;
			}
		} finally {
			this.running = false;
			if (!this.dead) this.onActive?.(false);
		}
	}

	/** Barge-in: stop playback, drop the queue, and refuse further pushes. */
	stop(): void {
		if (this.dead) return;
		this.dead = true;
		this.texts = [];
		this.voices = [];
		this.ranks = [];
		this.synths = [];
		this.player.stopAll();
		this.onActive?.(false);
	}
}
