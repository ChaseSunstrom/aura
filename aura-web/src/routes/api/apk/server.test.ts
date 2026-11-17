import { describe, expect, it } from 'vitest';
import { STORE_URL } from '$lib/hud/data/apps';
import { GET } from './+server';

const HASH = 'ab'.repeat(32);
const row = (code: number) => ({
	mime: 'application/vnd.android.package-archive',
	title: `Jarvis v2.0.${code}`,
	signed_url: `https://house.example/api/artifacts/${HASH}?sig=${code}`,
	created: 1_700_000_000 + code
});

/** Call the route with a listing answer, and return what it asked for and what it said. */
async function ask(answer: Response | Error): Promise<{ asked: string[]; res: Response }> {
	const asked: string[] = [];
	const fetch = async (url: string) => {
		asked.push(url);
		if (answer instanceof Error) throw answer;
		return answer;
	};
	try {
		return { asked, res: await (GET as unknown as (e: unknown) => Promise<Response>)({ fetch }) };
	} catch (thrown) {
		// SvelteKit's redirect() and error() are thrown, not returned.
		const t = thrown as { status: number; location?: string; body?: { message: string } };
		return { asked, res: new Response(t.body?.message ?? null, { status: t.status, headers: t.location ? { location: t.location } : {} }) };
	}
}

describe('/api/apk', () => {
	it('redirects to the newest stored build, asking the store through the console itself', async () => {
		const { asked, res } = await ask(Response.json({ artifacts: [row(7), row(12), row(9)] }));
		expect(asked).toEqual([STORE_URL]);
		expect(res.status).toBe(302);
		expect(res.headers.get('location')).toBe(row(12).signed_url);
	});

	it('answers 404 when the store holds no build', async () => {
		expect((await ask(Response.json({ artifacts: [] }))).res.status).toBe(404);
	});

	it('does not follow a link that is not an artifact', async () => {
		const bad = { ...row(3), signed_url: 'https://elsewhere.example/get.apk' };
		expect((await ask(Response.json({ artifacts: [bad] }))).res.status).toBe(404);
	});

	it('says the house did not answer when the listing cannot be reached', async () => {
		expect((await ask(new TypeError('fetch failed'))).res.status).toBe(502);
	});

	it('passes a signed-out answer on instead of a link', async () => {
		const { res } = await ask(new Response('sign in', { status: 401 }));
		expect(res.status).toBe(401);
		expect(res.headers.get('location')).toBeNull();
	});
});
