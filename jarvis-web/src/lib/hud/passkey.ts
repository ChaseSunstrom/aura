// Passkeys in the browser (T-0946): the page's half of `$lib/server/passkeys`. The
// browser's own WebAuthn prompt does the work; this asks the console for a challenge,
// hands the answer back, and turns each way it can fail into a sentence.
//
// Does not cover: a web view with no WebAuthn (the desktop app's on some systems, the
// phone's, which signs in by its key anyway). `offered` is false there and the
// password is what the screen shows.

import { browserSupportsWebAuthn, startAuthentication, startRegistration } from '@simplewebauthn/browser';

export interface PasskeyRow {
	id: string;
	name: string;
	added: number;
}

export interface PasskeyState {
	/** The console has its https address and this browser can do WebAuthn. */
	usable: boolean;
	/** At least one passkey is on this console. */
	registered: boolean;
	/** Why not, in words for the screen; null when usable. */
	why: string | null;
	/** The owner's passkeys; empty unless signed in. */
	passkeys: PasskeyRow[];
}

const NOT_HERE = 'This browser offers no passkey here. Open the console at its https address to use one.';

/** What the console says, joined with what this browser can do. */
export function passkeyState(
	body: { available?: boolean; problem?: string | null; registered?: boolean; passkeys?: PasskeyRow[] } | null,
	browserCan: boolean
): PasskeyState {
	const available = Boolean(body?.available);
	return {
		usable: available && browserCan,
		registered: Boolean(body?.registered),
		why: !available ? (body?.problem ?? 'Passkeys are not set up on this console.') : browserCan ? null : NOT_HERE,
		passkeys: Array.isArray(body?.passkeys) ? body.passkeys : []
	};
}

export async function readPasskeys(): Promise<PasskeyState> {
	const res = await fetch('/api/console/passkey');
	if (!res.ok) throw new Error(String(res.status));
	return passkeyState(await res.json(), browserSupportsWebAuthn());
}

async function ask(body: unknown): Promise<{ ok: boolean; status: number; body: Record<string, unknown> }> {
	const res = await fetch('/api/console/passkey', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body)
	});
	return { ok: res.ok, status: res.status, body: (await res.json().catch(() => ({}))) as Record<string, unknown> };
}

/** A refusal as a sentence: the console's own words, or one for the status. */
export function passkeyRefusal(status: number, message: unknown, fallback: string): string {
	if (typeof message === 'string' && message) return message;
	return status === 429 ? 'Too many attempts. Wait a moment.' : fallback;
}

/** The person closed the browser's prompt, or it timed out: not an error to shout about. */
function dismissed(e: unknown): boolean {
	const name = (e as { name?: string } | null)?.name;
	return name === 'NotAllowedError' || name === 'AbortError';
}

/** Null when the console opened; otherwise what to say. */
export async function signInWithPasskey(): Promise<string | null> {
	try {
		const options = await ask({ action: 'sign-in-options' });
		if (!options.ok) return passkeyRefusal(options.status, options.body.message, 'The console offered no passkey sign-in.');
		const response = await startAuthentication({ optionsJSON: options.body as never });
		const done = await ask({ action: 'sign-in', response });
		return done.ok ? null : passkeyRefusal(done.status, done.body.message, 'That passkey did not open the console.');
	} catch (e) {
		return dismissed(e) ? 'No passkey was used. Try again, or type the password.' : 'The passkey prompt failed. Type the password instead.';
	}
}

/** Null when the passkey was added; otherwise what to say. Signed-in browsers only (the server checks). */
export async function addPasskey(name: string): Promise<string | null> {
	try {
		const options = await ask({ action: 'register-options' });
		if (!options.ok) return passkeyRefusal(options.status, options.body.message, 'The console could not start a passkey.');
		const response = await startRegistration({ optionsJSON: options.body as never });
		const done = await ask({ action: 'register', response, name });
		return done.ok ? null : passkeyRefusal(done.status, done.body.message, 'The passkey could not be verified.');
	} catch (e) {
		const already = (e as { name?: string } | null)?.name === 'InvalidStateError';
		if (already) return 'This device already has a passkey for the console.';
		return dismissed(e) ? 'No passkey was made.' : 'The passkey prompt failed.';
	}
}

export async function removePasskey(id: string): Promise<boolean> {
	const res = await fetch('/api/console/passkey', {
		method: 'DELETE',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ id })
	});
	return res.ok && Boolean(((await res.json().catch(() => ({}))) as { ok?: boolean }).ok);
}
