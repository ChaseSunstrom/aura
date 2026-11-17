import { afterEach, describe, expect, it } from 'vitest';
import {
	NOT_A_PHONE,
	PHONE_ACTION_EVENT,
	PHONE_EVENTS,
	PHONE_TRANSCRIPT_EVENT,
	phoneBridge,
	phoneHears,
	phoneResultArg,
	phoneWords
} from './phone';

describe("the phone app's bridge", () => {
	afterEach(() => {
		delete (globalThis as { JarvisPhone?: unknown }).JarvisPhone;
		delete (globalThis as { window?: unknown }).window;
	});

	it('is there only when the app gave one', () => {
		expect(phoneBridge()).toBeNull();
		(globalThis as { JarvisPhone?: unknown }).JarvisPhone = { event: () => undefined };
		expect(phoneBridge()).not.toBeNull();
	});

	it('forwards the phone events, and answers a phone action on the plugin command', () => {
		expect(PHONE_EVENTS.has('hud.phone.media') && PHONE_EVENTS.has('hud.phone.action') && PHONE_EVENTS.has('hud.phone.routines')).toBe(true);
		// A route asked for on the phone (the car not linked yet) went nowhere: only the
		// car's own session read it (the owner, 2026-10-07: "it doesn't route at all").
		expect(PHONE_EVENTS.has('hud.car.navigate')).toBe(true);
		expect(PHONE_EVENTS.has('hud.say')).toBe(false);
		// A browser answers a phone action it cannot carry out, as an error, so the tool
		// stops waiting (2026-10-06: three 90 s waits in one turn).
		expect(PHONE_EVENTS.has(PHONE_ACTION_EVENT)).toBe(true);
		expect(phoneResultArg('s1', 'a1', NOT_A_PHONE)).toContain('"status":"error"');
		expect(phoneResultArg('s1', 'a-1', { ok: true })).toBe('@s1 phone-result a-1 {"ok":true}');
		expect(phoneResultArg('s1', 'bad id', {})).toBeNull();
		expect(phoneResultArg('', 'a1', {})).toBeNull();
	});

	it('says whether the phone writes down what is said, and no for an old app or a browser', () => {
		expect(phoneHears()).toBe(false);
		const bridge: { event: () => void; hears: () => unknown } = { event: () => undefined, hears: () => true };
		(globalThis as { JarvisPhone?: unknown }).JarvisPhone = bridge;
		expect(phoneHears()).toBe(true);
		// An app that answers anything but a yes is not taken at its word.
		bridge.hears = () => 'words';
		expect(phoneHears()).toBe(false);
		(globalThis as { JarvisPhone?: unknown }).JarvisPhone = { event: () => undefined };
		expect(phoneHears()).toBe(false);
	});

	it("takes the phone's words for its own recording, and its reason when it has none", async () => {
		// The suite runs in node: the page's window, as far as the bridge's answer needs one.
		(globalThis as { window?: unknown }).window = new EventTarget();
		const asked: string[] = [];
		(globalThis as { JarvisPhone?: unknown }).JarvisPhone = {
			event: () => undefined,
			transcribe: (id: string, wav: string) => {
				asked.push(wav);
				// Someone else's answer first: it is not this recording's.
				window.dispatchEvent(new CustomEvent(PHONE_TRANSCRIPT_EVENT, { detail: { id: 'other', text: 'no' } }));
				window.dispatchEvent(new CustomEvent(PHONE_TRANSCRIPT_EVENT, { detail: { id, text: 'lights off' } }));
			}
		};
		await expect(phoneWords('data:audio/wav;base64,AAAA')).resolves.toBe('lights off');
		expect(asked).toEqual(['data:audio/wav;base64,AAAA']);
		(globalThis as { JarvisPhone?: unknown }).JarvisPhone = {
			event: () => undefined,
			transcribe: (id: string) =>
				window.dispatchEvent(new CustomEvent(PHONE_TRANSCRIPT_EVENT, { detail: { id, error: 'no model' } }))
		};
		await expect(phoneWords('data:,')).rejects.toThrow('no model');
		(globalThis as { JarvisPhone?: unknown }).JarvisPhone = { event: () => undefined, transcribe: () => undefined };
		await expect(phoneWords('data:,', 5)).rejects.toThrow('too long');
	});
});
