import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HermesLink } from '$lib/hermes/link';
import { freshTurn, type ToolCard, type TurnState } from '$lib/hermes/hud';

// While a reply streams, a mark at its very end waits for the next character (T-0729: "wallet."
// may be "wallet.nu.fi"). A tool run is where the text pauses longest, so the narration before it
// ("Let me check that page.") must be said when the tool starts, not with the answer (T-0729 review).

beforeEach(() => {
	vi.stubGlobal('navigator', { mediaDevices: {}, userAgent: 'test' });
	vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
	vi.stubGlobal('requestAnimationFrame', () => 1);
	vi.stubGlobal('cancelAnimationFrame', () => {});
});

afterEach(() => vi.unstubAllGlobals());

const tool = (state: ToolCard['state']): ToolCard => ({
	id: 't1',
	name: 'browser_navigate',
	context: '',
	args: {},
	state,
	progress: '',
	duration: null,
	error: null,
	result: null,
	startedAt: 2,
	finishedAt: null
});

async function speaker() {
	const { VoiceController } = await import('./voice.svelte');
	const voice = new VoiceController({} as HermesLink);
	const said: string[] = [];
	Object.assign(voice, { newSpeech: () => ({ push: (line: string) => said.push(line), stop: () => {} }) });
	return { said, speak: (turn: TurnState) => voice.speakTurn(turn, true) };
}

describe('speakTurn', () => {
	it('says the narration before a tool when the tool starts', async () => {
		const { said, speak } = await speaker();
		const turn: TurnState = { ...freshTurn(), running: true, startedAt: 1, text: 'Let me check that page for you.' };
		speak(turn);
		expect(said).toEqual([]); // the stream may still be mid-word
		speak({ ...turn, tools: [tool('running')] });
		expect(said).toEqual(['Let me check that page for you.']);
		speak({ ...turn, tools: [tool('done')], text: turn.text + ' It is up.', running: false, finishedAt: 3 });
		expect(said).toEqual(['Let me check that page for you.', 'It is up.']);
	});
});

// T-1159: a locked console spoke a finished helper's report, hud.say and a streamed reply aloud.
describe('a held voice', () => {
	it('speaks nothing new from a reply, a helper or hud.say, and speaks again once resumed', async () => {
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		const said: string[] = [];
		Object.assign(voice, { newSpeech: () => ({ push: (line: string) => said.push(line), stop: () => {} }) });
		voice.holdOpenMic();
		const turn: TurnState = { ...freshTurn(), running: true, startedAt: 1, text: 'The first sentence is long enough. Thr' };
		voice.speakTurn(turn, true);
		voice.sayAs('A helper is done.', 'ash', true);
		voice.say('Said aloud.', 'ash');
		expect(said).toEqual([]);
		voice.resumeOpenMic();
		// the reply goes on: only what streams after the hold is spoken, not its first words again
		voice.speakTurn({ ...turn, text: turn.text + 'ee sentences are said after the hold. Four.' }, true);
		expect(said).toEqual(['Three sentences are said after the hold.']);
		voice.sayAs('A helper is done.', 'ash', true);
		voice.say('Said aloud.', 'ash');
		expect(said).toEqual(['Three sentences are said after the hold.', 'A helper is done.', 'Said aloud.']);
	});
});

// T-0906: the voice answered 503 and its fallback 400 for a day, and every reply was silent
// with nothing on the screen to say why.
describe('a line that cannot be spoken', () => {
	it('says so when neither the streamed voice nor the whole line answers', async () => {
		vi.stubGlobal('fetch', async () => new Response('{}', { status: 503 }));
		const { VoiceController } = await import('./voice.svelte');
		const voice = new VoiceController({} as HermesLink);
		const synth = (voice as unknown as { synthLine(text: string, voice: string | undefined, first: boolean): Promise<unknown> }).synthLine.bind(voice);
		expect(await synth('Hello.', undefined, true)).toBeNull();
		expect(voice.notice).toBe('This reply could not be spoken: the voice is not answering.');
	});
});
