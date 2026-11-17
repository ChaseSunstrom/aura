import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
	admit,
	closePasskeySessions,
	closeSession,
	idleLimitMs,
	openSession,
	resetConsoleAuth,
	sessionKeyed,
	signedInByCookieHeader,
	SESSION_COOKIE
} from './consoleAuth';

// T-0946: the lock is the server's. A stale cookie (idle past the limit, or ended by
// Lock) is refused on the API routes, the /hermes relay and the socket relays, which
// are the three ways the page reaches anything. Does not cover the pages themselves:
// they are open, and everything they can do goes through one of these.

const env = {
	AURA_CONSOLE_PASSWORD: 'a-long-console-password', // pragma: allowlist secret
	AURA_ADAPTER_KEY: 'k',
	AURA_CONSOLE_PASSWORD_FILE: '/nonexistent/console-password'
};
const req = (path: string, session: string | undefined) => ({ path, session, authorization: null, accept: '*/*' });
const cookie = (token: string) => `other=1; ${SESSION_COOKIE}=${encodeURIComponent(token)}`;
const PATHS = ['/api/state', '/api/devices/keys', '/hermes/v1/models'];

beforeEach(() => {
	resetConsoleAuth();
	vi.useFakeTimers();
	vi.setSystemTime(1_000_000);
});
afterEach(() => vi.useRealTimers());

describe('a stale session is refused where it matters', () => {
	it('idle: the API, the relay and the sockets all refuse it', async () => {
		const token = openSession();
		for (const path of PATHS) expect(await admit(env, req(path, token))).toBe('pass');
		expect(await signedInByCookieHeader(env, cookie(token))).toBe(true);

		vi.setSystemTime(Date.now() + idleLimitMs(env) + 1);
		for (const path of PATHS) expect(await admit(env, req(path, token))).toBe('refuse');
		expect(await signedInByCookieHeader(env, cookie(token))).toBe(false);
	});

	it('idle: a request inside the limit keeps the session for another limit', async () => {
		const token = openSession();
		vi.setSystemTime(Date.now() + idleLimitMs(env) - 1);
		expect(await admit(env, req('/api/state', token))).toBe('pass');
		vi.setSystemTime(Date.now() + idleLimitMs(env) - 1);
		expect(await admit(env, req('/api/state', token))).toBe('pass');
	});

	it('idle: the limit is the setting, not what a page says', async () => {
		const short = { ...env, AURA_CONSOLE_IDLE_MINUTES: '10' };
		const token = openSession();
		vi.setSystemTime(Date.now() + 10 * 60_000 + 1);
		expect(await admit(short, req('/api/state', token))).toBe('refuse');
	});

	it('locked: Lock ends the session for the API, the relay and the sockets', async () => {
		const token = openSession();
		closeSession(token);
		for (const path of PATHS) expect(await admit(env, req(path, token))).toBe('refuse');
		expect(await signedInByCookieHeader(env, cookie(token))).toBe(false);
	});

	it('keyed: the phone\'s session, opened by its key, is not idled out', async () => {
		const token = openSession(Date.now(), undefined, true);
		vi.setSystemTime(Date.now() + idleLimitMs(env) + 1);
		for (const path of PATHS) expect(await admit(env, req(path, token))).toBe('pass');
		expect(await signedInByCookieHeader(env, cookie(token))).toBe(true);
	});

	it('removing a passkey ends the sessions it opened, and no others', async () => {
		const lost = openSession(Date.now(), undefined, false, 'lost-laptop');
		const other = openSession(Date.now(), undefined, false, 'desk');
		const typed = openSession();
		closePasskeySessions('lost-laptop');
		expect(await admit(env, req('/api/state', lost))).toBe('refuse');
		expect(await admit(env, req('/api/state', other))).toBe('pass');
		expect(await admit(env, req('/api/state', typed))).toBe('pass');
	});

	it('keyed: only a session a key opened says so, which is what keeps its lock a screen', () => {
		expect(sessionKeyed(openSession(Date.now(), undefined, true))).toBe(true);
		expect(sessionKeyed(openSession(Date.now(), 'phone-1'))).toBe(true);
		expect(sessionKeyed(openSession(Date.now(), undefined, false, 'desk'))).toBe(false);
		expect(sessionKeyed(openSession())).toBe(false);
		expect(sessionKeyed('nothing')).toBe(false);
	});

	it('keyed: a User-Agent that says it is the phone opens nothing', async () => {
		expect(await admit(env, { ...req('/api/state', undefined), accept: 'text/html' })).toBe('refuse');
	});
});
