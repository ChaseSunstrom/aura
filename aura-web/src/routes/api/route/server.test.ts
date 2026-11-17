import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({ env: { HA_URL: 'http://house.test:8080', HA_TOKEN: 'core-token' } })); // pragma: allowlist secret

import { GET } from './+server';

const ask = async (query = 'from=59.33,18.06&to=59.34,18.07') =>
	GET({ url: new URL(`http://console.test/api/route?${query}`) } as Parameters<typeof GET>[0]);
// What the page reads: a status and, for an error, the reason in the body's `message`.
const outcome = (run: Promise<Response>) =>
	run.then(
		(r) => ({ status: r.status, message: '' }),
		(e: { status?: number; body?: { message?: string } }) => ({ status: e.status, message: e.body?.message })
	);
// The house's own error shape (jarvis-core `_api_error`): a FastAPI detail with a code and a message.
const house = (status: number, detail: unknown) => async () => Response.json({ detail }, { status });

afterEach(() => vi.unstubAllGlobals());

describe('GET /api/route', () => {
	it('relays the house 400 with its reason, as 400', async () => {
		const message = 'via is up to 5 [lat, lon] stops, in order';
		vi.stubGlobal('fetch', house(400, { code: 'bad_via', message }));
		expect(await outcome(ask())).toEqual({ status: 400, message });
	});

	it('answers 502 when the house cannot answer at all', async () => {
		vi.stubGlobal('fetch', async () => Promise.reject(new TypeError('connect ECONNREFUSED')));
		expect((await outcome(ask())).status).toBe(502);
	});

	it.each([500, 502, 503])('answers 502 when the house fails with %i', async (code) => {
		vi.stubGlobal('fetch', house(code, { code: 'router_down', message: 'valhalla is away' }));
		expect((await outcome(ask())).status).toBe(502);
	});

	// A rotated core token is the console's own sign-in failing, not the driver's request (T-0673).
	it.each([401, 403])('answers 502, not %i, when the house refuses the console', async (code) => {
		vi.stubGlobal('fetch', house(code, { code: 'unauthorized', message: 'bad token' }));
		expect((await outcome(ask())).status).toBe(502);
	});

	it('keeps 404 as "no route"', async () => {
		vi.stubGlobal('fetch', house(404, { code: 'no_route', message: 'no route between those points' }));
		expect(await outcome(ask())).toEqual({ status: 404, message: 'no route' });
	});

	it('still says something for a 4xx whose body is not the house shape, and bounds a long reason', async () => {
		vi.stubGlobal('fetch', async () => new Response('<html>bad</html>', { status: 400 }));
		expect((await outcome(ask())).message).toBe('the house refused the route (400)');
		vi.stubGlobal('fetch', house(400, { code: 'x', message: 'y'.repeat(5000) }));
		expect((await outcome(ask())).message?.length).toBeLessThanOrEqual(200);
	});

	it('relays a route', async () => {
		vi.stubGlobal('fetch', async () => Response.json({ routes: [] }));
		const res = await ask();
		expect(res.status).toBe(200);
		expect(await res.json()).toEqual({ routes: [] });
	});
});
