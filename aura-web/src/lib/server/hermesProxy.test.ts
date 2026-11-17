import { describe, it, expect, afterAll } from 'vitest';
import http from 'node:http';
import { WebSocket, WebSocketServer } from 'ws';
import { FORWARDED_REQUEST_HEADERS, hermesProxyTarget, resolveAdapter as resolveTs } from './hermes';
// server/hermes-proxy.js is copied verbatim into build/ and carries its own copy
// of resolveAdapter; this test is the guard that the two agree.
import { attachHermesProxy, resolveAdapter as resolveJs } from '../../../server/hermes-proxy.js';

describe('resolveAdapter', () => {
	const CASES: Record<string, string | undefined>[] = [
		{},
		{ AURA_ADAPTER_URL: 'http://jarvis:8643/', AURA_ADAPTER_KEY: 'k' },
		{ AURA_ADAPTER_URL: '  https://jarvis.tail.ts.net:8643  ' },
		{ AURA_ADAPTER_KEY: 'k' }
	];
	it('matches between the TypeScript and the relay copy for every env shape', () => {
		for (const env of CASES) expect(resolveJs(env), JSON.stringify(env)).toEqual(resolveTs(env));
	});
	it('derives the socket url and says whether it is usable', () => {
		const cfg = resolveTs({ AURA_ADAPTER_URL: 'https://jarvis:8643/', AURA_ADAPTER_KEY: 'k' });
		expect(cfg.url).toBe('https://jarvis:8643');
		expect(cfg.wsUrl).toBe('wss://jarvis:8643/ws');
		expect(cfg.configured).toBe(true);
		expect(resolveTs({ AURA_ADAPTER_URL: 'http://x' }).configured).toBe(false);
	});
});

describe('FORWARDED_REQUEST_HEADERS', () => {
	// The phone uploads a car drive log through this relay since it lost Tailscale
	// (T-0386); without these the adapter filed it as an untitled octet-stream.
	it('carries the artifact upload headers', () => {
		for (const h of ['x-artifact-mime', 'x-artifact-title', 'x-artifact-tool', 'x-artifact-ttl']) {
			expect(FORWARDED_REQUEST_HEADERS).toContain(h);
		}
	});
});

describe('hermesProxyTarget', () => {
	const base = 'http://adapter:8643';
	it('relays the two prefixes and keeps the query', () => {
		expect(hermesProxyTarget('api/status', base)).toBe('http://adapter:8643/api/status');
		expect(hermesProxyTarget('v1/toolsets', base, '?x=1')).toBe('http://adapter:8643/v1/toolsets?x=1');
		expect(hermesProxyTarget('api/jobs/abc/run', base)).toBe('http://adapter:8643/api/jobs/abc/run');
		expect(hermesProxyTarget('api/audio/transcribe', base)).toBe('http://adapter:8643/api/audio/transcribe');
	});
	it('refuses everything outside them', () => {
		expect(hermesProxyTarget('health', base)).toBeNull();
		expect(hermesProxyTarget('ws', base)).toBeNull();
		expect(hermesProxyTarget('', base)).toBeNull();
		expect(hermesProxyTarget('//evil/api/status', base)).toBeNull();
	});
	it('refuses the pairing payload, which carries the key', () => {
		expect(hermesProxyTarget('api/pair', base)).toBeNull();
		expect(hermesProxyTarget('api/pair/', base)).toBeNull();
		expect(hermesProxyTarget('api/pairing', base)).toBe('http://adapter:8643/api/pairing');
	});
	it('refuses the device keys, whose revoke goes through the console route (T-0569)', () => {
		expect(hermesProxyTarget('api/devices/keys', base)).toBeNull();
		expect(hermesProxyTarget('api/devices/keys/d1', base)).toBeNull();
		expect(hermesProxyTarget('api/devices', base)).toBe('http://adapter:8643/api/devices');
	});
	it('re-tests the normalised path, so an encoded dot segment cannot escape', () => {
		expect(hermesProxyTarget('api/%2e%2e/health', base)).toBeNull();
		expect(hermesProxyTarget('api/../health', base)).toBeNull();
		expect(hermesProxyTarget('v1/../api/pair', base)).toBeNull();
	});
	it('keeps a sub-path base intact', () => {
		expect(hermesProxyTarget('api/status', 'http://host/hermes')).toBe('http://host/hermes/api/status');
	});
});

// --- the socket relay ---------------------------------------------------------

function listen(server: http.Server): Promise<number> {
	return new Promise((resolve) => {
		server.listen(0, '127.0.0.1', () => resolve((server.address() as { port: number }).port));
	});
}

describe('attachHermesProxy', () => {
	const closers: Array<() => void> = [];
	afterAll(() => closers.forEach((c) => c()));

	async function adapter() {
		const seen: { auth: string | undefined; frames: string[] } = { auth: undefined, frames: [] };
		const server = http.createServer();
		const wss = new WebSocketServer({ server, path: '/ws' });
		wss.on('connection', (socket, req) => {
			seen.auth = req.headers.authorization;
			socket.send(JSON.stringify({ jsonrpc: '2.0', method: 'event', params: { type: 'gateway.ready', payload: {} } }));
			socket.on('message', (data, isBinary) => {
				if (isBinary) socket.send(data, { binary: true });
				else {
					seen.frames.push(data.toString());
					const frame = JSON.parse(data.toString());
					socket.send(JSON.stringify({ jsonrpc: '2.0', id: frame.id, result: { echoed: frame.method } }));
				}
			});
		});
		const port = await listen(server);
		closers.push(() => server.close());
		return { url: `http://127.0.0.1:${port}`, seen };
	}

	type SignedIn = ((cookie: string | undefined) => boolean | Promise<boolean>) | null;
	async function relay(env: Record<string, string>, signedIn: SignedIn = () => true, signInRecheckMs?: number) {
		const server = http.createServer((_req, res) => res.end('ok'));
		attachHermesProxy(server, { env, signedIn: signedIn ?? undefined, signInRecheckMs });
		const port = await listen(server);
		closers.push(() => server.close());
		return { port, host: `127.0.0.1:${port}` };
	}

	function dial(url: string, headers: Record<string, string>): Promise<{ ws: WebSocket; status?: number }> {
		return new Promise((resolve) => {
			const ws = new WebSocket(url, { headers });
			ws.on('open', () => resolve({ ws }));
			ws.on('unexpected-response', (_req, res) => resolve({ ws, status: res.statusCode }));
			ws.on('error', () => resolve({ ws, status: 0 }));
		});
	}

	it('injects the key for a same-origin browser and relays text and binary frames verbatim', async () => {
		const up = await adapter();
		const { port, host } = await relay({ AURA_ADAPTER_URL: up.url, AURA_ADAPTER_KEY: 'secret-key' });
		const { ws, status } = await dial(`ws://${host}/hermes/ws`, { origin: `http://${host}` });
		expect(status).toBeUndefined();
		const frames: any[] = [];
		const binaries: Buffer[] = [];
		ws.on('message', (d, isBinary) => (isBinary ? binaries.push(d as Buffer) : frames.push(JSON.parse(d.toString()))));
		ws.send(JSON.stringify({ jsonrpc: '2.0', id: 7, method: 'session.list', params: {} }));
		ws.send(Buffer.from([1, 2, 3]), { binary: true });
		await new Promise((r) => setTimeout(r, 200));
		expect(up.seen.auth).toBe('Bearer secret-key');
		expect(frames.map((f) => f.method ?? f.result?.echoed)).toEqual(['event', 'session.list']);
		expect(binaries[0]).toEqual(Buffer.from([1, 2, 3]));
		ws.close();
		void port;
	});

	it('refuses a browser on another origin with 403 and a bare client with 401', async () => {
		const up = await adapter();
		const { host } = await relay({ AURA_ADAPTER_URL: up.url, AURA_ADAPTER_KEY: 'k' });
		expect((await dial(`ws://${host}/hermes/ws`, { origin: 'http://evil.example' })).status).toBe(403);
		expect((await dial(`ws://${host}/hermes/ws`, {})).status).toBe(401);
	});

	// T-0387: the Origin header proves nothing to a caller on the LAN, so the
	// server's key goes only to a browser that signed in to the console.
	it('refuses a same-origin browser that has not signed in, and fails closed with no check', async () => {
		const up = await adapter();
		const env = { AURA_ADAPTER_URL: up.url, AURA_ADAPTER_KEY: 'k' };
		const gated = await relay(env, (c) => c === 'jarvis_console=good');
		const origin = { origin: `http://${gated.host}` };
		expect((await dial(`ws://${gated.host}/hermes/ws`, origin)).status).toBe(401);
		expect((await dial(`ws://${gated.host}/hermes/ws`, { ...origin, cookie: 'jarvis_console=bad' })).status).toBe(401);
		const ok = await dial(`ws://${gated.host}/hermes/ws`, { ...origin, cookie: 'jarvis_console=good' });
		expect(ok.status).toBeUndefined();
		ok.ws.close();
		const bare = await relay(env, null);
		expect((await dial(`ws://${bare.host}/hermes/ws`, { origin: `http://${bare.host}` })).status).toBe(401);
		expect(up.seen.auth).toBe('Bearer k');
	});

	// T-0569: revoking a device ends the console session its own key opened; the socket
	// that session opened, which carries the server's key upstream, must not outlive it.
	it('closes a signed-in socket 4401 once its sign-in ends', async () => {
		const up = await adapter();
		let live = true;
		const { host } = await relay({ AURA_ADAPTER_URL: up.url, AURA_ADAPTER_KEY: 'k' }, () => live, 40);
		const { ws, status } = await dial(`ws://${host}/hermes/ws`, { origin: `http://${host}`, cookie: 'jarvis_console=x' });
		expect(status).toBeUndefined();
		const closed = new Promise<number>((resolve) => ws.on('close', (c) => resolve(c)));
		await new Promise((r) => setTimeout(r, 120));
		expect(ws.readyState).toBe(WebSocket.OPEN);
		live = false;
		expect(await closed).toBe(4401);
	});

	it('passes a client’s own bearer token through instead of the server’s key', async () => {
		const up = await adapter();
		const { host } = await relay({ AURA_ADAPTER_URL: up.url, AURA_ADAPTER_KEY: 'server-key' });
		const { ws, status } = await dial(`ws://${host}/hermes/ws`, { authorization: 'Bearer phone-key' });
		expect(status).toBeUndefined();
		await new Promise((r) => setTimeout(r, 100));
		expect(up.seen.auth).toBe('Bearer phone-key');
		ws.close();
	});

	it('closes with 1011 when the adapter is not configured', async () => {
		const { host } = await relay({});
		const { ws } = await dial(`ws://${host}/hermes/ws`, { origin: `http://${host}` });
		const code = await new Promise<number>((resolve) => ws.on('close', (c) => resolve(c)));
		expect(code).toBe(1011);
	});

	it('leaves other upgrade paths alone, so the /ws relay beside it still gets its own', async () => {
		const up = await adapter();
		const { host } = await relay({ AURA_ADAPTER_URL: up.url, AURA_ADAPTER_KEY: 'k' });
		const outcome = await Promise.race([
			dial(`ws://${host}/ws`, { origin: `http://${host}` }).then(() => 'taken'),
			new Promise<string>((r) => setTimeout(() => r('left alone'), 300))
		]);
		expect(outcome).toBe('left alone');
		expect(up.seen.auth).toBeUndefined();
	});
});
