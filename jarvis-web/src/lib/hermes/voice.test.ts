import { describe, it, expect } from 'vitest';
import {
	GOAL_TRIGGERS,
	dataUrlMime,
	dataUrlToBytes,
	goalFromPhrase,
	isGoalPhrase,
	orbLabel,
	orbStateFor,
	transcriptIsEmpty,
	isStop,
	isWakeOnly
} from './voice';

describe('goal triggers', () => {
	it('recognises every trigger the protocol names, at the start of the sentence only', () => {
		for (const trigger of GOAL_TRIGGERS) {
			expect(isGoalPhrase(`${trigger} the tests pass`), trigger).toBe(true);
			expect(isGoalPhrase(`please ${trigger} it`), trigger).toBe(false);
		}
		expect(isGoalPhrase('turn on the lab lights')).toBe(false);
	});

	it('is not fooled by case, spacing or a curly apostrophe', () => {
		expect(isGoalPhrase('  Keep   Working On the notes')).toBe(true);
		expect(isGoalPhrase('Don’t stop until it builds')).toBe(true);
	});

	it('takes the goal after “set a goal”, and the whole sentence otherwise', () => {
		expect(goalFromPhrase('set a goal: tidy the notes')).toBe('tidy the notes');
		expect(goalFromPhrase('Set a goal to tidy the notes')).toBe('tidy the notes');
		expect(goalFromPhrase('keep going until the build is green')).toBe('keep going until the build is green');
		expect(goalFromPhrase('set a goal')).toBe('set a goal');
	});
});

describe('the orb', () => {
	const base = { recording: false, running: false, gotToken: false, speaking: false, warming: false, error: false };
	it('wears idle, listening, thinking, speaking, error in that order of precedence', () => {
		expect(orbStateFor(base)).toBe('idle');
		expect(orbStateFor({ ...base, running: true })).toBe('thinking');
		expect(orbStateFor({ ...base, running: true, gotToken: true })).toBe('thinking');
		expect(orbStateFor({ ...base, warming: true })).toBe('thinking');
		expect(orbStateFor({ ...base, speaking: true, running: true })).toBe('speaking');
		expect(orbStateFor({ ...base, recording: true, speaking: true })).toBe('listening');
		expect(orbStateFor({ ...base, error: true, recording: true })).toBe('error');
	});

	it('says WARMING UP only while thinking with the model cold', () => {
		expect(orbLabel('thinking', true)).toBe('WARMING UP');
		expect(orbLabel('thinking', false)).toBe('PROCESSING');
		expect(orbLabel('idle', true)).toBe('STANDBY');
		expect(orbLabel('listening', false)).toBe('LISTENING');
		expect(orbLabel('speaking', false)).toBe('RESPONDING');
	});
});

describe('transcripts and audio', () => {
	it('treats an empty or whitespace transcript as silence', () => {
		expect(transcriptIsEmpty('')).toBe(true);
		expect(transcriptIsEmpty('   ')).toBe(true);
		expect(transcriptIsEmpty(null)).toBe(true);
		expect(transcriptIsEmpty('hello')).toBe(false);
	});

	it('knows a transcript that is only the wake phrase (T-0769)', () => {
		for (const said of ['Jarvis', 'Jarvis.', 'Hey Jarvis.', 'Hey, Jarvis!', 'hey jarvis', 'Okay Jarvis?', 'OK, Jarvis.', 'Hi Jarvis', ' Jervis. ', 'Hey Javis', 'Aura', 'Hey Aura.', 'Hey, Aura!'])
			expect(isWakeOnly(said), said).toBe(true);
		// More than the name is a turn, and so is a sentence that only mentions him.
		for (const said of ['Jarvis, lights on.', 'Hey Jarvis turn on the lights', 'I told Jarvis.', 'Thank you, Jarvis.', 'Jarvisson', '', 'Hey.', 'Laura', 'Hey Laura.', 'Aurora', 'Hey Ora', 'Aura, lights on.'])
			expect(isWakeOnly(said), said).toBe(false);
	});

	it('decodes a data URL to its bytes and reads its type', () => {
		const url = `data:audio/wav;base64,${Buffer.from('RIFF').toString('base64')}`;
		expect(dataUrlMime(url)).toBe('audio/wav');
		expect(new TextDecoder().decode(dataUrlToBytes(url))).toBe('RIFF');
		expect(() => dataUrlToBytes('http://x')).toThrow('not a data URL');
	});
});

describe('isStop', () => {
	it('is a stop said on its own, with or without the name', () => {
		for (const said of ['stop', 'Stop.', 'cancel', 'never mind', 'Nevermind!', 'Jarvis, stop', 'stop it', 'cancel that', 'hold on', 'wait', 'shut up', 'Aura stop.', 'okay stop', 'stop, Jarvis', 'stop stop', 'Stop, stop!', 'stop it please', "that's enough", 'quiet', 'stop now']) {
			expect(isStop(said), said).toBe(true);
		}
	});

	it('is not a sentence that only contains one', () => {
		for (const said of ['', 'stop the kitchen timer', 'cancel my three o\'clock', 'wait for the oven', 'and check the garage too', 'do not stop', null]) {
			expect(isStop(said), String(said)).toBe(false);
		}
	});
});
