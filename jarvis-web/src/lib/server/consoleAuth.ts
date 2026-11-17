/**
 * A password on the console, and the pairing secret it unlocks.
 *
 * There was no login here. That was survivable only because the console was
 * assumed to be behind something else — but it is not a private page: the `/ws`
 * relay attaches the server-held admin token to whatever connects, and the
 * origin guard deliberately admits a client that sends no `Origin`, because
 * that is what a non-browser client looks like. So anything that can reach this
 * port is already an authenticated API client. The pairing secret was the one
 * thing standing between that and a PERMANENT credential, and it stood there by
 * being typed in full by a person, every session.
 *
 * This replaces the typing, not the second factor. The operator proves a
 * password once per browser session; the console holds the pairing secret and
 * uses it on their behalf. What is deliberately preserved: reaching the port is
 * still not enough. What genuinely changes: an attacker who can read this
 * server's environment or filesystem now gets the pairing secret too — but that
 * attacker already has `AURA_TOKEN` sitting in the same environment, so the
 * pairing secret was never what was protecting them from.
 *
 * Rules that follow from that:
 *
 * **The password is never stored.** A scrypt hash is (`node:crypto`), salted,
 * and a file that arrives holding a plaintext password is rewritten with its
 * hash the first time it is read.
 *
 * **The password never reaches the browser**, and neither does the pairing
 * secret until a session has been proved server-side. A control that fetched
 * the secret and then hid it behind a button would have already lost.
 *
 * **Attempts are counted, per caller.** See [isLocked].
 */

import * as nodeCrypto from 'node:crypto';
import * as nodeFs from 'node:fs';
import * as nodePath from 'node:path';
import { deviceFor, deviceLive, houseTokenLive } from './deviceKeys';

/** A node Buffer, as far as this module is concerned. */
type Bytes = Uint8Array & { toString(encoding: string): string };

const { randomBytes, randomUUID, scrypt, timingSafeEqual } = nodeCrypto as {
	randomBytes(size: number): Bytes;
	randomUUID(): string;
	scrypt(
		password: string,
		salt: Uint8Array,
		keylen: number,
		options: { N: number; r: number; p: number; maxmem: number },
		callback: (err: Error | null, key: Bytes) => void
	): void;
	timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean;
};

const { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } = nodeFs as {
	chmodSync(path: string, mode: number): void;
	existsSync(path: string): boolean;
	mkdirSync(path: string, options: { recursive: boolean }): void;
	readFileSync(path: string, encoding: string): string;
	writeFileSync(path: string, data: string, options: { mode: number }): void;
};

const { dirname, isAbsolute, join } = nodePath as {
	dirname(path: string): string;
	isAbsolute(path: string): boolean;
	join(...parts: string[]): string;
};

declare const Buffer: { from(input: string, encoding?: string): Bytes };
declare const process: { cwd(): string; env: Record<string, string | undefined> };

/** Plaintext password, hashed in memory at first use and never written out. */
export const ENV_PASSWORD = 'AURA_CONSOLE_PASSWORD';  // pragma: allowlist secret
/** A `scrypt$…` line produced by [hashPassword]. Nothing recoverable anywhere. */
export const ENV_PASSWORD_HASH = 'AURA_CONSOLE_PASSWORD_HASH';  // pragma: allowlist secret
/** Where the hash file lives, when the default is not wanted. */
export const ENV_PASSWORD_FILE = 'AURA_CONSOLE_PASSWORD_FILE';
/** The pairing secret this console mints with, set where the console runs. */
export const ENV_PAIRING_SECRET = 'AURA_PAIRING_SECRET';  // pragma: allowlist secret

/**
 * Beside jarvis-core's own `.storage/auth.json`, which holds token hashes for
 * the same reason and is already ignored by git everywhere in this repo.
 */
export const DEFAULT_PASSWORD_FILE = '.storage/console-password';

/**
 * Beside the password: the mark that first-run setup happened (or was
 * skipped). Written the moment a password is first chosen, whichever screen
 * chose it, and by "Later" on the setup sheet; read as `firstRun` by the
 * console route so the sheet shows once and never again. Holds the owner's
 * name and a timestamp, nothing secret -- the name is the label the house
 * enrols the owner's voice under, and the person's own choice.
 */
export const SETUP_FILE_NAME = 'console-setup.json';

/**
 * Shorter than this is a placeholder, not a password. Higher than
 * `api/pairing.py`'s MIN_SECRET_CHARS because that guards a generated secret
 * and this guards one a person invents at a keyboard.
 */
export const MIN_PASSWORD_CHARS = 10;

/**
 * scrypt cost. 16384/8/1 is the interactive-login setting: ~16 MiB and tens of
 * milliseconds per attempt, which is what makes a stolen hash expensive to
 * chew through offline. [MAX_ATTEMPTS] is what bounds the online guess rate —
 * these two are not substitutes for each other.
 */
export const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 32 } as const;

/**
 * The most memory any stored hash may ask this process to allocate — four
 * times what [SCRYPT] itself needs, and deliberately a CONSTANT rather than
 * something derived from the parameters in the hash. Those parameters come out
 * of an editable file, and scrypt's `N`, `r` and `p` are each an instruction to
 * spend memory: a ceiling computed from them is not a ceiling. With one, every
 * malformed or hostile cost is refused by the library before it allocates
 * anything, which is why nothing below re-validates them by hand.
 */
const MAX_MEM = 128 * SCRYPT.N * SCRYPT.r * 4;

function derive(
	password: string,
	salt: Bytes,
	params: { N: number; r: number; p: number; keylen: number }
): Promise<Bytes> {
	return new Promise((resolve, reject) => {
		scrypt(
			// NFKC so a password typed on a phone keyboard and the same password
			// typed on a laptop are the same bytes; two Unicode spellings of one
			// string locking the operator out is not a hypothetical.
			password.normalize('NFKC'),
			salt,
			params.keylen,
			{ N: params.N, r: params.r, p: params.p, maxmem: MAX_MEM },
			(err, key) => (err ? reject(err) : resolve(key))
		);
	});
}

/** `scrypt$N$r$p$salt$key`, all base64. One line, so a file can hold it. */
export async function hashPassword(password: string): Promise<string> {
	const salt = randomBytes(16);
	const key = await derive(password.trim(), salt, SCRYPT);
	return `scrypt$${SCRYPT.N}$${SCRYPT.r}$${SCRYPT.p}$${salt.toString('base64')}$${key.toString('base64')}`;
}

export function looksLikeHash(line: string): boolean {
	return /^scrypt\$\d+\$\d+\$\d+\$[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}$/.test(line.trim());
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
	const parts = String(stored ?? '').trim().split('$');
	if (parts.length !== 6 || parts[0] !== 'scrypt') return false;
	const [, n, r, p, saltB64, keyB64] = parts;
	const N = Number(n);
	const rr = Number(r);
	const pp = Number(p);
	const salt = Buffer.from(saltB64, 'base64');
	const expected = Buffer.from(keyB64, 'base64');
	if (!salt.length || expected.length < 16) return false;
	let key: Bytes;
	try {
		key = await derive(String(password ?? '').trim(), salt, {
			N,
			r: rr,
			p: pp,
			keylen: expected.length
		});
	} catch {
		return false;
	}
	return key.length === expected.length && timingSafeEqual(key, expected);
}

// --- where the hash lives ---------------------------------------------------

export type HashSource = 'env-hash' | 'env-password' | 'file' | 'none';

export interface StoredPassword {
	/** The hash in force, or null when no password has been chosen yet. */
	hash: string | null;
	source: HashSource;
	/** Something the operator has to fix, e.g. a malformed hash in the env. */
	problem: string | null;
}

export function passwordFile(env: Record<string, string | undefined>): string {
	const configured = (env[ENV_PASSWORD_FILE] ?? '').trim();
	if (configured) return isAbsolute(configured) ? configured : join(process.cwd(), configured);
	return join(process.cwd(), DEFAULT_PASSWORD_FILE);
}

/** Where the setup mark lives: the password file's directory. */
export function setupFile(env: Record<string, string | undefined>): string {
	return join(dirname(passwordFile(env)), SETUP_FILE_NAME);
}

export function setupDone(env: Record<string, string | undefined>): boolean {
	return existsSync(setupFile(env));
}

/** Mark setup done, by a person named `name` or by nobody (skipped). Idempotent. */
export function markSetup(env: Record<string, string | undefined>, name: string | null): void {
	const path = setupFile(env);
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, JSON.stringify({ done: new Date().toISOString(), name }) + '\n', { mode: 0o600 });
}

/**
 * The first-run walkthrough's progress (T-0629), beside the setup mark: one small JSON row
 * the walkthrough's own model wrote. Null when there is none or it cannot be read.
 * Does not cover: what the row means (`$lib/ds/walkthrough/model` parses it).
 */
export function walkthroughFile(env: Record<string, string | undefined>): string {
	return join(dirname(passwordFile(env)), 'console-walkthrough.json');
}

export function readWalkthrough(env: Record<string, string | undefined>): string | null {
	try {
		return readFileSync(walkthroughFile(env), 'utf8');
	} catch {
		return null;
	}
}

export function writeWalkthrough(env: Record<string, string | undefined>, row: string): void {
	const path = walkthroughFile(env);
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, row + '\n', { mode: 0o600 });
}

/** Hashing the env password on every request would cost 30ms per request. */
let envMemo: { password: string; hash: string } | null = null;

export async function storedPassword(
	env: Record<string, string | undefined>
): Promise<StoredPassword> {
	const envHash = (env[ENV_PASSWORD_HASH] ?? '').trim();
	if (envHash) {
		if (looksLikeHash(envHash)) return { hash: envHash, source: 'env-hash', problem: null };
		// Fail closed and say so. Silently falling through to "no password set"
		// would offer the next visitor the choose-a-password form on a console
		// its operator believes is locked.
		return {
			hash: null,
			source: 'none',
			problem: `${ENV_PASSWORD_HASH} is not a scrypt hash — unset it, or set ${ENV_PASSWORD} instead`
		};
	}

	const envPassword = (env[ENV_PASSWORD] ?? '').trim();
	if (envPassword) {
		if (envPassword.length < MIN_PASSWORD_CHARS) {
			return {
				hash: null,
				source: 'none',
				problem: `${ENV_PASSWORD} is shorter than ${MIN_PASSWORD_CHARS} characters`
			};
		}
		if (envMemo?.password !== envPassword) {
			envMemo = { password: envPassword, hash: await hashPassword(envPassword) };
		}
		return { hash: envMemo.hash, source: 'env-password', problem: null };
	}

	let contents = '';
	try {
		contents = readFileSync(passwordFile(env), 'utf8').trim();
	} catch {
		return { hash: null, source: 'none', problem: null };
	}
	if (!contents) return { hash: null, source: 'none', problem: null };
	if (looksLikeHash(contents)) return { hash: contents, source: 'file', problem: null };

	// `echo hunter2hunter2 > .storage/console-password` is what an operator will
	// actually do, and refusing it would push them towards the plaintext env var
	// instead. So it is accepted once and immediately replaced by its hash: the
	// password is not stored, it is only briefly on its way to not being stored.
	if (contents.length < MIN_PASSWORD_CHARS) {
		return {
			hash: null,
			source: 'none',
			problem: `the password in ${passwordFile(env)} is shorter than ${MIN_PASSWORD_CHARS} characters`
		};
	}
	const hash = await hashPassword(contents);
	writeHash(env, hash);
	return { hash, source: 'file', problem: null };
}

/** Write the hash, 0600, creating the directory. Throws on failure — a password
 *  that reports success and is not persisted is worse than a visible error. */
export function writeHash(env: Record<string, string | undefined>, hash: string): void {
	const path = passwordFile(env);
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, `${hash}\n`, { mode: 0o600 });
	// `mode` on writeFileSync only applies when the file is created, so an
	// existing world-readable file would keep its mode through a rotation.
	chmodSync(path, 0o600);
}

// --- sessions ---------------------------------------------------------------

export const SESSION_COOKIE = 'jarvis_console';
/**
 * Signed in for a month on the owner's own browser (the owner, 2026-09-25: "it should
 * be like that for ALL settings, initially, instead of asking every time"). Twelve
 * hours in memory meant the password again after every deploy and every morning.
 */
export const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;
/** One console, a handful of tabs. A cap keeps the map from being a memory leak. */
export const MAX_SESSIONS = 64;

/** Minutes a browser's session may sit unused before the console asks again (T-0946). */
export const ENV_IDLE_MINUTES = 'AURA_CONSOLE_IDLE_MINUTES';
/** A week: the owner signs in once, not every morning (2026-09-25), and an abandoned tab still ends. */
export const DEFAULT_IDLE_MINUTES = 7 * 24 * 60;
/** Shorter than this locks a console mid-sentence; a typo of 0 must not mean "always locked". */
export const MIN_IDLE_MINUTES = 5;
/** How stale the saved last-used time may be: a write per request would be a write per poll. */
const USED_SAVE_MS = 60_000;

/**
 * The server's idle limit. The page's own "Lock after" is not read here: a number the
 * browser sends is the browser's to change. The page ends its session when it locks.
 */
export function idleLimitMs(env: Record<string, string | undefined>): number {
	const minutes = Number(String(env[ENV_IDLE_MINUTES] ?? '').trim() || NaN);
	return Math.max(MIN_IDLE_MINUTES, Number.isFinite(minutes) ? minutes : DEFAULT_IDLE_MINUTES) * 60_000;
}

/**
 * A session's end, when it was last used, and how it was opened: by the device whose own
 * key did it (T-0569, so a revoke can end it), or `keyed` by the adapter key. A keyed
 * session is the phone's or the car's web view, already gated by that key, and is not
 * idled out (T-0946); only the key on the page load sets it, never a header a browser picks.
 * `passkey` names the passkey that opened it, so removing that passkey ends it.
 */
type Session = { expires: number; used: number; device?: string; keyed?: boolean; passkey?: string };
const sessions = new Map<string, Session>();
let sessionsLoaded = false;
/** False once a test has reset the module: its sessions never touch a real file. */
let persist = true;

/** Beside the password: the live sessions, so a restart or a deploy keeps them. */
function sessionsFile(): string {
	return join(dirname(passwordFile(process.env)), 'console-sessions.json');
}

function loadSessions(): void {
	if (sessionsLoaded) return;
	sessionsLoaded = true;
	try {
		// A bare number is an owner's session (the file before T-0569); an object names a device.
		const saved = JSON.parse(readFileSync(sessionsFile(), 'utf8')) as Record<string, unknown>;
		for (const [token, value] of Object.entries(saved)) {
			// A session saved before T-0946 has no last-used time: it counts from this start,
			// so a deploy does not sign everyone out.
			const now = Date.now();
			if (typeof value === 'number') sessions.set(token, { expires: value, used: now });
			const tagged = value as { expires?: unknown; device?: unknown; used?: unknown; keyed?: unknown; passkey?: unknown } | null;
			if (tagged && typeof tagged === 'object' && typeof tagged.expires === 'number') {
				const device = typeof tagged.device === 'string' ? tagged.device : undefined;
				sessions.set(token, {
					expires: tagged.expires,
					used: typeof tagged.used === 'number' ? tagged.used : now,
					...(device ? { device } : {}),
					...(tagged.keyed === true || device ? { keyed: true } : {}),
					...(typeof tagged.passkey === 'string' ? { passkey: tagged.passkey } : {})
				});
			}
		}
	} catch {
		/* none yet, or unreadable: sign in again, which is the safe way to fail */
	}
}

function saveSessions(): void {
	if (!persist) return;
	try {
		mkdirSync(dirname(sessionsFile()), { recursive: true });
		const saved = Object.fromEntries(sessions);
		writeFileSync(sessionsFile(), JSON.stringify(saved), { mode: 0o600 });
	} catch {
		/* not remembered across a restart; the session still holds until then */
	}
}

/** A new session; [device] when a device's own key opened it (T-0569), [keyed] when any key did. */
export function openSession(now = Date.now(), device?: string, keyed = false, passkey?: string): string {
	loadSessions();
	for (const [token, s] of sessions) if (s.expires <= now) sessions.delete(token);
	while (sessions.size >= MAX_SESSIONS) {
		// Oldest expiry first: the session closest to being useless anyway.
		const oldest = [...sessions].sort((a, b) => a[1].expires - b[1].expires)[0];
		sessions.delete(oldest[0]);
	}
	const token = `${randomUUID()}.${randomBytes(16).toString('base64url')}`;
	sessions.set(token, {
		expires: now + SESSION_TTL_MS,
		used: now,
		...(device ? { device } : {}),
		...(keyed || device ? { keyed: true } : {}),
		...(passkey ? { passkey } : {})
	});
	saveSessions();
	return token;
}

export function sessionValid(token: string | undefined | null, now = Date.now()): boolean {
	return sessionOf(token, now) !== null;
}

/**
 * The live session [token] names, or null. Finding it is using it: the idle limit counts
 * from the last time this returned it.
 */
function sessionOf(
	token: string | undefined | null,
	now = Date.now(),
	env: Record<string, string | undefined> = process.env
): Session | null {
	loadSessions();
	const offered = String(token ?? '');
	if (!offered) return null;
	const candidate = Buffer.from(offered);
	// Constant-time against every live session, and the loop does not stop
	// early on a match — the same rule `PairingCodes.claim` follows, for the
	// same reason: this is the check that stands in front of the credential.
	let found: Session | null = null;
	for (const [issued, s] of sessions) {
		const known = Buffer.from(issued);
		const same = known.length === candidate.length && timingSafeEqual(known, candidate);
		const live = s.expires > now && (s.keyed || now - s.used <= idleLimitMs(env));
		if (same && live) found = s;
		if (same && !live) sessions.delete(issued);
	}
	if (found && now > found.used) {
		const stale = now - found.used > USED_SAVE_MS;
		found.used = now;
		if (stale) saveSessions();
	}
	return found;
}

export function closeSession(token: string | undefined | null): void {
	loadSessions();
	if (token && sessions.delete(String(token))) saveSessions();
}

/**
 * Whether [token] is a live session a key opened: the phone's or the car's web view. Its
 * lock stays a screen, because ending the session there either signs straight back in
 * with the key or strands the view at a password it was never meant to need.
 */
export function sessionKeyed(token: string | undefined | null, env: Record<string, string | undefined> = process.env): boolean {
	return sessionOf(token, Date.now(), env)?.keyed === true;
}

/** End every session passkey [id] opened: removing a lost device's passkey signs it out. */
export function closePasskeySessions(id: string): void {
	loadSessions();
	let closed = false;
	for (const [token, s] of sessions) if (s.passkey === id && sessions.delete(token)) closed = true;
	if (closed) saveSessions();
}

/** End every session device [id]'s own key opened: what a revoke means here (T-0569). */
export function closeDeviceSessions(id: string): void {
	loadSessions();
	let closed = false;
	for (const [token, s] of sessions) if (s.device === id && sessions.delete(token)) closed = true;
	if (closed) saveSessions();
}

// --- the gate ---------------------------------------------------------------
//
// T-0387. The relays (`/hermes/*` with the adapter key, `/ws` with jarvis-core's
// admin token) and the API routes act as the owner. Behind Tailscale only the
// owner's devices reached this port; since home moved to the LAN (2026-10-27)
// anyone on it did. A gated path now needs a session from the sign-in, or the
// adapter key itself (what the phone and the car were paired with).
//
// Open on purpose: the sign-in (`/api/console`); a pairing claim (it carries its
// own one-time code); the phone's server discovery (`/api/config`: non-secret, and
// a 401 there reads as jarvis-core to `ServerEndpoint.kindFromProbe`); a model
// download (`/api/models/<name>` passes the phone's own token upstream and never
// attaches the server's); and the voice gate (`/api/voice/speaker/**`: the phone
// calls it with its own token, and its GET is counts and scores only). The pages
// themselves are open; everything they can do goes through a gated path.
//
// Does not cover: a password-less console, which stays open as it always was (the
// first-run setup asks for one). A path is judged as SvelteKit's router decodes it
// (`decode_pathname`): tested raw, `/%61pi/state` reached /api/state unsigned. A
// malformed encoding is gated; the router answers it 400 anyway.

const EXEMPT = [
	/^\/api\/console$/,
	// Signing in with a passkey happens before a session; the route says who may do what.
	/^\/api\/console\/passkey$/,
	/^\/api\/pair\/claim$/,
	/^\/api\/config$/,
	/^\/api\/models\/[^/]+$/,
	/^\/api\/voice\/speaker(\/|$)/
];

/** The relays that reach only the house, which a house token may call (T-0782). */
const HOUSE_RELAYS = [
	/^\/api\/maps\/[^/]+$/,
	// One map region: its pack for the phone to copy, and its removal (T-0979).
	/^\/api\/maps\/(?:regions|packs)\/[a-z0-9][a-z0-9-]{0,63}$/,
	/^\/api\/tts$/
];

/** [path] as the router decodes it, or null for a malformed encoding. */
function routedPath(path: string): string | null {
	try {
		return path.split('%25').map(decodeURI).join('%25');
	} catch {
		return null;
	}
}

export function gated(path: string): boolean {
	const routed = routedPath(path);
	if (routed === null) return true;
	return /^\/(api|hermes)(\/|$)/.test(routed) && !EXEMPT.some((r) => r.test(routed));
}

/**
 * Whether a cookie may be marked Secure: only when the browser came over https,
 * which here means Caddy said so. Node serves plain http, and adapter-node calls
 * every request https when ORIGIN is unset, so `url.protocol` marked the session
 * Secure on http://<lan>:8199 too, where Chromium drops it and nobody stays signed
 * in. A forged header only makes the cookie stricter.
 */
export function secureCookie(headers: Headers): boolean {
	return headers.get('x-forwarded-proto') === 'https';
}

/** Whether [authorization] is exactly `Bearer <the adapter key>`, compared in constant time. */
export function bearerAdmits(
	env: Record<string, string | undefined>,
	authorization: string | null | undefined
): boolean {
	const key = String(env.AURA_ADAPTER_KEY ?? '').trim();
	if (!key || !authorization) return false;
	const want = Buffer.from(`Bearer ${key}`);
	const got = Buffer.from(authorization);
	return want.length === got.length && timingSafeEqual(want, got);
}

/**
 * Signed in: a live session, or no password set at all. A misconfigured password is closed.
 * A session a device's own key opened lasts only while that key does (T-0569).
 */
export async function signedIn(
	env: Record<string, string | undefined>,
	token: string | null | undefined,
	fetcher: typeof fetch = fetch
): Promise<boolean> {
	const stored = await storedPassword(env);
	if (!stored.hash && !stored.problem) return true;
	const session = sessionOf(token, Date.now(), env);
	if (!session) return false;
	return !session.device || (await deviceLive(env, session.device, fetcher));
}

/** [signedIn] for a raw Cookie header: the socket relays see the request, not SvelteKit's cookies. */
export async function signedInByCookieHeader(
	env: Record<string, string | undefined>,
	header: string | null | undefined
): Promise<boolean> {
	const match = new RegExp(`(?:^|;\\s*)${SESSION_COOKIE}=([^;]*)`).exec(header ?? '');
	return signedIn(env, match ? decodeURIComponent(match[1]) : undefined);
}

/**
 * What the hook does with one request: `pass`, `refuse` (401), or `sign-in` -- a
 * keyed page load (the phone's and the car's web view) that gets a session cookie,
 * so the page's own fetches and sockets, which carry no key, are signed in.
 */
export async function admit(
	env: Record<string, string | undefined>,
	req: {
		path: string;
		session: string | null | undefined;
		authorization: string | null | undefined;
		accept: string | null | undefined;
	},
	fetcher: typeof fetch = fetch
): Promise<'pass' | 'refuse' | 'sign-in'> {
	// The adapter key, or a live device's own key (T-0569: the adapter says whose it is).
	const keyed =
		bearerAdmits(env, req.authorization) ||
		(/^bearer\s/i.test(req.authorization ?? '') && (await deviceFor(env, req.authorization, fetcher)) !== null);
	if (keyed && !gated(req.path) && /\btext\/html\b/.test(req.accept ?? '') && !sessionValid(req.session)) {
		return 'sign-in';
	}
	if (!gated(req.path) || keyed || (await signedIn(env, req.session, fetcher))) return 'pass';
	// A phone paired through the console calls the maps and media relays natively with the
	// house's own token; refused here, the car drew no buildings and no limit (T-0782). Only
	// those relays, and never a session: the token reaches nothing it could not reach direct.
	const routed = routedPath(req.path);
	if (routed && HOUSE_RELAYS.some((r) => r.test(routed)) && (await houseTokenLive(env, req.authorization, fetcher))) {
		return 'pass';
	}
	return 'refuse';
}

// --- attempt limiting -------------------------------------------------------

/**
 * An unlimited password endpoint is not a password. scrypt costs an attacker
 * ~30ms per guess, so an unbounded endpoint hands them roughly thirty guesses
 * a second per connection and as many connections as they like: every password
 * a person would actually choose falls inside an afternoon, and the console
 * never says a word while it happens. These bound the online rate; the scrypt
 * cost only bounds the offline one.
 *
 * Counted per caller, not globally, for the reason `api/pairing.py` spells out:
 * a global counter is a denial of service anybody can trigger, locking the
 * operator out of their own console by failing five logins.
 */
export const MAX_ATTEMPTS = 5;
export const ATTEMPT_WINDOW_MS = 300_000;
/** A spoofable key must not be able to grow this map without bound. */
export const MAX_TRACKED_CLIENTS = 256;

const failures = new Map<string, number[]>();

function recent(client: string, now: number): number[] {
	const kept = (failures.get(client) ?? []).filter((at) => now - at < ATTEMPT_WINDOW_MS);
	if (kept.length) failures.set(client, kept);
	else failures.delete(client);
	return kept;
}

export function isLocked(client: string, now = Date.now()): boolean {
	return recent(client, now).length >= MAX_ATTEMPTS;
}

export function recordFailure(client: string, now = Date.now()): void {
	const kept = recent(client, now);
	if (failures.size >= MAX_TRACKED_CLIENTS && !failures.has(client)) {
		// Drop the least recently failing caller. That forgives somebody who
		// was already being refused, which is a better failure than an
		// unbounded map keyed by a value the caller can choose.
		const oldest = [...failures].sort((a, b) => a[1][a[1].length - 1] - b[1][b[1].length - 1])[0];
		failures.delete(oldest[0]);
	}
	failures.set(client, [...kept, now]);
}

export function clearFailures(client: string): void {
	failures.delete(client);
}

/** Seconds until [client] may try again, or 0. */
export function lockedFor(client: string, now = Date.now()): number {
	const kept = recent(client, now);
	if (kept.length < MAX_ATTEMPTS) return 0;
	return Math.max(1, Math.ceil((ATTEMPT_WINDOW_MS - (now - kept[0])) / 1000));
}

// --- the pairing secret this console holds ----------------------------------

export type SecretSource = 'env' | 'operator' | 'none';

/**
 * Handed over by an operator who proved the password, and kept in this
 * process's memory only — never written to disk, never sent to the browser
 * except through the password-gated reveal. A restart forgets it, which is the
 * cost of not writing down the thing that guards permanent credentials.
 */
let heldSecret = '';

export function pairingSecret(env: Record<string, string | undefined>): {
	secret: string;
	source: SecretSource;
} {
	const configured = (env[ENV_PAIRING_SECRET] ?? '').trim();
	if (configured) return { secret: configured, source: 'env' };
	if (heldSecret) return { secret: heldSecret, source: 'operator' };
	return { secret: '', source: 'none' };
}

export function holdPairingSecret(value: string): void {
	heldSecret = String(value ?? '').trim();
}

export function dropPairingSecret(): void {
	heldSecret = '';
}

/** Test seam. Module state is deliberate — these are per-process, not per-request. */
export function resetConsoleAuth(): void {
	sessions.clear();
	// A test's sessions are its own: nothing is read back from a file a run left.
	sessionsLoaded = true;
	persist = false;
	failures.clear();
	heldSecret = '';
	envMemo = null;
}
