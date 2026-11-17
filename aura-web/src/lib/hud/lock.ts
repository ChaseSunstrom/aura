// The lock (the owner, 2026-09-10): "a configurable timeout that will lock
// Jarvis behind a nice lockscreen after a certain amount of time, requiring a
// password (or voice unlock with enrollment if enabled, and has voice unlock
// set)".
//
// What it is, plainly: a screen over the console after a quiet spell, that the
// console password or an enrolled voice takes down. It is the lock on the
// study door -- it keeps a passer-by at the owner's desk off the room. The door
// is the server's (T-0387, `consoleAuth.ts` `admit`): with a password set, the
// relays and the API refuse a browser holding no session, and such a browser
// meets this same pane as its sign-in -- password or passkey, since a voice opens
// no session. Since T-0946 locking shuts that door too: the page ends its session
// (`lockEndsSession`), so the locked tab's cookie opens nothing. The one case it
// does not: voice unlock on, where the session has to outlive the lock for a voice
// to be checked at all, and the lock is the screen it always was.
//
// Pure: the timer's arithmetic and the preferences' parsing, tested on a
// cranked clock; the component owns the DOM, the microphone and the fetches.

/** How long a quiet console stays open: the presets. Minutes; 0 is never. */
export const LOCK_AFTER_CHOICES: readonly number[] = [0, 1, 5, 15, 30, 60];
/**
 * The longest a person may type in beside the presets (the owner, 2026-09-10:
 * "should be customizeable"). Twelve hours: past that "never" is what is meant.
 */
export const LOCK_AFTER_MAX = 720;

export const LOCK_AFTER_KEY = 'jarvis:lock-after';
export const LOCK_VOICE_KEY = 'jarvis:lock-voice';

/** A stored or typed number of minutes: a whole number 1..LOCK_AFTER_MAX, or 0 for anything else. */
export function parseLockAfter(raw: string | number | null | undefined): number {
	const n = Number(raw);
	if (!Number.isInteger(n) || n < 1 || n > LOCK_AFTER_MAX) return 0;
	return n;
}

export interface IdleState {
	/** When the person last did something (ms). */
	activeAt: number;
	/** Minutes of quiet that lock; 0 never locks. */
	afterMin: number;
}

/** True when the console has been quiet for its whole allowance. */
export function shouldLock(state: IdleState, now: number): boolean {
	if (state.afterMin <= 0) return false;
	return now - state.activeAt >= state.afterMin * 60_000;
}

/**
 * Whether locking signs the browser out of the server as well. Not with voice unlock
 * on: a voice opens no session, so ending it would leave the voice nothing to open. Nor
 * in the phone's or the car's web view (`keyed`): its key signs it straight back in on
 * the reload, so the lock would not hold, and that view is behind the phone's own lock.
 */
export function lockEndsSession(voiceUnlock: boolean, keyed = false): boolean {
	return !voiceUnlock && !keyed;
}

/** How the lock explains itself, given what can open it. */
export function lockHint(configured: boolean, voice: boolean, passkey = false): string {
	if (!configured) return 'No console password is set. Choose one in Settings → Console to make the lock hold.';
	const first = passkey ? 'Use your passkey, or type the console password' : 'Type the console password';
	return voice ? `${first}, or say something and let your voice open it.` : `${first}.`;
}

/**
 * The verify relay's verdict, read as one of three: opened, refused, or
 * could not judge (too short, no speech) -- the last is "try again", not
 * "you are not who this belongs to".
 */
export function voiceVerdict(
	body: { verdict?: { accepted?: boolean; reason?: string; label?: string | null; level?: string | null } } | null | undefined
): 'open' | 'refused' | 'unclear' | 'guest' {
	const v = body?.verdict;
	if (!v) return 'unclear';
	// A guest is recognised and still kept out: the lock is the study door,
	// and a guest's voice opens nothing the owner's approval would guard
	// (`voice/speaker.py` LEVELS). Said as "guest" so the screen can say who.
	if (v.accepted) return v.level === 'guest' ? 'guest' : 'open';
	if (v.reason && /^(no-speech|unverifiable)/.test(v.reason)) return 'unclear';
	return 'refused';
}
