import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({ env: { HA_URL: 'http://house.test:8080', HA_TOKEN: 'core-token' } })); // pragma: allowlist secret

import { GET } from './+server';

const ask = async () => GET({ url: new URL('http://console.test/api/messages') } as Parameters<typeof GET>[0]);
const status = (run: Promise<unknown>) => run.then(() => 200, (e: { status?: number }) => e.status);

afterEach(() => vi.unstubAllGlobals());

describe('GET /api/messages', () => {
	// A rotated core token: the house says 401, and relayed as-is the Texts screen read
	// "The console answered 401", which is the console's own sign-in failing (T-0673).
	it.each([401, 403, 404, 500])('answers 502 when the house answers %i', async (code) => {
		vi.stubGlobal('fetch', async () => new Response('{}', { status: code }));
		expect(await status(ask())).toBe(502);
	});

	it('answers 502 when the house answers something that is not JSON', async () => {
		vi.stubGlobal('fetch', async () => new Response('<html>proxy error</html>', { status: 200 }));
		expect(await status(ask())).toBe(502);
	});

	it('relays the texts as the house wrote them, never cached', async () => {
		vi.stubGlobal('fetch', async () => Response.json({ trust: 'untrusted', messages: [] }));
		const res = await ask();
		expect(await res.json()).toEqual({ trust: 'untrusted', messages: [] });
		expect(res.headers.get('cache-control')).toBe('no-store');
	});
});
