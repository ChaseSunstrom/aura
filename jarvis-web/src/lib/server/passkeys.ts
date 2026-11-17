/**
 * Passkeys on the console's gate (T-0946): a fingerprint, Windows Hello or Touch ID
 * instead of typing the console password. The password stays, as the way back in when
 * an authenticator is lost and as the only way in where WebAuthn cannot run.
 *
 * The rules, each the answer to a way this goes wrong:
 *
 * **The site's name comes from a setting, never the request.** `AURA_CONSOLE_ORIGIN`
 * (the https name Caddy serves) gives both the relying-party id and the one origin an
 * answer may come from. Read off the `Host` header, a page on any name that reaches
 * this port could mint and replay answers for its own name. Unset, there are no
 * passkeys and the page says why.
 *
 * **A challenge is the server's, used once, for two minutes.** It is taken out of the
 * map when an answer names it, right or wrong, so a captured answer cannot be sent
 * twice.
 *
 * **The person is verified, not only present.** A security key that was merely touched
 * is refused: the passkey stands in for a password, so it has to be a fingerprint, a
 * face or a PIN.
 *
 * **Only a signed-in browser adds or removes one.** The route enforces that; nothing
 * here opens a session.
 *
 * Does not cover: attestation (any authenticator the owner's device offers is taken;
 * this is one house, not a fleet with a hardware policy), a passkey being the only way
 * in (the password always works), or a cloned authenticator whose counter is zero on
 * both copies (synced passkeys report zero; a counter that is used and goes backwards
 * is refused), or a flood of sign-in asks from the LAN, which can push out a real one for
 * its two minutes (asks are not counted, since counting them would lock the owner out
 * for asking; the password still signs in).
 */

import * as nodeFs from 'node:fs';
import * as nodePath from 'node:path';
import {
	generateAuthenticationOptions,
	generateRegistrationOptions,
	verifyAuthenticationResponse,
	verifyRegistrationResponse,
	type AuthenticatorTransportFuture
} from '@simplewebauthn/server';
import { isoBase64URL } from '@simplewebauthn/server/helpers';
import { passwordFile } from './consoleAuth';

const { mkdirSync, readFileSync, writeFileSync } = nodeFs as {
	mkdirSync(path: string, options: { recursive: boolean }): void;
	readFileSync(path: string, encoding: string): string;
	writeFileSync(path: string, data: string, options: { mode: number }): void;
};
const { dirname, join } = nodePath as { dirname(path: string): string; join(...parts: string[]): string };

type Env = Record<string, string | undefined>;

/** The console's public https address, e.g. `https://console.example`. */
export const ENV_ORIGIN = 'AURA_CONSOLE_ORIGIN';
/** Beside the password hash, for the same reason it is there. */
export const PASSKEYS_FILE_NAME = 'console-passkeys.json';
/** A handful of devices in one house; a cap keeps a signed-in script from filling the disk. */
export const MAX_PASSKEYS = 16;
export const CHALLENGE_TTL_MS = 120_000;
/** Each unanswered ask holds one, per kind; nobody honest has this many prompts open. */
export const MAX_CHALLENGES = 64;
const MAX_NAME_CHARS = 40;

export interface Passkey {
	id: string;
	/** The COSE public key, base64url. */
	publicKey: string;
	counter: number;
	transports?: AuthenticatorTransportFuture[];
	/** What the owner called it ("Laptop"). */
	name: string;
	added: number;
}

/** The relying party, or why there is none. */
export function relyingParty(env: Env): { origin: string; rpID: string } | { problem: string } {
	const raw = String(env[ENV_ORIGIN] ?? '').trim();
	if (!raw) return { problem: `Passkeys need the console's https address: set ${ENV_ORIGIN} where the console runs.` };
	let url: URL;
	try {
		url = new URL(raw);
	} catch {
		return { problem: `${ENV_ORIGIN} is not an address.` };
	}
	// A browser offers WebAuthn only to a secure context; localhost is one.
	if (url.protocol !== 'https:' && url.hostname !== 'localhost') {
		return { problem: `${ENV_ORIGIN} must be an https address: a browser offers no passkey over plain http.` };
	}
	return { origin: url.origin, rpID: url.hostname };
}

export function passkeysFile(env: Env): string {
	return join(dirname(passwordFile(env)), PASSKEYS_FILE_NAME);
}

export function listPasskeys(env: Env): Passkey[] {
	try {
		const saved = JSON.parse(readFileSync(passkeysFile(env), 'utf8')) as unknown;
		if (!Array.isArray(saved)) return [];
		return saved.filter(
			(k): k is Passkey =>
				Boolean(k) && typeof k.id === 'string' && typeof k.publicKey === 'string' && typeof k.counter === 'number'
		);
	} catch {
		return []; // none yet, or unreadable: the password still signs in
	}
}

function save(env: Env, keys: Passkey[]): void {
	mkdirSync(dirname(passkeysFile(env)), { recursive: true });
	writeFileSync(passkeysFile(env), JSON.stringify(keys), { mode: 0o600 });
}

export function removePasskey(env: Env, id: string): boolean {
	const keys = listPasskeys(env);
	const kept = keys.filter((k) => k.id !== id);
	if (kept.length === keys.length) return false;
	save(env, kept);
	return true;
}

// --- challenges -------------------------------------------------------------

type Kind = 'register' | 'sign-in';
const challenges = new Map<string, { kind: Kind; expires: number }>();

function hold(challenge: string, kind: Kind, now: number): void {
	for (const [c, held] of challenges) if (held.expires <= now) challenges.delete(c);
	// Its own kind only: anyone may ask for sign-in challenges, and a flood of them must
	// not push out the one a signed-in owner is registering against.
	const same = [...challenges].filter(([, held]) => held.kind === kind);
	for (const [c] of same.slice(0, Math.max(0, same.length - MAX_CHALLENGES + 1))) challenges.delete(c);
	challenges.set(challenge, { kind, expires: now + CHALLENGE_TTL_MS });
}

/** True once per challenge this server issued for [kind], and never after. */
function take(challenge: string, kind: Kind, now: number): boolean {
	const held = challenges.get(challenge);
	challenges.delete(challenge);
	return held !== undefined && held.kind === kind && held.expires > now;
}

export function resetPasskeys(): void {
	challenges.clear();
}

// --- registration (a signed-in browser only; the route checks) -----------------

export async function registrationOptions(env: Env, now = Date.now()) {
	const rp = relyingParty(env);
	if ('problem' in rp) throw new Error(rp.problem);
	const options = await generateRegistrationOptions({
		rpName: 'Console',
		rpID: rp.rpID,
		// One house, one gate: there is one "user", and the name is only what the
		// authenticator's own picker shows.
		userName: 'owner',
		userDisplayName: 'Owner',
		attestationType: 'none',
		excludeCredentials: listPasskeys(env).map((k) => ({ id: k.id })),
		authenticatorSelection: { residentKey: 'preferred', userVerification: 'required' }
	});
	hold(options.challenge, 'register', now);
	return options;
}

export async function register(env: Env, response: unknown, name: unknown, now = Date.now()): Promise<Passkey> {
	const rp = relyingParty(env);
	if ('problem' in rp) throw new Error(rp.problem);
	const keys = listPasskeys(env);
	if (keys.length >= MAX_PASSKEYS) throw new Error(`this console keeps at most ${MAX_PASSKEYS} passkeys; remove one first`);
	const verified = await verifyRegistrationResponse({
		response: response as Parameters<typeof verifyRegistrationResponse>[0]['response'],
		expectedChallenge: (challenge: string) => take(challenge, 'register', now),
		expectedOrigin: rp.origin,
		expectedRPID: rp.rpID,
		requireUserVerification: true
	});
	const made = verified.registrationInfo?.credential;
	if (!verified.verified || !made) throw new Error('the passkey could not be verified');
	if (keys.some((k) => k.id === made.id)) throw new Error('that passkey is already on this console');
	const label = typeof name === 'string' ? name.trim().slice(0, MAX_NAME_CHARS) : '';
	const passkey: Passkey = {
		id: made.id,
		publicKey: isoBase64URL.fromBuffer(made.publicKey),
		counter: made.counter,
		transports: made.transports,
		name: label || `Passkey ${keys.length + 1}`,
		added: now
	};
	// Read again: the verification above awaited, and a passkey added or removed meanwhile
	// must not be undone by writing the list as it was before.
	save(env, [...listPasskeys(env).filter((k) => k.id !== passkey.id), passkey]);
	return passkey;
}

// --- sign-in ----------------------------------------------------------------

export async function signInOptions(env: Env, now = Date.now()) {
	const rp = relyingParty(env);
	if ('problem' in rp) throw new Error(rp.problem);
	const options = await generateAuthenticationOptions({
		rpID: rp.rpID,
		userVerification: 'required',
		allowCredentials: listPasskeys(env).map((k) => ({
			id: k.id,
			transports: k.transports
		}))
	});
	hold(options.challenge, 'sign-in', now);
	return options;
}

/**
 * The id of the registered passkey [response] answers one of our challenges with, or null
 * for every way it does not; the caller counts the failure and says one thing.
 */
export async function verifySignIn(env: Env, response: unknown, now = Date.now()): Promise<string | null> {
	const rp = relyingParty(env);
	if ('problem' in rp) return null;
	const offered = response as { id?: unknown } | null;
	const key = listPasskeys(env).find((k) => k.id === offered?.id);
	if (!key) return null;
	try {
		const verified = await verifyAuthenticationResponse({
			response: response as Parameters<typeof verifyAuthenticationResponse>[0]['response'],
			expectedChallenge: (challenge: string) => take(challenge, 'sign-in', now),
			expectedOrigin: rp.origin,
			expectedRPID: rp.rpID,
			credential: { id: key.id, publicKey: isoBase64URL.toBuffer(key.publicKey), counter: key.counter },
			requireUserVerification: true
		});
		if (!verified.verified) return null;
		// Read again, as `register` does: a passkey removed while this one was being checked
		// stays removed, and one removed is not signed in.
		const current = listPasskeys(env);
		const kept = current.find((k) => k.id === key.id);
		if (!kept) return null;
		kept.counter = verified.authenticationInfo.newCounter;
		try {
			save(env, current);
		} catch {
			/* the counter is not remembered; the answer was still a good one */
		}
		return kept.id;
	} catch {
		return null; // a bad signature, a spent challenge, a counter that went backwards
	}
}
