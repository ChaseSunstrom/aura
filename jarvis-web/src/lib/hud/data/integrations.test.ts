import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { ACCOUNT_EXAMPLES, accountsView, integrationsView, unwrapUntrusted } from './integrations';

// `tests/contracts/connect-reads.json` is the table of what each provider can be asked; a
// provider added there without an example would show a connected account with nothing to try.
describe('account examples', () => {
	const table = JSON.parse(
		readFileSync(fileURLToPath(new URL('../../../../../tests/contracts/connect-reads.json', import.meta.url)), 'utf8')
	) as { reads: Record<string, string[]>; writes: Record<string, string[]> };

	it('offers an example ask for every provider with a read', () => {
		for (const provider of Object.keys(table.reads)) expect(ACCOUNT_EXAMPLES[provider]?.length, provider).toBeGreaterThan(0);
	});

	it('offers an add example for every provider with a write', () => {
		for (const provider of Object.keys(table.writes))
			expect(ACCOUNT_EXAMPLES[provider]?.some((ask) => ask.startsWith('Add ')), provider).toBe(true);
	});
});

// `tests/contracts/untrusted-fence.json` is the table; jarvis-core's
// quarantine.unwrap reads the same one.
const CONTRACT = JSON.parse(
	readFileSync(
		fileURLToPath(new URL('../../../../../tests/contracts/untrusted-fence.json', import.meta.url)),
		'utf8'
	)
);

describe('unwrapUntrusted', () => {
	for (const c of CONTRACT.cases) {
		it(c.name, () => expect(unwrapUntrusted(c.fenced)).toBe(c.plain));
	}
});

describe('integrationsView fence', () => {
	const fenced = CONTRACT.cases[0].fenced as string;
	it('shows no fence text for catalogue or extension rows', () => {
		const rows = integrationsView({
			catalogue: [{ id: 'a', kind: 'skill', description: fenced }],
			extensions: [{ id: 'e', kind: 'skill', key: 'e', description: fenced }]
		});
		for (const r of rows) {
			expect(r.detail).toContain('Turns the lights red.');
			expect(r.detail).not.toMatch(/untrusted_content|NOTE TO THE MODEL/);
		}
	});
});

// T-1343: `oauth.status` through call_service, as the house answers it (a response keyed
// `response`, every provider in the table, no token). Made-up values only.
const NOW = 1_800_000_000_000;
const tested = (ok: boolean, calls: { name: string; ok: boolean; http: number }[], minutesAgo = 5) => ({ ok, at: NOW / 1000 - minutesAgo * 60, calls });
const STATUS = {
	response: {
		providers: {
			google: { label: 'Google', configured: true, status: 'connected', last_test: tested(true, [{ name: 'calendar', ok: true, http: 200 }, { name: 'gmail', ok: true, http: 200 }]) },
			github: { label: 'GitHub', configured: true, status: 'connected', last_test: tested(false, [{ name: 'repos', ok: true, http: 200 }, { name: 'notifications', ok: false, http: 403 }], 90) },
			microsoft: { label: 'Microsoft 365', configured: true, status: 'needs_consent', last_test: null },
			slack: { label: 'Slack', configured: true, status: 'disconnected', last_test: null },
			spotify: { label: 'Spotify', configured: false, status: 'not_configured', last_test: null },
			notion: { label: 'Notion', configured: true, status: 'connected', last_test: null }
		}
	}
};

describe('accountsView', () => {
	const rows = accountsView(STATUS, NOW)!;
	const row = (id: string) => rows.find((r) => r.id === id)!;

	it('is one row per provider, in the house order', () => {
		expect(rows.map((r) => r.id)).toEqual(['google', 'github', 'microsoft', 'slack', 'spotify', 'notion']);
		expect(row('microsoft').name).toBe('Microsoft 365');
	});

	it('a connected account says when it was last tested and offers Test, Disconnect and example asks', () => {
		const g = row('google');
		expect(g.state).toBe('connected');
		expect(g.word).toBe('Connected');
		expect(g.detail).toBe('Tested 5 minutes ago: 2 of 2 calls passed');
		expect(g.asks.connect).toBeUndefined();
		expect(g.asks.test).toMatch(/Test my Google/);
		expect(g.asks.disconnect).toMatch(/Disconnect my Google/);
		expect(g.asks.examples.length).toBeGreaterThan(0);
		expect(row('microsoft').asks.examples).toEqual([]); // not connected: nothing to read from
		expect(row('notion').asks.examples).toEqual(['Which Notion workspace am I connected to?']); // its one read
	});

	it('says which test call failed, and a connected account never tested yet says so', () => {
		expect(row('github').detail).toBe('Tested 2 hours ago: 1 of 2 calls passed, notifications failed');
		expect(row('notion').detail).toBe('Not tested yet');
	});

	it('an account that needs sign-in (lapsed or never signed in) offers Connect and nothing else', () => {
		for (const id of ['microsoft', 'slack']) {
			const r = row(id);
			expect(r.state).toBe('needs-signin');
			expect(r.asks.connect).toContain(`provider ${id}`);
			expect(r.asks.test).toBeUndefined();
			expect(r.asks.disconnect).toBeUndefined();
		}
		expect(row('microsoft').detail).toMatch(/lapsed/i);
		expect(row('slack').detail).toMatch(/not signed in/i);
	});

	it('a provider with no client set up says what to do and offers no Connect', () => {
		const s = row('spotify');
		expect(s.state).toBe('not-set-up');
		expect(s.word).toBe('Not set up');
		expect(s.detail).toMatch(/client id and secret/i);
		expect(s.detail).toContain('OAUTH_SPOTIFY_CLIENT_ID');
		expect(s.asks).toEqual({ examples: [] });
	});

	it('is null for a house that did not answer, or answered with something else', () => {
		for (const bad of [undefined, null, 'x', {}, { response: {} }, { response: { providers: 3 } }, { response: { providers: [] } }]) {
			expect(accountsView(bad, NOW)).toBeNull();
		}
	});

	it('also reads the answer unwrapped or keyed service_response', () => {
		expect(accountsView(STATUS.response, NOW)).toHaveLength(6);
		expect(accountsView({ service_response: STATUS.response }, NOW)).toHaveLength(6);
	});

	it('an account says when its token expires and why the last refresh failed, in the house words only', () => {
		const p = (extra: object) => accountsView({ response: { providers: { google: { label: 'Google', configured: true, status: 'connected', last_test: null, ...extra } } } }, NOW)![0]!;
		expect(p({ expires_at: NOW / 1000 + 55 * 60, last_refresh_error: { reason: 'busy', at: NOW / 1000 - 120 } }).notes).toEqual(['Expires in 55 minutes', 'Last refresh failed: busy, 2 minutes ago']);
		expect(p({ expires_at: NOW / 1000 - 5 }).notes).toEqual(['Token expired, renewed on next use']);
		expect(p({ expires_at: null, last_refresh_error: null }).notes).toEqual([]); // a token that never expires says nothing
		// Not one of the three words: not shown, so provider text can never reach the line.
		const odd = p({ last_refresh_error: { reason: 'ya29.SECRET invalid_grant', at: NOW / 1000 }, access_token: 'ya29.SECRET' });
		expect(JSON.stringify(odd)).not.toMatch(/SECRET/);
		expect(odd.notes).toEqual([]);
		const lapsed = accountsView({ response: { providers: { google: { label: 'Google', configured: true, status: 'needs_consent', expires_at: null, last_refresh_error: { reason: 'refused', at: NOW / 1000 - 3600 } } } } }, NOW)![0]!;
		expect(lapsed.notes).toEqual(['Last refresh failed: refused, an hour ago']);
	});

	it('copies only what it shows: a token the house should never send does not get through', () => {
		const leaky = {
			response: {
				providers: {
					google: { label: 'Google', configured: true, status: 'connected', access_token: 'ya29.SECRET', client_secret: 'shh', last_test: { ok: true, at: NOW / 1000, calls: [{ name: 'gmail', ok: true, http: 200, token: 'ya29.SECRET' }] } }
				}
			}
		};
		expect(JSON.stringify(accountsView(leaky, NOW))).not.toMatch(/SECRET|shh/);
	});

	it('skips a row whose id is not what the house sends, and shows no unknown state as connected', () => {
		const odd = { response: { providers: { 'Bad Id!': { label: 'x', status: 'connected' }, google: { label: 'Google', configured: true, status: 'something-new' }, x: 7 } } };
		const out = accountsView(odd, NOW)!;
		expect(out.map((r) => r.id)).toEqual(['google']);
		expect(out[0]!.state).toBe('needs-signin');
	});
});
