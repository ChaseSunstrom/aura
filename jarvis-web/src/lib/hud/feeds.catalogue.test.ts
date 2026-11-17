import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Feeds } from './feeds.svelte';

// The integrations feed read `jarvis/extensions/browse` for its entries only, through an
// `optional()` that turned a 1.5 s wait into an empty list: a house with no route out drew an
// empty catalogue with nothing saying why, and the reply's `errors` were dropped (T-0323).

const tool = { name: 'lock', description: 'Lock a door', tier: 3 };

function houseWith(browse: (msg: Record<string, unknown>) => Promise<unknown>, installed = [tool]) {
	const client = {
		listToolRows: async () => installed,
		listSkills: async () => ({ skills: [] }),
		listMcpServers: async () => ({ servers: [] }),
		subscribeEvents: async () => ({ unsubscribe: async () => {} }),
		command: (msg: { type: string }) => (msg.type === 'jarvis/extensions/browse' ? browse(msg) : Promise.resolve({ extensions: [] }))
	};
	return { conn: { client }, hermes: null } as never;
}

beforeEach(() => {
	// Hermes' skills are not what this is about; a relay that is not there is the fallback.
	vi.stubGlobal('fetch', async () => new Response('[]', { status: 200 }));
});
afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

describe('integrations feed: the catalogue', () => {
	it('keeps the rows that loaded and says which registries could not be read', async () => {
		const feeds = new Feeds();
		await feeds.integrations.load(
			houseWith(async () => ({
				entries: [{ id: 'diary', kind: 'skill', source: 'bundled' }],
				sources: ['bundled', 'mcp-registry'],
				errors: [{ source: 'mcp-registry', error: 'Name or service not known' }]
			}))
		);
		const data = feeds.integrations.data;
		expect(feeds.integrations.status).toBe('ready');
		expect(data.catalogue.map((c) => c.id)).toEqual(['diary']);
		expect(data.catalogueProblem).toEqual({ sources: [{ source: 'mcp-registry', error: 'Name or service not known' }], timedOut: false, error: '' });
	});

	it('says why the catalogue is empty when no registry could be read', async () => {
		const feeds = new Feeds();
		const errors = [
			{ source: 'anthropic-skills', error: 'timed out' },
			{ source: 'mcp-registry', error: 'Name or service not known' }
		];
		await feeds.integrations.load(houseWith(async () => ({ entries: [], sources: ['anthropic-skills', 'mcp-registry'], errors, error: 'anthropic-skills: timed out; mcp-registry: Name or service not known' })));
		const data = feeds.integrations.data;
		expect(data.catalogue).toEqual([]);
		expect(data.catalogueProblem?.sources).toEqual(errors);
		expect(data.tools.map((t) => t.name)).toEqual(['lock']);
	});

	it('keeps a reason that names no registry', async () => {
		const feeds = new Feeds();
		await feeds.integrations.load(houseWith(async () => ({ entries: [], sources: [], errors: [], error: 'no catalog source is configured' })));
		expect(feeds.integrations.data.catalogueProblem).toEqual({ sources: [], timedOut: false, error: 'no catalog source is configured' });
	});

	it('turns a browse that does not answer in time into a problem, not an empty list', async () => {
		vi.useFakeTimers();
		const feeds = new Feeds();
		const loading = feeds.integrations.load(houseWith(() => new Promise(() => {})));
		await vi.advanceTimersByTimeAsync(1500);
		await loading;
		const data = feeds.integrations.data;
		expect(feeds.integrations.status).toBe('ready');
		expect(data.tools.map((t) => t.name)).toEqual(['lock']);
		expect(data.catalogue).toEqual([]);
		expect(data.catalogueProblem?.timedOut).toBe(true);
	});

	it('has no problem when every registry answered', async () => {
		const feeds = new Feeds();
		await feeds.integrations.load(houseWith(async () => ({ entries: [{ id: 'diary', kind: 'skill' }], sources: ['bundled'], errors: [] })));
		expect(feeds.integrations.data.catalogueProblem).toBeNull();
	});

	// T-0329: "Nothing installed" hid the Catalogue and the line saying why it was empty.
	it('is ready, not empty, for a house with nothing installed and a catalogue that could not be read', async () => {
		const feeds = new Feeds();
		await feeds.integrations.load(houseWith(async () => ({ entries: [], sources: ['bundled'], errors: [{ source: 'bundled', error: 'no catalog index' }], error: 'bundled: no catalog index' }), []));
		expect(feeds.integrations.status).toBe('ready');
		await feeds.integrations.load(houseWith(async () => ({ entries: [], sources: ['bundled'], errors: [] }), []));
		expect(feeds.integrations.status).toBe('empty');
	});
});

// T-0329: jarvis-core remembers a registry failure for 30 s, so Retry inside that window was
// answered with the failure it was retrying, at once, as if the registry had been tried again.
describe('integrations feed: Retry', () => {
	const answered = { entries: [], sources: ['bundled'], errors: [] };

	it('asks the browse to skip cached registry errors, once, and a plain reload does not', async () => {
		const feeds = new Feeds();
		const asked: Record<string, unknown>[] = [];
		const house = houseWith(async (msg) => (asked.push(msg), answered));
		feeds.setSources(house as never);
		await feeds.integrations.load(house);
		await feeds.retryCatalogue();
		await feeds.integrations.load(house);
		expect(asked.map((m) => m.refresh)).toEqual([undefined, true, undefined]);
	});

	it('says it is reloading while the rows are read, with the last rows still shown', async () => {
		const feeds = new Feeds();
		let release = () => {};
		const house = houseWith((): Promise<unknown> => new Promise((resolve) => (release = () => resolve(answered))));
		const first = feeds.integrations.load(house);
		expect(feeds.integrations.reloading).toBe(true);
		release();
		await first;
		expect(feeds.integrations.reloading).toBe(false);

		const again = feeds.integrations.load(house);
		expect(feeds.integrations.reloading).toBe(true);
		expect(feeds.integrations.status).toBe('ready');
		release();
		await again;
		expect(feeds.integrations.reloading).toBe(false);
	});

	// The review of T-0329: the button said 'Retrying…' on every load, the panel's own included.
	it('feeds.retrying is the Retry alone: set while its load runs, not for an initial or periodic one', async () => {
		const feeds = new Feeds();
		let release = () => {};
		const house = houseWith((): Promise<unknown> => new Promise((resolve) => (release = () => resolve(answered))));
		feeds.setSources(house as never);
		const first = feeds.integrations.load(house);
		expect(feeds.integrations.reloading).toBe(true);
		expect(feeds.retrying).toBe(false);
		release();
		await first;

		const retry = feeds.retryCatalogue();
		expect(feeds.retrying).toBe(true);
		release();
		await retry;
		expect(feeds.retrying).toBe(false);
	});

	it('leaves no refresh pending when there is no connection to ask, so the reconnect reload is plain', async () => {
		const feeds = new Feeds();
		await feeds.retryCatalogue();
		expect(feeds.retrying).toBe(false);
		const asked: Record<string, unknown>[] = [];
		await feeds.integrations.load(houseWith(async (msg) => (asked.push(msg), answered)));
		expect(asked.map((m) => m.refresh)).toEqual([undefined]);
	});

	it('a periodic reload that comes due mid-Retry waits for it: the Retry\'s rows are the ones kept', async () => {
		vi.useFakeTimers();
		const feeds = new Feeds();
		const asked: Record<string, unknown>[] = [];
		const replies: ((reply: unknown) => void)[] = [];
		const house = houseWith((msg): Promise<unknown> => (asked.push(msg), new Promise((resolve) => replies.push(resolve))));
		feeds.setSources(house as never);
		feeds.activate(['integrations']);
		replies[0]!({ entries: [], sources: ['bundled'], errors: [{ source: 'bundled', error: 'no catalog index' }] });
		await vi.advanceTimersByTimeAsync(0);
		expect(feeds.integrations.reloading).toBe(false);

		const retry = feeds.retryCatalogue();
		// A burst of link events arms the trailing reload; it comes due while the Retry is out.
		feeds.setSources(house as never);
		await vi.advanceTimersByTimeAsync(Feeds.REFRESH_MS);
		expect(asked.map((m) => m.refresh)).toEqual([undefined, true]);

		replies[1]!({ entries: [{ id: 'diary', kind: 'skill' }], sources: ['bundled'], errors: [] });
		await retry;
		expect(feeds.integrations.data.catalogue.map((c) => c.id)).toEqual(['diary']);
		expect(feeds.integrations.data.catalogueProblem).toBeNull();
		feeds.dispose();
	});

	// jarvis-core's registry client waits 3 s to connect (CONNECT_S); a Retry that gave up at the
	// plain load's 1.5 s ended in 'took too long' on every house whose egress is dropping, and
	// never showed the registry errors it had asked for.
	it('gives a Retry long enough for the registries\' own connect timeout, and a plain load not', async () => {
		vi.useFakeTimers();
		const errors = [{ source: 'mcp-registry', error: 'Cannot connect to host registry.modelcontextprotocol.io:443' }];
		const after3s = () => houseWith(() => new Promise((resolve) => setTimeout(() => resolve({ entries: [], sources: ['mcp-registry'], errors, error: 'x' }), 3000)));

		const retried = new Feeds();
		retried.setSources(after3s() as never);
		const retry = retried.retryCatalogue();
		await vi.advanceTimersByTimeAsync(3000);
		await retry;
		expect(retried.integrations.data.catalogueProblem).toEqual({ sources: errors, timedOut: false, error: 'x' });

		const plain = new Feeds();
		const load = plain.integrations.load(after3s());
		await vi.advanceTimersByTimeAsync(3000);
		await load;
		expect(plain.integrations.data.catalogueProblem?.timedOut).toBe(true);

		const silent = new Feeds();
		silent.setSources(houseWith(() => new Promise(() => {})) as never);
		const unanswered = silent.retryCatalogue();
		await vi.advanceTimersByTimeAsync(4500);
		await unanswered;
		expect(silent.integrations.data.catalogueProblem?.timedOut).toBe(true);
		expect(silent.integrations.status).toBe('ready');
	});
});
