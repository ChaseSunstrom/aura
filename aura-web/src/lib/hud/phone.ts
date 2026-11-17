import { wornName } from '$lib/ds/theme.svelte';
// The phone app's console home (`MainActivity` + `ConsolePage`, the owner, 2026-09-27:
// "the mobile app UI ... doesn't really match the look/feel of the console"): the page is
// the console, but some of what Jarvis does is the phone's to do -- play its music, send a
// text, watch a routine's trigger. Those arrive on this page's Hermes session as events,
// and without the native conversation there was nothing to carry them out. The app hands
// `window.JarvisPhone` to the page; the page forwards those events to it, and the phone's
// answer to a `phone_action` comes back as `jarvis:phone-result` for the page to send on.
//
// Does not cover a browser: no bridge, nothing forwarded, and a phone action there fails
// the way it always did ("no phone answered").

/** What the phone's app gives the page (`@JavascriptInterface`, strings only). */
export interface PhoneBridge {
	event(type: string, json: string): void;
	/** One of the phone's own screens (Settings → This phone): its settings, the car, its tasks. */
	open?(screen: PhoneScreen): void;
	/** Whether the phone is linked to the car now: the page then lets go of the microphone. */
	carLinked?(): boolean;
	/** Whether the phone writes down what is said here ([phoneHears]); absent in an older app. */
	hears?(): boolean;
	/** Transcribe a WAV data URL on the phone; answered as [PHONE_TRANSCRIPT_EVENT]. */
	transcribe?(id: string, wav: string): void;
}

/**
 * Whether the phone writes down what is said to this page (the owner, 2026-10-08: "on
 * phone transcription isnt working" -- the phone's switch reached only its native screen).
 * The recording still goes to the adapter with the phone's words, for the speaker gate to
 * hear: the phone's idea of whether the gate enforces can be stale, and words alone would
 * pass a stranger as the key holder. Room tone with no words in it stays on the phone.
 */
export function phoneHears(): boolean {
	return phoneBridge()?.hears?.() === true;
}

export const PHONE_TRANSCRIPT_EVENT = 'jarvis:phone-transcript';

/** The first one opens the model (seconds); an utterance after that is well under one. */
const PHONE_WORDS_MS = 20_000;

let asked = 0;

/** The phone's words for a recording; rejects with the phone's reason, or when it is too slow. */
export function phoneWords(wav: string, timeoutMs = PHONE_WORDS_MS): Promise<string> {
	const bridge = phoneBridge();
	if (!bridge?.transcribe) return Promise.reject(new Error('this screen is not the phone'));
	const id = `stt-${++asked}`;
	return new Promise((resolve, reject) => {
		const heard = (e: Event) => {
			const said = (e as CustomEvent<{ id?: string; text?: string; error?: string }>).detail ?? {};
			if (said.id !== id) return;
			done();
			if (said.error) reject(new Error(said.error));
			else resolve(String(said.text ?? ''));
		};
		const timer = setTimeout(() => {
			done();
			reject(new Error('the phone took too long'));
		}, timeoutMs);
		const done = () => {
			clearTimeout(timer);
			window.removeEventListener(PHONE_TRANSCRIPT_EVENT, heard);
		};
		window.addEventListener(PHONE_TRANSCRIPT_EVENT, heard);
		bridge.transcribe!(id, wav);
	});
}

/** The car page's web view (`CarHud`), known from the first frame, before any car push. */
export const onCarPage = (): boolean => typeof navigator !== 'undefined' && navigator.userAgent.includes('JarvisCar');

/** An Android device, where a held web microphone is a call to the system. */
export const onAndroid = (): boolean => typeof navigator !== 'undefined' && /Android/.test(navigator.userAgent);

/** The phone's native screens the console links to; the phone and car options stay native. */
export type PhoneScreen = 'phone' | 'car' | 'tasks' | 'voice' | 'check';

export const PHONE_SCREENS: readonly { screen: PhoneScreen; label: string; hint: string }[] = [
	{ screen: 'phone', label: 'Phone settings', hint: 'Wake word, listening in the car, texts and calls, the headset, updates' },
	{ screen: 'car', label: 'Car setup', hint: 'Pair the car, its Wi-Fi, and what shows on its screen' },
	{ screen: 'tasks', label: 'Phone tasks', hint: 'Automations that run on this phone' },
	{ screen: 'voice', label: 'Voice on this phone', hint: 'Enrol and test your voice with this phone\'s microphone' },
	{ screen: 'check', label: 'System check', get hint() { return `Permissions and access ${wornName()} needs, one tap each`; } }
];

/**
 * The events the phone, not the page, carries out. `hud.car.navigate` too: a route asked
 * for here, with the car not linked, used to be read only by the car's own session.
 */
export const PHONE_EVENTS: ReadonlySet<string> = new Set([
	'hud.phone.media',
	'hud.phone.action',
	'hud.phone.routines',
	'hud.car.navigate'
]);

export const ROUTINES_EVENT = 'hud.phone.routines';

export const PHONE_ACTION_EVENT = 'hud.phone.action';

/** What a screen with no phone answers a phone action with, so the tool does not wait 90 s. */
export const NOT_A_PHONE = {
	status: 'error',
	error: `this screen is not the phone; ${wornName()} reaches the phone through the house when it is online`
};

/** Long enough for a phone on the same conversation to answer first. */
export const NOT_A_PHONE_AFTER_MS = 8000;

/** The bridge, when this page is the phone app's home screen. */
export function phoneBridge(): PhoneBridge | null {
	const b = (globalThis as { JarvisPhone?: Partial<PhoneBridge> }).JarvisPhone;
	return b && typeof b.event === 'function' ? (b as PhoneBridge) : null;
}

/** `/hud @<sid> phone-result <id> <json>`: the phone's answer, for the waiting tool call. */
export function phoneResultArg(sessionId: string, id: string, body: unknown): string | null {
	if (!sessionId || !id || !/^[\w-]+$/.test(id)) return null;
	return `@${sessionId} phone-result ${id} ${JSON.stringify(body ?? {})}`;
}
