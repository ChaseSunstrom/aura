import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as nodeFs from 'node:fs';
import * as nodeOs from 'node:os';
import * as nodePath from 'node:path';
import { fileURLToPath } from 'node:url';
import {
	ATTEMPT_WINDOW_MS,
	admit,
	bearerAdmits,
	gated,
	signedIn,
	secureCookie,
	signedInByCookieHeader,
	MAX_ATTEMPTS,
	MAX_SESSIONS,
	MIN_PASSWORD_CHARS,
	clearFailures,
	closeDeviceSessions,
	closeSession,
	dropPairingSecret,
	hashPassword,
	holdPairingSecret,
	idleLimitMs,
	isLocked,
	lockedFor,
	looksLikeHash,
	markSetup,
	setupDone,
	setupFile,
	openSession,
	pairingSecret,
	passwordFile,
	recordFailure,
	resetConsoleAuth,
	sessionValid,
	storedPassword,
	verifyPassword,
	SESSION_TTL_MS,
	writeHash
} from './consoleAuth';
import { resetDeviceKeys } from './deviceKeys';

const { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } = nodeFs as {
	mkdtempSync(prefix: string): string;
	readFileSync(path: string, encoding: string): string;
	rmSync(path: string, options: { recursive: boolean; force: boolean }): void;
	statSync(path: string): { mode: number };
	writeFileSync(path: string, data: string, options?: { mode: number }): void;
};
const { tmpdir } = nodeOs as { tmpdir(): string };
const { join } = nodePath as { join(...parts: string[]): string };

let dir = '';
/** An env with the hash file pointed somewhere disposable. */
function env(extra: Record<string, string | undefined> = {}) {
	return { AURA_CONSOLE_PASSWORD_FILE: join(dir, 'console-password'), ...extra };
}

beforeEach(() => {
	dir = mkdtempSync(join(tmpdir(), 'jarvis-console-'));
	resetConsoleAuth();
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe('the password itself is never stored', () => {
	it('round-trips a password through a salted scrypt hash', async () => {
		const hash = await hashPassword('correct horse battery');
		expect(looksLikeHash(hash)).toBe(true);
		expect(hash).not.toContain('correct horse battery');
		expect(await verifyPassword('correct horse battery', hash)).toBe(true);
		expect(await verifyPassword('correct horse batter', hash)).toBe(false);
		expect(await verifyPassword('', hash)).toBe(false);
	});

	it('salts, so two consoles with the same password do not share a hash', async () => {
		expect(await hashPassword('same password')).not.toBe(await hashPassword('same password'));
	});

	it('ignores whitespace around a pasted password rather than locking the operator out', async () => {
		const hash = await hashPassword('  paste-artefact  ');
		expect(await verifyPassword('paste-artefact', hash)).toBe(true);
	});

	it('refuses an absurd cost from a stored hash instead of spending it', async () => {
		// One edited digit in the hash file, and every login attempt after it
		// asks for a gigabyte (`N`) or a thousand times the work (`p`). Both are
		// refused, and refused QUICKLY: the memory ceiling is a constant, so it
		// still bounds a hash that asks for more. Derive the ceiling from the
		// hash's own parameters instead and this takes seconds each.
		const parts = (await hashPassword('whatever it is')).split('$');
		const hostile = (n: string, p: string) =>
			['scrypt', n, parts[2], p, parts[4], parts[5]].join('$');
		const started = Date.now();
		expect(await verifyPassword('whatever it is', hostile(parts[1], '1000000'))).toBe(false);
		expect(await verifyPassword('whatever it is', hostile(String(1 << 20), parts[3]))).toBe(false);
		expect(Date.now() - started).toBeLessThan(1_000);
	});

	it('refuses a malformed hash rather than throwing', async () => {
		for (const bad of ['', 'hunter2', 'scrypt$16384$8$1$onlyfour', 'bcrypt$1$2$3$a$b']) {
			expect(await verifyPassword('hunter2', bad), bad).toBe(false);
		}
	});

	// The installer's setup page hashes the password a person chooses, in Python, before this
	// console has ever run (deploy/setup/server.py). A line it makes that this refuses is a
	// first sign-in that fails, so both suites read one table (deploy/tests/test_setup_gui.py).
	it('accepts each hash the installer makes, and only for its own password (tests/contracts/console-password.json)', async () => {
		const table = JSON.parse(
			readFileSync(fileURLToPath(new URL('../../../../tests/contracts/console-password.json', import.meta.url)), 'utf-8')
		) as { rows: { password: string; salt: string; line: string }[] };
		expect(table.rows.length).toBeGreaterThanOrEqual(4);
		for (const row of table.rows) {
			expect(looksLikeHash(row.line), row.password).toBe(true);
			expect(await verifyPassword(row.password, row.line), row.password).toBe(true);
			expect(await verifyPassword(`${row.password}x`, row.line), row.password).toBe(false);
		}
		// The same password in the other Unicode spelling is the same password.
		const spelled = table.rows.find((row) => row.password !== row.password.normalize('NFKC'))!;
		expect(await verifyPassword(spelled.password.normalize('NFKC'), spelled.line)).toBe(true);
	});
});

describe('where the hash lives', () => {
	it('reports no password when nothing is set anywhere', async () => {
		const stored = await storedPassword(env());
		expect(stored).toEqual({ hash: null, source: 'none', problem: null });
	});

	it('takes a hash from the environment', async () => {
		const hash = await hashPassword('from-the-environment');
		const stored = await storedPassword(env({ AURA_CONSOLE_PASSWORD_HASH: hash }));
		expect(stored.source).toBe('env-hash');
		expect(await verifyPassword('from-the-environment', stored.hash!)).toBe(true);
	});

	it('fails closed on a malformed hash in the environment', async () => {
		// The dangerous alternative is falling through to "no password set",
		// which offers the choose-a-password form on a console its operator
		// believes is locked.
		const stored = await storedPassword(env({ AURA_CONSOLE_PASSWORD_HASH: 'hunter2' }));
		expect(stored.hash).toBeNull();
		expect(stored.problem).toContain('AURA_CONSOLE_PASSWORD_HASH');
	});

	it('hashes a plaintext env password without writing it anywhere', async () => {
		const stored = await storedPassword(env({ AURA_CONSOLE_PASSWORD: 'env-plaintext-pw' }));  // pragma: allowlist secret
		expect(stored.source).toBe('env-password');
		expect(await verifyPassword('env-plaintext-pw', stored.hash!)).toBe(true);
		expect(() => statSync(passwordFile(env()))).toThrow();
	});

	it('refuses a too-short env password', async () => {
		const stored = await storedPassword(env({ AURA_CONSOLE_PASSWORD: 'short' }));
		expect(stored.hash).toBeNull();
		expect(stored.problem).toContain(String(MIN_PASSWORD_CHARS));
	});

	it('replaces a plaintext password left in the file with its hash', async () => {
		// `echo my-password > .storage/console-password` is what an operator
		// actually does. Accepting it and then not storing it is the point.
		const path = passwordFile(env());
		writeFileSync(path, 'written-by-hand\n');
		const stored = await storedPassword(env());
		expect(stored.source).toBe('file');
		expect(await verifyPassword('written-by-hand', stored.hash!)).toBe(true);
		const onDisk = readFileSync(path, 'utf8');
		expect(onDisk).not.toContain('written-by-hand');
		expect(looksLikeHash(onDisk)).toBe(true);
		expect(statSync(path).mode & 0o077).toBe(0);
	});

	it('writes the hash 0600, even over a world-readable file', async () => {
		const path = passwordFile(env());
		writeFileSync(path, 'placeholder\n', { mode: 0o644 });
		writeHash(env(), await hashPassword('a-chosen-password'));
		expect(statSync(path).mode & 0o077).toBe(0);
		const stored = await storedPassword(env());
		expect(await verifyPassword('a-chosen-password', stored.hash!)).toBe(true);
	});
});

describe('first-run setup', () => {
	it('is marked beside the password, 0600, with the name and nothing secret, and never twice', () => {
		expect(setupDone(env())).toBe(false);
		expect(setupFile(env())).toBe(join(dir, 'console-setup.json'));
		markSetup(env(), 'Vera');
		expect(setupDone(env())).toBe(true);
		expect(statSync(setupFile(env())).mode & 0o077).toBe(0);
		const written = JSON.parse(readFileSync(setupFile(env()), 'utf8')) as { done: string; name: string | null };
		expect(written.name).toBe('Vera');
		expect(Number.isNaN(Date.parse(written.done))).toBe(false);
		expect(Object.keys(written).sort()).toEqual(['done', 'name']);
		// Skipped later: still done, and the name is not invented.
		markSetup(env(), null);
		expect(JSON.parse(readFileSync(setupFile(env()), 'utf8')).name).toBeNull();
	});
});

describe('sessions', () => {
	it('accepts the token it issued and nothing else', () => {
		const token = openSession();
		expect(sessionValid(token)).toBe(true);
		expect(sessionValid(`${token}x`)).toBe(false);
		expect(sessionValid('')).toBe(false);
		expect(sessionValid(undefined)).toBe(false);
	});

	// Signed in once, not every morning (the owner, 2026-09-25); the idle lock ("Lock
	// after") is what covers a console left open, and the session still ends.
	it('lasts a month, then expires', () => {
		const token = openSession(1_000);
		expect(sessionValid(token, 1_000 + 13 * 60 * 60 * 1000)).toBe(true);
		expect(sessionValid(token, 1_000 + SESSION_TTL_MS + 1)).toBe(false);
	});

	it('forgets one on request, which is what LOCK means', () => {
		const token = openSession();
		closeSession(token);
		expect(sessionValid(token)).toBe(false);
	});

	// T-0946: a console nobody has touched for the idle limit asks again, and using it
	// is what keeps it. The limit is the server's; the page's own timer is not trusted.
	it('is refused once unused past the idle limit, and kept by use', () => {
		const day = 24 * 60 * 60 * 1000;
		const idle = idleLimitMs({});
		expect(idle).toBe(7 * day);
		const left = openSession(1_000);
		const used = openSession(1_000);
		expect(sessionValid(used, 1_000 + idle - 1)).toBe(true);
		expect(sessionValid(used, 1_000 + idle + day)).toBe(true);
		expect(sessionValid(left, 1_000 + idle + 1)).toBe(false);
	});

	it('reads the idle limit from its setting, with a floor', () => {
		expect(idleLimitMs({ AURA_CONSOLE_IDLE_MINUTES: '30' })).toBe(30 * 60_000);
		expect(idleLimitMs({ AURA_CONSOLE_IDLE_MINUTES: '0' })).toBe(5 * 60_000);
		expect(idleLimitMs({ AURA_CONSOLE_IDLE_MINUTES: 'soon' })).toBe(7 * 24 * 60 * 60_000);
	});

	it('does not idle out a session the phone or the car opened with a key', () => {
		const keyed = openSession(1_000, undefined, true);
		expect(sessionValid(keyed, 1_000 + idleLimitMs({}) + 1)).toBe(true);
		expect(sessionValid(keyed, 1_000 + SESSION_TTL_MS + 1)).toBe(false);
	});

	it('bounds how many it keeps', () => {
		const first = openSession();
		for (let i = 0; i < MAX_SESSIONS + 2; i += 1) openSession();
		expect(sessionValid(first)).toBe(false);
	});
});

describe('attempt limiting', () => {
	// An unlimited endpoint is not a password: scrypt costs a guess ~30ms, so
	// unbounded means tens of guesses a second and silence while it happens.
	it('shuts the door on a caller after MAX_ATTEMPTS inside the window', () => {
		for (let i = 0; i < MAX_ATTEMPTS - 1; i += 1) recordFailure('1.2.3.4', 1_000);
		expect(isLocked('1.2.3.4', 1_000)).toBe(false);
		recordFailure('1.2.3.4', 1_000);
		expect(isLocked('1.2.3.4', 1_000)).toBe(true);
		expect(lockedFor('1.2.3.4', 1_000)).toBeGreaterThan(0);
	});

	it('locks out only the caller that failed', () => {
		// A global counter is a denial of service anybody can trigger: five
		// wrong guesses and the operator cannot reach their own console.
		for (let i = 0; i < MAX_ATTEMPTS; i += 1) recordFailure('attacker', 1_000);
		expect(isLocked('attacker', 1_000)).toBe(true);
		expect(isLocked('the-operator', 1_000)).toBe(false);
	});

	it('forgives once the window has passed', () => {
		for (let i = 0; i < MAX_ATTEMPTS; i += 1) recordFailure('1.2.3.4', 1_000);
		expect(isLocked('1.2.3.4', 1_000 + ATTEMPT_WINDOW_MS + 1)).toBe(false);
	});

	it('forgets a caller that got it right', () => {
		for (let i = 0; i < MAX_ATTEMPTS; i += 1) recordFailure('1.2.3.4', 1_000);
		clearFailures('1.2.3.4');
		expect(isLocked('1.2.3.4', 1_000)).toBe(false);
	});
});

describe('the pairing secret this console holds', () => {
	it('prefers the environment, and a browser may not replace that', () => {
		holdPairingSecret('typed-into-the-panel');
		expect(pairingSecret({ AURA_PAIRING_SECRET: 'from-the-env' })).toEqual({  // pragma: allowlist secret
			secret: 'from-the-env',
			source: 'env'
		});
	});

	it('holds one an operator handed over, in memory only', () => {
		expect(pairingSecret({}).source).toBe('none');
		holdPairingSecret('  typed-into-the-panel  ');
		expect(pairingSecret({})).toEqual({ secret: 'typed-into-the-panel', source: 'operator' });
		dropPairingSecret();
		expect(pairingSecret({}).source).toBe('none');
	});
});

// --- the gate (T-0387) ------------------------------------------------------
//
// The console holds the adapter key and jarvis-core's admin token and relays
// with them. Since home left Tailscale for the LAN (2026-10-27) anyone on the
// network could reach /hermes/* and /api/* and be relayed as the owner. These
// pin who gets through: a signed-in browser, or a caller that brings the key.

describe('which paths the gate covers', () => {
	it('covers every relay and API path', () => {
		for (const path of [
			'/api',
			'/api/configx',
			'/api/config/x',
			'/api/tts',
			'/api/memory/export',
			'/api/messages',
			'/api/models',
			'/api/models/x/y',
			'/hermes',
			'/hermes/v1/models',
			'/hermes/ws',
			'/api/pair',
			'/api/pair/secret',
			'/api/pair-evil',
			'/api/consoles',
			'/api/console/x',
			'/api/console/passkey/x',
			'/api/console/passkeys',
			'/api/pair/claimx',
			'/api/voice/speakerx',
			// The newest-APK link hands out a signed download: never open (T-0988).
			'/api/apk'
		]) {
			expect(gated(path), path).toBe(true);
		}
	});

	// SvelteKit decodes a path before it matches a route, so `/%61pi/state` is served by
	// /api/state; tested raw, it slipped past the gate with no credential.
	it('judges a percent-encoded path as the router decodes it, and gates a malformed one', () => {
		for (const path of ['/%61pi/state', '/%68ermes/v1/models', '/api/%70air', '/api/%E0%A4%A']) {
			expect(gated(path), path).toBe(true);
		}
		expect(gated('/api/%63onsole')).toBe(false);
	});

	// `/api/config` is the phone's server discovery (non-secret: no token, only
	// whether one is set); `/api/models/<name>` passes the phone's own token upstream.
	it('leaves the pages, the sign-in itself, pairing claims, discovery, model downloads and the voice gate open', () => {
		for (const path of [
			'/',
			'/car',
			'/healthz',
			'/apix',
			'/hermesx',
			'/api/config',
			'/api/models/x',
			'/api/console',
			'/api/console/passkey',
			'/api/pair/claim',
			'/api/voice/speaker',
			'/api/voice/speaker/enrol',
			'/api/voice/speaker/people'
		]) {
			expect(gated(path), path).toBe(false);
		}
	});
});

describe('the adapter key as a credential', () => {
	const keyed = { AURA_ADAPTER_KEY: 'the-adapter-key' };
	it('admits exactly "Bearer <key>"', () => {
		expect(bearerAdmits(keyed, 'Bearer the-adapter-key')).toBe(true);
	});
	it('refuses anything else, and everything when no key is set', () => {
		expect(bearerAdmits(keyed, 'Bearer the-adapter-kez')).toBe(false);
		expect(bearerAdmits(keyed, 'the-adapter-key')).toBe(false);
		expect(bearerAdmits(keyed, '')).toBe(false);
		expect(bearerAdmits(keyed, null)).toBe(false);
		expect(bearerAdmits({}, 'Bearer ')).toBe(false);
		expect(bearerAdmits({ AURA_ADAPTER_KEY: '  ' }, 'Bearer   ')).toBe(false);
	});
});

describe('signed in', () => {
	it('is open while no password is set, as the console always was', async () => {
		expect(await signedIn(env(), undefined)).toBe(true);
	});
	it('is closed when the password is misconfigured, not open', async () => {
		expect(await signedIn(env({ AURA_CONSOLE_PASSWORD_HASH: 'not-a-hash' }), undefined)).toBe(  // pragma: allowlist secret
			false
		);
	});
	it('needs a session the console issued once a password is set', async () => {
		const e = env({ AURA_CONSOLE_PASSWORD: 'a-long-console-password' });  // pragma: allowlist secret
		expect(await signedIn(e, undefined)).toBe(false);
		expect(await signedIn(e, 'forged')).toBe(false);
		expect(await signedIn(e, openSession())).toBe(true);
	});
	it('reads the session out of a Cookie header, for the socket relays', async () => {
		const e = env({ AURA_CONSOLE_PASSWORD: 'a-long-console-password' });  // pragma: allowlist secret
		const token = openSession();
		expect(await signedInByCookieHeader(e, `jarvis_sid=x; jarvis_console=${token}`)).toBe(true);
		expect(await signedInByCookieHeader(e, `jarvis_console=${token}x`)).toBe(false);
		expect(await signedInByCookieHeader(e, `other_jarvis_console=${token}`)).toBe(false);
		expect(await signedInByCookieHeader(e, undefined)).toBe(false);
	});
});

describe('admit', () => {
	const e = () =>
		env({ AURA_CONSOLE_PASSWORD: 'a-long-console-password', AURA_ADAPTER_KEY: 'k' });  // pragma: allowlist secret
	const req = (extra: Partial<Parameters<typeof admit>[1]> = {}) => ({
		path: '/hermes/v1/models',
		session: undefined,
		authorization: null,
		accept: '*/*',
		...extra
	});

	it('refuses a gated path to a caller with nothing', async () => {
		expect(await admit(e(), req())).toBe('refuse');
	});
	// The relay swaps any bearer for the server key, so this check is the only thing
	// that stops a phone with a rotated key acting as the owner over HTTP (T-0390).
	it('refuses a key that is not the adapter key', async () => {
		expect(await admit(e(), req({ authorization: 'Bearer old-key' }))).toBe('refuse');
		expect(await admit(e(), req({ authorization: 'Bearer k ' }))).toBe('refuse');
	});
	it('passes the key and a session', async () => {
		expect(await admit(e(), req({ authorization: 'Bearer k' }))).toBe('pass');
		expect(await admit(e(), req({ session: openSession() }))).toBe('pass');
	});
	// The phone and the car load the console in a web view with the key as a
	// header on the first page load only; that load signs the view in, so its
	// own fetches and sockets carry a session cookie from then on.
	it('signs in a keyed page load, and only a page load', async () => {
		expect(await admit(e(), req({ path: '/', authorization: 'Bearer k', accept: 'text/html,*/*' }))).toBe(
			'sign-in'
		);
		expect(await admit(e(), req({ path: '/', authorization: 'Bearer k' }))).toBe('pass');
		expect(
			await admit(e(), req({ path: '/', authorization: 'Bearer k', accept: 'text/html', session: openSession() }))
		).toBe('pass');
		expect(await admit(e(), req({ path: '/', authorization: 'Bearer x', accept: 'text/html' }))).toBe('pass');
	});
	it('passes the open paths without a credential', async () => {
		expect(await admit(e(), req({ path: '/api/console' }))).toBe('pass');
		expect(await admit(e(), req({ path: '/car' }))).toBe('pass');
	});
	it('passes everything while no password is set', async () => {
		expect(await admit(env(), req())).toBe('pass');
	});
});

// T-0782: a phone paired through the console holds the house's own token and calls the
// house relays with it; the gate refused it, so the car drew no buildings and no limit.
describe('a house token on the house relays', () => {
	const e = () =>
		env({
			AURA_CONSOLE_PASSWORD: 'a-long-console-password', // pragma: allowlist secret
			AURA_ADAPTER_KEY: 'k',
			AURA_ADAPTER_URL: 'http://adapter:8643',
			AURA_URL: 'http://core:8080',
			AURA_TOKEN: 'server-token'  // pragma: allowlist secret
		});
	const req = (extra: Partial<Parameters<typeof admit>[1]> = {}) => ({
		path: '/api/maps/buildings',
		session: undefined,
		authorization: 'Bearer house',
		accept: '*/*',
		...extra
	});
	let asked = 0;
	const fetcher = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		const auth = new Headers(init.headers).get('authorization');
		if (String(input) === 'http://core:8080/api/') {
			asked++;
			return new Response('{}', { status: auth === 'Bearer house' ? 200 : 401 });
		}
		return new Response('', { status: 401 });
	}) as typeof fetch;
	beforeEach(() => {
		asked = 0;
		resetDeviceKeys();
	});

	it('admits a token the house accepts on its maps and media relays, asking once', async () => {
		expect(await admit(e(), req(), fetcher)).toBe('pass');
		expect(await admit(e(), req({ path: '/api/maps/match' }), fetcher)).toBe('pass');
		expect(await admit(e(), req({ path: '/api/tts' }), fetcher)).toBe('pass');
		// A map region's pack, which the phone copies (T-0979): an id, never a deeper path.
		expect(await admit(e(), req({ path: '/api/maps/packs/us-utah' }), fetcher)).toBe('pass');
		expect(await admit(e(), req({ path: '/api/maps/regions/us-utah' }), fetcher)).toBe('pass');
		expect(await admit(e(), req({ path: '/api/maps/packs/us-utah/x' }), fetcher)).toBe('refuse');
		expect(await admit(e(), req({ path: '/api/maps/other/us-utah' }), fetcher)).toBe('refuse');
		expect(asked).toBe(1);
	});
	it('refuses a token the house refuses', async () => {
		expect(await admit(e(), req({ authorization: 'Bearer guess' }), fetcher)).toBe('refuse');
	});
	it('opens nothing else: not Hermes, not the other APIs, not a session', async () => {
		expect(await admit(e(), req({ path: '/hermes/v1/models' }), fetcher)).toBe('refuse');
		expect(await admit(e(), req({ path: '/api/devices/keys' }), fetcher)).toBe('refuse');
		expect(await admit(e(), req({ path: '/api/maps/../devices/keys' }), fetcher)).toBe('refuse');
		expect(await admit(e(), req({ path: '/', accept: 'text/html' }), fetcher)).toBe('pass');
	});
});

// T-0569: a phone paired since holds its own adapter key, which the adapter names. The
// console admits it like the master key, and a revoke must end what it opened here too.
describe('a device key', () => {
	const e = () =>
		env({
			AURA_CONSOLE_PASSWORD: 'a-long-console-password', // pragma: allowlist secret
			AURA_ADAPTER_KEY: 'k',
			AURA_ADAPTER_URL: 'http://adapter:8643'
		});
	const req = (extra: Partial<Parameters<typeof admit>[1]> = {}) => ({
		path: '/hermes/v1/models',
		session: undefined,
		authorization: null,
		accept: '*/*',
		...extra
	});
	let liveIds: string[] = [];
	const fetcher = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
		const url = String(input);
		const auth = new Headers(init.headers).get('authorization');
		if (url.endsWith('/api/pair')) {
			const ok = auth === 'Bearer phone' && liveIds.includes('d1');
			return Response.json(ok ? { kind: 'hermes-adapter', device: { id: 'd1' } } : {}, { status: ok ? 200 : 401 });
		}
		if (url.endsWith('/api/devices/keys') && auth === 'Bearer k') {
			return Response.json({ keys: liveIds.map((id) => ({ id })) });
		}
		return new Response('', { status: 404 });
	}) as typeof fetch;
	beforeEach(() => {
		liveIds = ['d1', 'd2'];
		resetDeviceKeys();
	});

	it('admits a live device key like the adapter key, and signs in its page load', async () => {
		expect(await admit(e(), req({ authorization: 'Bearer phone' }), fetcher)).toBe('pass');
		expect(await admit(e(), req({ path: '/', authorization: 'Bearer phone', accept: 'text/html' }), fetcher)).toBe(
			'sign-in'
		);
		expect(await admit(e(), req({ authorization: 'Bearer guess' }), fetcher)).toBe('refuse');
	});
	it('ends the session a device opened once its key is gone, and leaves the owner signed in', async () => {
		const device = openSession(Date.now(), 'd1');
		const owner = openSession();
		expect(await signedIn(e(), device, fetcher)).toBe(true);
		liveIds = ['d2'];
		resetDeviceKeys();
		expect(await signedIn(e(), device, fetcher)).toBe(false);
		expect(await signedIn(e(), owner, fetcher)).toBe(true);
		expect(await admit(e(), req({ authorization: 'Bearer phone' }), fetcher)).toBe('refuse');
		expect(await admit(e(), req({ session: device }), fetcher)).toBe('refuse');
	});
	it('closes every session of one device at once, and no other', () => {
		const device = openSession(Date.now(), 'd1');
		const again = openSession(Date.now(), 'd1');
		const other = openSession(Date.now(), 'd2');
		const owner = openSession();
		closeDeviceSessions('d1');
		expect([device, again, other, owner].map((t) => sessionValid(t))).toEqual([false, false, true, true]);
	});
});

describe('secureCookie', () => {
	// adapter-node calls a request https when ORIGIN is unset; the console on
	// http://<lan>:8199 then set a Secure session Chromium would not keep.
	it('marks Secure only when the proxy says the browser came over https', () => {
		expect(secureCookie(new Headers({ 'x-forwarded-proto': 'https' }))).toBe(true);
		expect(secureCookie(new Headers({ 'x-forwarded-proto': 'http' }))).toBe(false);
		expect(secureCookie(new Headers())).toBe(false);
	});
});
