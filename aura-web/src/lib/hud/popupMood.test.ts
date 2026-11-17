import { describe, expect, it } from 'vitest';
import { SUMMON_EVENT, popupMood, popupRung, popupTitle, summonsToType } from './popupMood';

describe('the desktop app summons the popup to type (T-0696)', () => {
	const summon = (detail: unknown) => new CustomEvent(SUMMON_EVENT, { detail });
	// shell.rs / logic palette.rs dispatch exactly this event name.
	it('is the event the app dispatches', () => expect(SUMMON_EVENT).toBe('jarvis:summon'));
	it('focuses the line only when asked to type', () => {
		expect(summonsToType(summon({ to: 'type' }))).toBe(true);
		expect(summonsToType(summon({ to: 'talk' }))).toBe(false);
		expect(summonsToType(summon({}))).toBe(false);
		expect(summonsToType(summon(null))).toBe(false);
		expect(summonsToType(new Event(SUMMON_EVENT))).toBe(false);
	});
	it('says away in the title once Escape put the island away, so the app hides the window', () => {
		expect(popupTitle('Jarvis', 'idle', false, true)).toBe('Jarvis · away');
		expect(popupTitle('Jarvis', 'working', true, true)).toBe('Jarvis · working · pill · away');
		expect(popupTitle('Jarvis', 'idle')).toBe('Jarvis');
	});
});

describe('the popup ladder', () => {
	const rest = { recording: false, mood: 'idle' as const, exchange: false, expanded: false };
	it('rests as the compact HUD', () => expect(popupRung(rest)).toBe('hud'));
	it('becomes the listening pill while the mic records, whatever else is up', () => {
		expect(popupRung({ ...rest, recording: true })).toBe('listening');
		expect(popupRung({ ...rest, recording: true, mood: 'needs you' })).toBe('listening');
	});
	it('opens to the full assistant for a turn, an answer, a wait or a press', () => {
		expect(popupRung({ ...rest, mood: 'working' })).toBe('assistant');
		expect(popupRung({ ...rest, mood: 'needs you' })).toBe('assistant');
		expect(popupRung({ ...rest, exchange: true })).toBe('assistant');
		expect(popupRung({ ...rest, expanded: true })).toBe('assistant');
	});
});

describe('the popup says what it is doing, for the desktop app', () => {
	it('waiting on the person outranks a notice, which outranks a running turn', () => {
		expect(popupMood({ waiting: 1, notice: true, reactor: 'thinking' })).toBe('needs you');
		expect(popupMood({ waiting: 0, notice: true, reactor: 'thinking' })).toBe('notice');
		expect(popupMood({ waiting: 0, notice: false, reactor: 'speaking' })).toBe('working');
		expect(popupMood({ waiting: 0, notice: false, reactor: 'idle' })).toBe('idle');
		expect(popupMood({ waiting: 0, notice: false, reactor: 'offline' })).toBe('idle');
	});
	// The app's `mood_of` splits on " · " and matches these words exactly.
	it('titles in the shape the app reads', () => {
		expect(popupTitle('Jarvis', 'idle')).toBe('Jarvis');
		expect(popupTitle('AURA', 'needs you')).toBe('AURA · needs you');
		expect(popupTitle('Jarvis', 'working')).toBe('Jarvis · working');
		expect(popupTitle('Jarvis', 'notice')).toBe('Jarvis · notice');
		expect(popupTitle('Jarvis', 'working', true)).toBe('Jarvis · working · pill');
		expect(popupTitle('Jarvis', 'idle', true)).toBe('Jarvis · pill');
	});
});
