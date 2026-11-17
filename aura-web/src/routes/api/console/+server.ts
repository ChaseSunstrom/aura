import { error, json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import {
	ENV_PASSWORD,
	MIN_PASSWORD_CHARS,
	SESSION_COOKIE,
	SESSION_TTL_MS,
	clearFailures,
	closeSession,
	hashPassword,
	isLocked,
	lockedFor,
	markSetup,
	openSession,
	passwordFile,
	secureCookie,
	recordFailure,
	sessionKeyed,
	sessionValid,
	setupDone,
	storedPassword,
	verifyPassword,
	writeHash
} from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// The console's login.
//
// It exists because reaching this server is already enough to use its admin
// token — the `/ws` relay attaches it to whatever connects. See
// `$lib/server/consoleAuth` for what that does and does not change, and
// `api/pair/+server.ts` for what the password now unlocks.

/** Rate-limit bucket. Only ever a key, so a spoofed one wins a fresh allowance
 *  and nothing else — exactly the trade `api/pairing.py` documents. */
function clientKey(getClientAddress: () => string): string {
	try {
		return getClientAddress() || 'unknown';
	} catch {
		return 'unknown';
	}
}

/** Where the operator stands: is there a password, and are they past it? */
export const GET: RequestHandler = async ({ cookies }) => {
	const stored = await storedPassword(env);
	return json({
		configured: Boolean(stored.hash),
		// First run: no password has ever been chosen and nobody has said
		// "later". The console shows the setup sheet on this, once.
		firstRun: !stored.hash && !setupDone(env),
		authenticated: sessionValid(cookies.get(SESSION_COOKIE)),
		// The phone's or the car's web view, signed in by its key: its lock stays a screen.
		keyed: sessionKeyed(cookies.get(SESSION_COOKIE), env),
		source: stored.source,
		problem: stored.problem,
		minChars: MIN_PASSWORD_CHARS,
		// One line of documentation, rendered in the panel: the two places an
		// operator can put it instead of choosing one in the browser.
		envVar: ENV_PASSWORD,
		file: passwordFile(env)
	});
};

/**
 * Unlock — choosing the password if none has been chosen yet.
 *
 * First load on a console with no password is the one moment the choice can be
 * made without one, which is why it is bounded by "no password exists" and
 * nothing else: the alternative is a console that cannot be locked at all until
 * somebody edits a file on the server, and an unlockable console is what this
 * whole file exists to end.
 */
export const POST: RequestHandler = async ({ cookies, request, getClientAddress }) => {
	const who = clientKey(getClientAddress);
	if (isLocked(who)) {
		throw error(429, `too many attempts — wait ${lockedFor(who)}s and try again`);
	}

	const body = await request.json().catch(() => ({}));
	const password = typeof body?.password === 'string' ? body.password : '';
	const stored = await storedPassword(env);
	if (stored.problem) throw error(500, stored.problem);

	// "Later" on the setup sheet: nothing chosen, nothing opened, only the
	// mark that the sheet is not to come back. Bounded to first run like the
	// choice itself: once a password exists there is no setup to skip.
	if (body?.skip === true) {
		if (!stored.hash) markSetup(env, null);
		return json({ ok: true, skipped: true });
	}

	let chosen = false;
	if (!stored.hash) {
		if (password.trim().length < MIN_PASSWORD_CHARS) {
			throw error(400, `choose a password of at least ${MIN_PASSWORD_CHARS} characters`);
		}
		// Hash first, write second: a failed write must not leave the console
		// reporting a password that nothing on disk agrees with.
		const hash = await hashPassword(password);
		try {
			writeHash(env, hash);
		} catch {
			throw error(
				500,
				`could not write ${passwordFile(env)} — set ${ENV_PASSWORD} where the console runs instead`
			);
		}
		chosen = true;
		// Choosing the first password IS setup, whichever screen did it: the
		// setup sheet passes the owner's name, the pairing panel passes none.
		try {
			markSetup(env, typeof body?.name === 'string' && body.name.trim() ? body.name.trim() : null);
		} catch {
			/* the mark is a convenience; the password is what matters and it is written */
		}
	} else if (!(await verifyPassword(password, stored.hash))) {
		// Counted before the refusal is sent, so a client that hangs up early
		// still spent its attempt.
		recordFailure(who);
		throw error(401, 'that console password is not correct');
	}

	clearFailures(who);
	cookies.set(SESSION_COOKIE, openSession(), {
		path: '/',
		httpOnly: true, // page JS never sees it, so an XSS cannot lift the session
		sameSite: 'strict', // and a hostile page's POST arrives without it
		secure: secureCookie(request.headers),
		// Kept as long as the session: signed in once, not once per browser start.
		maxAge: Math.floor(SESSION_TTL_MS / 1000)
	});
	return json({ ok: true, chosen });
};

/** Lock it again — the operator leaving the machine, not an expiry. */
export const DELETE: RequestHandler = ({ cookies, request }) => {
	closeSession(cookies.get(SESSION_COOKIE));
	cookies.delete(SESSION_COOKIE, { path: '/', secure: secureCookie(request.headers) });
	return json({ ok: true });
};
