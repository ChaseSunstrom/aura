import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import {
	SESSION_COOKIE,
	SESSION_TTL_MS,
	clearFailures,
	closePasskeySessions,
	isLocked,
	lockedFor,
	openSession,
	recordFailure,
	secureCookie,
	sessionValid,
	storedPassword
} from '$lib/server/consoleAuth';
import {
	listPasskeys,
	register,
	registrationOptions,
	relyingParty,
	removePasskey,
	signInOptions,
	verifySignIn
} from '$lib/server/passkeys';
import type { RequestHandler } from './$types';

// Passkeys on the console's gate (T-0946); the rules are in `$lib/server/passkeys`.
//
// This path is outside the gate (`EXEMPT` in consoleAuth.ts) because signing in
// happens before a session exists. So each action says here who may ask: anyone may
// ask for a sign-in challenge and answer it; only a signed-in browser lists, adds or
// removes a passkey.

/**
 * Its own attempt bucket, apart from the password's: five bad passkey answers from an
 * address must not lock the owner out of the password, which is the way back in.
 */
function bucket(getClientAddress: () => string): string {
	try {
		return `passkey:${getClientAddress() || 'unknown'}`;
	} catch {
		return 'passkey:unknown';
	}
}

/** What the sign-in screen needs before a session, and the list once there is one. */
export const GET: RequestHandler = ({ cookies }) => {
	const rp = relyingParty(env);
	const keys = listPasskeys(env);
	const signedIn = sessionValid(cookies.get(SESSION_COOKIE));
	return json({
		available: !('problem' in rp),
		problem: 'problem' in rp ? rp.problem : null,
		// A stranger learns that there are passkeys here (and, asking to sign in, their ids,
		// which WebAuthn does not treat as secret); names and dates are for the owner.
		registered: keys.length > 0,
		passkeys: signedIn ? keys.map((k) => ({ id: k.id, name: k.name, added: k.added })) : []
	});
};

export const POST: RequestHandler = async ({ cookies, request, getClientAddress }) => {
	const body = (await request.json().catch(() => ({}))) as { action?: unknown; response?: unknown; name?: unknown };
	const rp = relyingParty(env);
	if ('problem' in rp) throw error(409, rp.problem);
	const signedIn = sessionValid(cookies.get(SESSION_COOKIE));

	if (body.action === 'register-options' || body.action === 'register') {
		// A passkey is a second key to a door that has a first one: no password, no passkey,
		// and nobody adds one from outside.
		if (!(await storedPassword(env)).hash) throw error(409, 'choose a console password first');
		if (!signedIn) throw error(401, 'sign in to the console first');
		if (body.action === 'register-options') return json(await registrationOptions(env));
		try {
			const made = await register(env, body.response, body.name);
			return json({ ok: true, passkey: { id: made.id, name: made.name, added: made.added } });
		} catch (e) {
			throw error(400, e instanceof Error ? e.message : 'the passkey could not be verified');
		}
	}

	const who = bucket(getClientAddress);
	if (isLocked(who)) throw error(429, `too many attempts — wait ${lockedFor(who)}s and try again`);
	if (body.action === 'sign-in-options') return json(await signInOptions(env));
	if (body.action === 'sign-in') {
		const passkey = await verifySignIn(env, body.response);
		if (passkey === null) {
			recordFailure(who);
			throw error(401, 'that passkey did not open the console');
		}
		clearFailures(who);
		cookies.set(SESSION_COOKIE, openSession(Date.now(), undefined, false, passkey), {
			path: '/',
			httpOnly: true,
			sameSite: 'strict',
			secure: secureCookie(request.headers),
			maxAge: Math.floor(SESSION_TTL_MS / 1000)
		});
		return json({ ok: true });
	}
	throw error(400, 'unknown action');
};

export const DELETE: RequestHandler = async ({ cookies, request }) => {
	if (!sessionValid(cookies.get(SESSION_COOKIE))) throw error(401, 'sign in to the console first');
	const body = (await request.json().catch(() => ({}))) as { id?: unknown };
	if (typeof body.id !== 'string' || !removePasskey(env, body.id)) return json({ ok: false });
	// A passkey is removed because its device is gone: what it opened closes with it.
	closePasskeySessions(body.id);
	return json({ ok: true });
};
