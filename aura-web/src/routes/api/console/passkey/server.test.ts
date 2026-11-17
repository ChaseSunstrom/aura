import { describe, it, expect, beforeEach, vi } from 'vitest';

// Who may ask the passkey route for what (T-0946). The verification itself is
// `passkeys.test.ts`; this is the route's own promise, since the path sits outside
// the gate: a stranger gets a sign-in challenge and a yes/no, and nothing else.

const env = vi.hoisted(() => ({}) as Record<string, string>);
vi.mock('$env/dynamic/private', () => ({ env }));

import { MAX_ATTEMPTS, SESSION_COOKIE, isLocked, openSession, resetConsoleAuth, sessionValid } from '$lib/server/consoleAuth';
import { resetPasskeys } from '$lib/server/passkeys';
import { DELETE, GET, POST } from './+server';

type Handler = (event: never) => Response | Promise<Response>;

function event(body: unknown, session?: string, address = '192.0.2.10') {
	const jar = new Map<string, string>(session ? [[SESSION_COOKIE, session]] : []);
	return {
		jar,
		event: {
			request: new Request('https://console.example/api/console/passkey', {
				method: 'POST',
				headers: { 'content-type': 'application/json', 'x-forwarded-proto': 'https' },
				body: JSON.stringify(body)
			}),
			cookies: {
				get: (name: string) => jar.get(name),
				set: (name: string, value: string) => void jar.set(name, value)
			},
			getClientAddress: () => address
		}
	};
}

/** The status a handler answered or threw (SvelteKit's `error()` throws). */
async function status(handler: Handler, e: unknown): Promise<number> {
	try {
		return (await handler(e as never)).status;
	} catch (thrown) {
		return (thrown as { status?: number }).status ?? 500;
	}
}

beforeEach(() => {
	resetConsoleAuth();
	resetPasskeys();
	for (const key of Object.keys(env)) delete env[key];
	Object.assign(env, {
		AURA_CONSOLE_ORIGIN: 'https://console.example',
		AURA_CONSOLE_PASSWORD: 'a-long-console-password', // pragma: allowlist secret
		AURA_CONSOLE_PASSWORD_FILE: '/nonexistent/console-password'
	});
});

describe('the passkey route', () => {
	it('lets only a signed-in browser add, list or remove a passkey', async () => {
		expect(await status(POST as Handler, event({ action: 'register-options' }).event)).toBe(401);
		expect(await status(POST as Handler, event({ action: 'register', response: {} }).event)).toBe(401);
		expect(await status(DELETE as Handler, event({ id: 'x' }).event)).toBe(401);
		expect(await status(POST as Handler, event({ action: 'register-options' }, openSession()).event)).toBe(200);
		const listed = await ((await (GET as Handler)(event({}).event as never)) as Response).json();
		expect(listed).toMatchObject({ available: true, registered: false, passkeys: [] });
	});

	it('adds no passkey to a console that has no password', async () => {
		delete env.AURA_CONSOLE_PASSWORD;
		expect(await status(POST as Handler, event({ action: 'register-options' }, openSession()).event)).toBe(409);
	});

	it('says why when the address is not set, and offers nothing', async () => {
		delete env.AURA_CONSOLE_ORIGIN;
		expect(await status(POST as Handler, event({ action: 'sign-in-options' }).event)).toBe(409);
		const told = await ((await (GET as Handler)(event({}).event as never)) as Response).json();
		expect(told.available).toBe(false);
		expect(told.problem).toContain('AURA_CONSOLE_ORIGIN');
	});

	it('opens no session for a bad answer, and its failures do not lock the password', async () => {
		for (let i = 0; i < MAX_ATTEMPTS; i += 1) {
			const tried = event({ action: 'sign-in', response: { id: 'nobody' } });
			expect(await status(POST as Handler, tried.event)).toBe(401);
			expect(sessionValid(tried.jar.get(SESSION_COOKIE))).toBe(false);
		}
		expect(await status(POST as Handler, event({ action: 'sign-in-options' }).event)).toBe(429);
		// The password's bucket is the bare address (`clientKey` in ../+server.ts).
		expect(isLocked('192.0.2.10')).toBe(false);
		expect(await status(POST as Handler, event({ action: 'sign-in-options' }, undefined, '192.0.2.11').event)).toBe(200);
	});
});
