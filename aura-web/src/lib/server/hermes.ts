// Where the Hermes adapter is, and what the console may relay to it.
//
// The apps' one credential — the adapter's bearer key — is held here, on the
// server, and never reaches the browser. The page talks to `/hermes/ws` and
// `/hermes/api/*` / `/hermes/v1/*` on its own origin; this server injects the
// key and forwards. Same principle as `backend.ts` and the `/ws` relay.
//
// NOTE: `server/hermes-proxy.js` carries a hand-copy of `resolveAdapter()`
// because it is copied verbatim into `build/` and cannot import from `src/`.
// `hermesProxy.test.ts` diffs the two.

import { climbsOut } from './backend';

export interface AdapterConfig {
	/** Base HTTP url of the adapter, trailing slashes stripped. Empty when unset. */
	url: string;
	/** The bearer key. Server-side only. */
	key: string;
	/** `ws(s)://…/ws`, the HUD socket. */
	wsUrl: string;
	configured: boolean;
}

export function resolveAdapter(env: Record<string, string | undefined> = {}): AdapterConfig {
	const url = String(env.AURA_ADAPTER_URL ?? '').trim().replace(/\/+$/, '');
	const key = String(env.AURA_ADAPTER_KEY ?? '').trim();
	return {
		url,
		key,
		wsUrl: url ? url.replace(/^http/, 'ws') + '/ws' : '',
		configured: Boolean(url && key)
	};
}

/** The adapter paths the browser may reach through the relay. */
export const HERMES_PREFIXES = ['/api/', '/v1/'] as const;

/**
 * Paths under those prefixes the browser may NOT reach.
 *
 * `/api/pair` answers with the key itself — it is the payload the phone's QR
 * carries — and relaying it would hand the browser the one thing this file
 * exists to keep from it. `/api/devices/keys` has its own console routes
 * (T-0569): a revoke there also ends the device's console sessions and its
 * house token, which one relayed here would skip.
 */
export const HERMES_DENIED = ['/api/pair', '/api/devices/keys'] as const;

/**
 * What `/llm/*` may reach on llama-swap: its OpenAI API only. Its `/ui`,
 * `/upstream/<model>/*`, `/logs` and `/unload` stay off the console's origin.
 */
export const LLM_PREFIXES = ['/v1/'] as const;

/**
 * Resolve a browser-supplied `path` (`api/status`, `v1/toolsets?x=1`) against
 * the adapter, or null when it escapes the allow-list.
 *
 * Normalised through the URL parser and re-tested, for the reason
 * `mediaProxyTarget` documents: `%2e%2e` is a dot segment to the parser and
 * not to a substring test.
 */
export function hermesProxyTarget(
	path: string,
	baseUrl: string,
	search = '',
	prefixes: readonly string[] = HERMES_PREFIXES
): string | null {
	const raw = '/' + String(path ?? '').replace(/^\/+/, '');
	if (raw.startsWith('//') || /[\\\r\n\t]/.test(raw)) return null;
	if (!prefixes.some((p) => raw.startsWith(p))) return null;

	let base: URL;
	let target: URL;
	try {
		base = new URL(baseUrl);
		target = new URL(base.href.replace(/\/+$/, '') + raw);
	} catch {
		return null;
	}
	if (target.origin !== base.origin || target.username || target.password) return null;
	const basePath = base.pathname.replace(/\/+$/, '');
	const rel = target.pathname.startsWith(basePath) ? target.pathname.slice(basePath.length) : '';
	if (!prefixes.some((p) => rel.startsWith(p))) return null;
	if (climbsOut(rel)) return null;
	if (HERMES_DENIED.some((d) => rel === d || rel.startsWith(d + '/'))) return null;
	const query = search && search !== '?' ? (search.startsWith('?') ? search : `?${search}`) : '';
	return target.href + query;
}

/** Headers copied from the browser's request to the adapter. */
// `range` rides through for the artifacts (`/api/artifacts/<id>`): a `<video>`
// asks for bytes from where the person seeked to, and a relay that dropped the
// header handed it the whole file from the top on every seek.
// The `x-artifact-*` headers name a car drive log the phone uploads through
// this relay since it lost Tailscale (T-0386); dropped, the adapter filed it
// as an untitled octet-stream with the default lifetime.
export const FORWARDED_REQUEST_HEADERS = ['content-type', 'accept', 'accept-language', 'range', 'x-artifact-mime', 'x-artifact-title', 'x-artifact-tool', 'x-artifact-ttl'] as const;

/** Headers copied from the adapter's answer to the browser. */
// The voice's own headers ride through: which engine spoke (and why a
// fallback did), and the sample rate of a PCM stream, without which the
// player cannot schedule it.
// `content-range` and `accept-ranges` are the other half of the range relay:
// without them a player never learns the file is seekable, and a 206 with no
// range header is a truncated 200 to it.
export const FORWARDED_RESPONSE_HEADERS = ['content-type', 'content-disposition', 'content-range', 'accept-ranges', 'cache-control', 'x-sample-rate', 'x-jarvis-tts-engine', 'x-jarvis-tts-fallback', 'x-jarvis-voice'] as const;
