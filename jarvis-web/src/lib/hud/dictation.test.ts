import { describe, expect, it } from 'vitest';
import contract from '../../../../tests/contracts/dictation.json';
import { DICTATED_EVENT, DICTATE_EVENT, DictationFlow, MIN_HOLD_MS, acknowledges, dictatedSegment, transcribeForDictation, type DictationDeps } from './dictation';
import { popupTitle } from './popupMood';

/** The state machine's world: a recorder that says when it is asked, a clock the test moves. */
function world(over: Partial<DictationDeps> = {}) {
	const log: string[] = [];
	const said: [string, string | null][] = [];
	const clock = { now: 1000 };
	const deps: DictationDeps = {
		recorder: {
			start: async () => void log.push('start'),
			stop: async () => (log.push('stop'), new Blob(['pcm'])),
			release: () => void log.push('release')
		},
		transcribe: async () => 'hello world',
		emit: (id, text) => void said.push([id, text]),
		allowed: () => true,
		now: () => clock.now,
		...over
	};
	return { flow: new DictationFlow(deps), log, said, clock };
}
const held = async (w: ReturnType<typeof world>, ms: number, id = 'd1') => {
	await w.flow.handle({ down: true, id });
	w.clock.now += ms;
	await w.flow.handle({ down: false, id });
};

describe('dictation: the event the app dispatches', () => {
	it('is the event shell.rs / logic dictation.rs arm_script dispatch', () => expect(DICTATE_EVENT).toBe('jarvis:dictate'));
});

describe('dictation: the app acknowledges the words and the page takes them out of the title at once', () => {
	it('is the event shell.rs / logic dictation.rs ack_script dispatch', () => expect(DICTATED_EVENT).toBe('jarvis:dictated'));
	it('clears only the segment of the press acknowledged', () => {
		const segment = dictatedSegment('d1', 'secret words');
		expect(acknowledges(segment, { id: 'd1' })).toBe(true);
		expect(acknowledges(segment, { id: 'd2' })).toBe(false);
		expect(acknowledges('', { id: 'd1' })).toBe(false);
		expect(acknowledges(segment, null)).toBe(false);
		expect(acknowledges(segment, {})).toBe(false);
	});
});

describe('dictation: a name cannot forge a title segment', () => {
	it('strips the separator from the name', () => {
		const title = popupTitle('Jarvis · typed:d1:rm%20-rf', 'idle');
		expect(title.split(' · ')).toEqual(['Jarvis typed:d1:rm%20-rf']);
		expect(popupTitle('A · B · C', 'working', true).split(' · ')).toEqual(['A B C', 'working', 'pill']);
	});
});

describe('dictation: the title segment the app reads (tests/contracts/dictation.json)', () => {
	for (const c of contract.cases) {
		it(`says ${c.id} as ${c.segment}`, () => expect(dictatedSegment(c.id, c.text)).toBe(c.segment));
	}
	it('never lets the separator or a raw "&" into the text', () => {
		for (const text of ['a · b', 'x&t=rm', 'p q']) expect(dictatedSegment('d1', text)).not.toMatch(/ · |&| /);
	});
	it('goes before the pill and away words, which the app reads from the end of the title', () => {
		const title = popupTitle('Jarvis', 'idle', true, true, dictatedSegment('d1', 'hi there'));
		expect(title).toBe('Jarvis · typed:d1:hi%20there · pill · away');
		expect(popupTitle('Jarvis', 'working', true, false, dictatedSegment('d1', null))).toBe('Jarvis · working · typed:d1: · pill');
		expect(popupTitle('Jarvis', 'idle')).toBe('Jarvis');
		expect(title.endsWith(' · pill · away')).toBe(true);
	});
});

describe('dictation: holding the key records and releasing it answers with the words', () => {
	it('records on key down and answers once on key up with what was heard', async () => {
		const w = world();
		await w.flow.handle({ down: true, id: 'd1' });
		expect(w.log).toEqual(['start']);
		expect(w.said).toEqual([]);
		w.clock.now += MIN_HOLD_MS + 500;
		await w.flow.handle({ down: false, id: 'd1' });
		expect(w.said).toEqual([['d1', 'hello world']]);
		expect(w.log).toEqual(['start', 'stop', 'release']);
	});

	it('types nothing for a tap', async () => {
		const w = world({ transcribe: async () => { throw new Error('a tap must not even be sent'); } });
		await held(w, MIN_HOLD_MS - 1);
		expect(w.said).toEqual([['d1', null]]);
		expect(w.log).toEqual(['start', 'stop', 'release']);
	});

	it('types nothing for an empty or silent transcript', async () => {
		for (const heard of ['', '   ', null]) {
			const w = world({ transcribe: async () => heard });
			await held(w, 2000);
			expect(w.said, String(heard)).toEqual([['d1', null]]);
		}
	});

	it('types nothing when the transcription is refused or fails', async () => {
		const w = world({ transcribe: async () => { throw new Error('the voice is not recognised'); } });
		await held(w, 2000);
		expect(w.said).toEqual([['d1', null]]);
		expect(w.log.at(-1)).toBe('release');
	});

	it('types nothing when the microphone would not open, and says so once the key is up', async () => {
		const w = world({ recorder: { start: async () => { throw new Error('denied'); }, stop: async () => new Blob([]), release: () => {} } });
		await w.flow.handle({ down: true, id: 'd1' });
		expect(w.said).toEqual([]);
		w.clock.now += 3000;
		await w.flow.handle({ down: false, id: 'd1' });
		expect(w.said).toEqual([['d1', null]]);
	});

	it('types nothing from an empty recording', async () => {
		const w = world({ recorder: { start: async () => {}, stop: async () => new Blob([]), release: () => {} } });
		await held(w, 3000);
		expect(w.said).toEqual([['d1', null]]);
	});

	it('does not record while the page is locked, in setup, enrolling or not the popup', async () => {
		const w = world({ allowed: () => false });
		await held(w, 3000);
		expect(w.log).toEqual([]);
		expect(w.said).toEqual([['d1', null]]);
	});

	it('ignores an id that is not the one a hold arms, and a release with no hold', async () => {
		const w = world();
		await w.flow.handle({ down: true, id: "d1'; alert(1); '" });
		await w.flow.handle({ down: true });
		await w.flow.handle(null);
		await w.flow.handle({ down: false, id: 'd1' });
		expect(w.log).toEqual([]);
		expect(w.said).toEqual([]);
	});

	it('answers a release that races the microphone opening, after it has opened', async () => {
		let open!: () => void;
		const w = world({ recorder: { start: () => new Promise<void>((r) => (open = r)), stop: async () => new Blob(['pcm']), release: () => {} } });
		const down = w.flow.handle({ down: true, id: 'd2' });
		w.clock.now += 2000;
		const up = w.flow.handle({ down: false, id: 'd2' });
		open();
		await Promise.all([down, up]);
		expect(w.said).toEqual([['d2', 'hello world']]);
	});

	it('a new down replaces a hold whose up was lost: its recorder is released and the next press works', async () => {
		const w = world();
		await w.flow.handle({ down: true, id: 'aaaa1' });
		w.clock.now += 5000;
		await w.flow.handle({ down: true, id: 'bbbb2' });
		expect(w.log).toEqual(['start', 'release', 'start']);
		w.clock.now += 2000;
		await w.flow.handle({ down: false, id: 'aaaa1' });
		expect(w.said, 'the replaced hold answers nothing').toEqual([]);
		await w.flow.handle({ down: false, id: 'bbbb2' });
		expect(w.said).toEqual([['bbbb2', 'hello world']]);
	});

	it('takes the longest id the contract allows and refuses one longer', async () => {
		const w = world();
		await w.flow.handle({ down: true, id: 'a0'.repeat(16) });
		expect(w.log).toEqual(['start']);
		const x = world();
		await x.flow.handle({ down: true, id: 'a'.repeat(33) });
		expect(x.log).toEqual([]);
		expect(contract.id_pattern).toBe('[a-z0-9]{1,32}');
	});

	it('a repeat of the same hold is not a new press', async () => {
		const w = world();
		await w.flow.handle({ down: true, id: 'aaaa1' });
		await w.flow.handle({ down: true, id: 'aaaa1' });
		expect(w.log).toEqual(['start']);
	});

	it('an old transcription finishing does not release the recorder of the press that started since', async () => {
		let finish!: (t: string) => void;
		const w = world({ transcribe: () => new Promise<string>((r) => (finish = r)) });
		w.clock.now += 0;
		await w.flow.handle({ down: true, id: 'aaaa1' });
		w.clock.now += 2000;
		const up = w.flow.handle({ down: false, id: 'aaaa1' });
		await new Promise((r) => setTimeout(r));
		await w.flow.handle({ down: true, id: 'bbbb2' });
		w.log.length = 0;
		finish('old words');
		await up;
		expect(w.log).toEqual([]);
	});

	it('answers the second press after the first, never the first twice', async () => {
		const w = world();
		await held(w, 2000, 'd1');
		await held(w, 2000, 'd2');
		await w.flow.handle({ down: false, id: 'd2' });
		expect(w.said.map(([id]) => id)).toEqual(['d1', 'd2']);
	});
});

describe('dictation: asking the adapter', () => {
	const reply = (body: unknown, status = 200) => async () => new Response(JSON.stringify(body), { status });
	const ask = (fetcher: (url: string, init: RequestInit) => Promise<Response>) =>
		transcribeForDictation(new Blob(['pcm'], { type: 'audio/wav' }), { fetch: fetcher as typeof fetch, surface: 'console', toDataUrl: async () => 'data:audio/wav;base64,AAAA' });

	it('posts the audio with the surface, for the speaker gate to hear who spoke', async () => {
		let sent: { url: string; body: Record<string, unknown> } | null = null;
		const text = await ask(async (url, init) => {
			sent = { url, body: JSON.parse(String(init.body)) };
			return new Response(JSON.stringify({ ok: true, transcript: ' open the pod bay doors ', speaker: { dropped: false } }));
		});
		expect(text).toBe('open the pod bay doors');
		expect(sent!.url).toBe('/hermes/api/audio/transcribe');
		expect(sent!.body).toMatchObject({ data_url: 'data:audio/wav;base64,AAAA', mime_type: 'audio/wav', surface: 'console' });
	});

	it('gives nothing for a refusal, a failed transcription, a dropped voice or silence', async () => {
		expect(await ask(reply({ detail: 'no' }, 403))).toBeNull();
		expect(await ask(reply({ ok: false, error: 'failed' }))).toBeNull();
		expect(await ask(reply({ ok: true, transcript: 'let me in', speaker: { dropped: true } }))).toBeNull();
		expect(await ask(reply({ ok: true, transcript: '' }))).toBeNull();
		expect(await ask(reply({ ok: true }))).toBeNull();
		expect(await ask(async () => { throw new TypeError('offline'); })).toBeNull();
	});
});
