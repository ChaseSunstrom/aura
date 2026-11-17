// The Hermes HUD socket relay.
//
// The browser connects to ws(s)://<origin>/hermes/ws; this opens a server-side
// socket to ${AURA_ADAPTER_URL}/ws with `Authorization: Bearer <key>` and
// relays frames verbatim in both directions — JSON text and binary alike. The
// adapter speaks Hermes's own JSON-RPC, so there is no handshake to swallow:
// the first frame the page sees is the gateway's `gateway.ready`.
//
// Same origin guard as the `/ws` relay, for the same reason: a socket on this
// relay carries the adapter's key, and WebSocket upgrades are exempt from the
// same-origin policy unless the server checks Origin itself. Origin names the
// page, not the person, and any LAN client can type it: so a browser must also
// hold a console session (`signedIn`, T-0387), and with no check registered it is
// refused. A client that brings its own bearer token is passed through with that
// token instead.
//
// Used by the production launcher (build/index.js, see scripts/postbuild.mjs)
// and by the vite dev server (vite.config.ts).

import { WebSocketServer, WebSocket } from 'ws';
import { bearerFromRequest, isOriginAllowed, parseAllowedOrigins, refuse, signedIn, watchSignIn } from './ws-proxy.js';

// Hand-copy of src/lib/server/hermes.ts — this file is copied verbatim into
// build/ by scripts/postbuild.mjs, so it cannot import from src/. Keep in sync;
// hermesProxy.test.ts diffs both copies.
/**
 * @param {Record<string, string|undefined>} env
 * @returns {{ url: string, key: string, wsUrl: string, configured: boolean }}
 */
export function resolveAdapter(env = {}) {
	const url = String(env.AURA_ADAPTER_URL ?? '').trim().replace(/\/+$/, '');
	const key = String(env.AURA_ADAPTER_KEY ?? '').trim();
	return {
		url,
		key,
		wsUrl: url ? url.replace(/^http/, 'ws') + '/ws' : '',
		configured: Boolean(url && key)
	};
}

/**
 * @param {import('node:http').Server} httpServer
 * @param {{ path?: string, url?: string, key?: string,
 *           env?: Record<string, string|undefined>, allowedOrigins?: string[],
 *           signedIn?: (cookie: string|undefined) => boolean|Promise<boolean>, signInRecheckMs?: number }} [opts]
 */
export function attachHermesProxy(httpServer, opts = {}) {
	const path = opts.path ?? '/hermes/ws';
	const wss = new WebSocketServer({ noServer: true });

	httpServer.on('upgrade', async (req, socket, head) => {
		let pathname = '/';
		try {
			pathname = new URL(req.url ?? '/', 'http://internal').pathname;
		} catch {
			socket.destroy();
			return;
		}
		if (pathname !== path) return; // the /ws relay, vite HMR, …

		const env = opts.env ?? process.env;
		const presented = bearerFromRequest(req);
		if (!presented) {
			const allowed = opts.allowedOrigins ?? parseAllowedOrigins(env.AURA_ALLOWED_ORIGINS);
			const origin = req.headers?.origin;
			const isBrowser = origin !== undefined && origin !== null && origin !== '';
			if (isBrowser && !isOriginAllowed(origin, req.headers?.host, allowed)) {
				socket.write('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');
				socket.destroy();
				return;
			}
			if (!isBrowser) {
				socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n');
				socket.destroy();
				return;
			}
			// The adapter key goes only to a browser that signed in (T-0387).
			if (!(await signedIn(req, opts))) return refuse(socket, '401 Unauthorized');
			if (socket.destroyed) return;
		}

		const adapter = resolveAdapter(env);
		const url = opts.url ?? adapter.url;
		const key = presented ?? opts.key ?? adapter.key;
		wss.handleUpgrade(req, socket, head, (client) => {
			// A client's own key is the adapter's to refuse (it hangs up a revoked one);
			// the server's key rides only as long as the session that earned it.
			if (!presented) watchSignIn(client, req, opts);
			relay(client, { url, key });
		});
	});

	return wss;
}

/**
 * @param {import('ws').WebSocket} client
 * @param {{ url: string, key: string }} cfg
 */
function relay(client, { url, key }) {
	if (!url || !key) {
		client.close(1011, 'server missing AURA_ADAPTER_URL/AURA_ADAPTER_KEY');
		return;
	}
	const target = url.replace(/\/+$/, '').replace(/^http/, 'ws') + '/ws';
	const upstream = new WebSocket(target, { headers: { Authorization: `Bearer ${key}` } });

	/** @type {Array<[import('ws').RawData, boolean]>} */
	const pending = [];
	let open = false;

	client.on('message', (data, isBinary) => {
		if (!open) pending.push([data, isBinary]);
		else if (upstream.readyState === WebSocket.OPEN) upstream.send(data, { binary: isBinary });
	});
	upstream.on('open', () => {
		open = true;
		for (const [d, b] of pending) upstream.send(d, { binary: b });
		pending.length = 0;
	});
	upstream.on('message', (data, isBinary) => {
		if (client.readyState === WebSocket.OPEN) client.send(data, { binary: isBinary });
	});

	// The adapter's own refusal (4401, a bad key) reaches the page as-is, so
	// the failure is diagnosable from the browser; anything else closes clean.
	upstream.on('close', (code, reason) => {
		if (client.readyState === WebSocket.OPEN || client.readyState === WebSocket.CONNECTING) {
			const ok = code === 1000 || (code >= 4000 && code < 5000);
			client.close(ok ? code : 1011, reason?.toString() ?? '');
		}
	});
	upstream.on('error', () => {
		if (client.readyState === WebSocket.OPEN || client.readyState === WebSocket.CONNECTING) {
			client.close(1011, 'adapter unreachable');
		}
	});
	const closeUpstream = () => {
		if (upstream.readyState === WebSocket.OPEN || upstream.readyState === WebSocket.CONNECTING) {
			upstream.close();
		}
	};
	client.on('close', closeUpstream);
	client.on('error', closeUpstream);
}
