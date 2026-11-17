import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, vi } from 'vitest';
import { SpeechQueue, cutStream, speakable, takeSentences, type SpeechPlayer } from './speech';

// The first-clause rule lives in the adapter (aura_adapter/clause.py); the console's part is to
// say which line starts a reply. The flag's name is the shared table's (T-0605).
describe('first clause', () => {
	it('asks the adapter for it on the first line of a reply', () => {
		const table = JSON.parse(
			readFileSync(fileURLToPath(new URL('../../../../tests/contracts/first-clause.json', import.meta.url)), 'utf8')
		);
		const src = readFileSync(fileURLToPath(new URL('../hud/voice.svelte.ts', import.meta.url)), 'utf8');
		expect(src).toContain(`${table.requestFlag}: firstClause`);
	});
});

describe('takeSentences', () => {
	it('splits complete sentences and keeps the incomplete remainder', () => {
		const { sentences, rest } = takeSentences('The kitchen is quite warm. The lab is rather cool. And the hall');
		expect(sentences).toEqual(['The kitchen is quite warm.', 'The lab is rather cool.']);
		expect(rest).toBe(' And the hall');
	});

	it('does not cut on a short fragment — a decimal or an abbreviation waits', () => {
		// "It is 22.5 degrees" — the "." after 22 is mid-number; the fragment is
		// short so it joins the next.
		const { sentences, rest } = takeSentences('It is 22.5 degrees in here right now.');
		expect(sentences).toEqual(['It is 22.5 degrees in here right now.']);
		expect(rest).toBe('');
	});

	it('ends a sentence at a newline too', () => {
		const { sentences } = takeSentences('The standard search is coming back empty, Sir\nPrice data is fine though, Sir\n');
		expect(sentences).toEqual([
			'The standard search is coming back empty, Sir',
			'Price data is fine though, Sir'
		]);
	});

	it('carries a growing stream: the remainder completes on the next chunk', () => {
		const first = takeSentences('Working on the answer for you now');
		expect(first.sentences).toEqual([]);
		expect(first.rest).toBe('Working on the answer for you now');
		const second = takeSentences(first.rest + ', here it is. And more');
		expect(second.sentences).toEqual(['Working on the answer for you now, here it is.']);
	});

	// T-0729: the owner heard a fenced command and a URL tail with a session id spelled out.
	it('holds an open fenced block until it closes, then cuts it whole', () => {
		const open = takeSentences('I would run this for you:\n```bash\ndocker compose up -d\nmake test\n', 20, true);
		expect(open.sentences).toEqual(['I would run this for you:']);
		expect(open.rest).toBe('```bash\ndocker compose up -d\nmake test\n');
		const closed = takeSentences(open.rest + '```\nThen tell me how it went.', 20, true);
		expect(closed.sentences).toEqual(['```bash\ndocker compose up -d\nmake test\n```']);
	});

	it('while the stream is open, a mark at its very end is not yet an end', () => {
		// "wallet." may be "wallet.nu.fi", "22." may be "22.5": wait for what follows.
		expect(takeSentences('Opening the page at https://wallet.', 20, true).sentences).toEqual([]);
		expect(takeSentences('The kitchen is at 22.', 20, true).sentences).toEqual([]);
		expect(takeSentences('The kitchen is at 22. ', 20, true).sentences).toEqual(['The kitchen is at 22.']);
		// A finished text still ends at its last mark.
		expect(takeSentences('The kitchen is at 22.').sentences).toEqual(['The kitchen is at 22.']);
	});

	it('only a fence that starts a line opens a block: three ticks in prose do not hold the rest', () => {
		const cut = takeSentences('Wrap it in ``` fences. Then reload the page and tell me.');
		expect(cut.sentences).toEqual(['Wrap it in ``` fences.', 'Then reload the page and tell me.']);
		const tilde = takeSentences('Run this one for me:\n~~~\nmake test\nmake lint\n', 20, true);
		expect(tilde.sentences).toEqual(['Run this one for me:']);
	});
});

describe('speakable', () => {
	it('strips markdown and code but keeps the words', () => {
		const said = speakable('## Market\n\nThe **lab** is `on` — see [the board](https://x.example) now');
		expect(said).not.toMatch(/[*_`#>|]/);
		expect(said).not.toContain('http');
		expect(said).toContain('Market');
		expect(said).toContain('lab');
		expect(said).toContain('the board'); // the link's words survive
		expect(said).not.toContain('board]'); // but not its target syntax
	});

	it('leaves a bare URL for the adapter, which says its host', () => {
		expect(speakable('It is at https://wallet.nu.fi/auth now.')).toBe('It is at https://wallet.nu.fi/auth now.');
	});

	it('says a fenced block is on screen, closed or cut off', () => {
		expect(speakable('Here is code:\n```\nrm -rf /\n```\ndone')).toBe('Here is code: The code is on screen. done');
		expect(speakable('Here is code:\n```\nrm -rf')).toBe('Here is code: The code is on screen.');
		expect(speakable('Here is code:\n~~~\nrm -rf /\n~~~\ndone')).toBe('Here is code: The code is on screen. done');
		// Three ticks in prose are not a block: the rest of the reply is still said.
		expect(speakable('Wrap it in ``` fences. Then reload the page.')).toBe('Wrap it in fences. Then reload the page.');
	});

	it('leaves an id\'s underscore, a comparison and a dotfile\'s space for the adapter', () => {
		// The adapter takes "tok_1a2b3c4d" for an id; "tok1a2b3c4d" it would read out.
		expect(speakable('The token tok_1a2b3c4d is set; _emphasis_ goes.')).toBe('The token tok_1a2b3c4d is set; emphasis goes.');
		expect(speakable('Edit your .env file.')).toBe('Edit your .env file.');
		expect(speakable('> Quoted: only if x > 5.')).toBe('Quoted: only if x > 5.');
	});

	it('ends each list item, so the voice pauses between them', () => {
		expect(speakable('- **Google**\n- Facebook\n1. MetaMask')).toBe('Google. Facebook. MetaMask.');
	});
});

describe('the NUFI reply (tests/contracts/speech-nufi.json)', () => {
	const table = JSON.parse(
		readFileSync(fileURLToPath(new URL('../../../../tests/contracts/speech-nufi.json', import.meta.url)), 'utf8')
	) as { reply: string; said: string[] };

	it('streamed a few characters at a time, is queued as whole sentences with nothing spelled', () => {
		const sizes = [1, 3, 2, 5, 4, 7];
		const queued: string[] = [];
		let from = 0;
		for (let end = 0, i = 0; end < table.reply.length; i++) {
			end = Math.min(table.reply.length, end + sizes[i % sizes.length]);
			const cut = cutStream(table.reply.slice(0, end), from, true);
			queued.push(...cut.lines);
			from = cut.upTo;
		}
		const last = cutStream(table.reply, from, false);
		queued.push(...last.lines);
		expect(last.upTo).toBe(table.reply.length);
		expect(queued).toEqual(table.said);
	});
});

describe('SpeechQueue', () => {
	const fakePlayer = () => {
		const played: string[] = [];
		const player: SpeechPlayer & { played: string[] } = {
			played,
			async playBytes(bytes: ArrayBuffer) {
				played.push(new TextDecoder().decode(bytes));
			},
			async playPcm(stream) {
				let text = '';
				for await (const piece of stream.pieces) text += new TextDecoder().decode(piece);
				played.push(`pcm@${stream.rate}:${text}`);
			},
			stopAll() {}
		};
		return player;
	};
	const enc = (s: string) => new TextEncoder().encode(s).buffer;
	const tick = () => new Promise((r) => setTimeout(r, 0));

	it('plays sentences in the order pushed, even when a later one synthesises first', async () => {
		const player = fakePlayer();
		// The second synthesis resolves before the first; playback must still be in order.
		const synth = vi.fn(async (text: string) => {
			await new Promise((r) => setTimeout(r, text === 'one' ? 20 : 1));
			return enc(text);
		});
		const q = new SpeechQueue({ synth, player });
		q.push('one');
		q.push('two');
		q.push('three');
		await vi.waitFor(() => expect(player.played).toEqual(['one', 'two', 'three']));
	});

	it('carries each line\'s voice to the synthesiser, so a subagent reports in its own', async () => {
		const player = fakePlayer();
		const synth = vi.fn(async (text: string, voice?: string) => enc(`${voice ?? 'default'}:${text}`));
		const q = new SpeechQueue({ synth, player });
		q.push('The reply.', 'jarvis');
		q.push('The report.', 'aura');
		q.push('Unvoiced.');
		await vi.waitFor(() => expect(player.played).toEqual(['jarvis:The reply.', 'aura:The report.', 'default:Unvoiced.']));
	});

	it('puts a better rank ahead of what waits, never ahead of what is playing', async () => {
		const player = fakePlayer();
		let release: () => void = () => {};
		const gate = new Promise<void>((r) => (release = r));
		const synth = vi.fn(async (text: string) => {
			// The first line plays until released, so the rest is queued behind it.
			if (text === 'first') await gate;
			return enc(text);
		});
		const q = new SpeechQueue({ synth, player, ahead: 0 });
		q.push('first', 'jarvis', 2);
		q.push('report one', 'aura', 3);
		q.push('report two', 'aura', 3);
		// The primary's next sentence arrives while the report waits: it goes first.
		q.push('second', 'jarvis', 2);
		// An approval outranks the reply, but not the sentence being spoken.
		q.push('approve?', 'jarvis', 1);
		release();
		await vi.waitFor(() => expect(player.played.length).toBe(5));
		expect(player.played).toEqual(['first', 'approve?', 'second', 'report one', 'report two']);
	});

	it('synthesises ahead but not the whole queue at once', async () => {
		const player = fakePlayer();
		let inflight = 0;
		let peak = 0;
		const synth = vi.fn(async (text: string) => {
			inflight += 1;
			peak = Math.max(peak, inflight);
			await new Promise((r) => setTimeout(r, 5));
			inflight -= 1;
			return enc(text);
		});
		const q = new SpeechQueue({ synth, player, ahead: 1 });
		for (const s of ['a', 'b', 'c', 'd', 'e']) q.push(s);
		await vi.waitFor(() => expect(player.played.length).toBe(5));
		// ahead:1 means at most the playing one plus one more in flight.
		expect(peak).toBeLessThanOrEqual(2);
	});

	it('barge-in stops playback and drops what is queued', async () => {
		const player = fakePlayer();
		const stop = vi.spyOn(player, 'stopAll');
		const synth = vi.fn(async (text: string) => {
			await new Promise((r) => setTimeout(r, 5));
			return enc(text);
		});
		const q = new SpeechQueue({ synth, player });
		q.push('one');
		q.push('two');
		q.stop();
		await tick();
		await tick();
		expect(stop).toHaveBeenCalled();
		q.push('ignored');
		await tick();
		expect(player.played).not.toContain('ignored');
	});

	it('reports active while speaking and idle when drained', async () => {
		const player = fakePlayer();
		const states: boolean[] = [];
		const synth = async (t: string) => enc(t);
		const q = new SpeechQueue({ synth, player, onActive: (a) => states.push(a) });
		q.push('hello there sir');
		await vi.waitFor(() => expect(q.active).toBe(false));
		expect(states[0]).toBe(true);
		expect(states[states.length - 1]).toBe(false);
	});

	it('plays a streamed clip in its turn, piece by piece, between whole ones', async () => {
		const player = fakePlayer();
		const pieces = async function* () {
			yield new TextEncoder().encode('he');
			await new Promise((r) => setTimeout(r, 5));
			yield new TextEncoder().encode('llo');
		};
		const q = new SpeechQueue({
			synth: async (t) => (t === 'two' ? { rate: 24000, pieces: pieces() } : enc(t)),
			player
		});
		q.push('one');
		q.push('two');
		q.push('three');
		await new Promise((r) => setTimeout(r, 40));
		expect(player.played).toEqual(['one', 'pcm@24000:hello', 'three']);
	});
});
