import type { Handle } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { hudWebOriginsAttr } from '$lib/server/hudWebOrigins';
import {
	SESSION_COOKIE,
	SESSION_TTL_MS,
	admit,
	bearerAdmits,
	openSession,
	secureCookie,
	signedInByCookieHeader
} from '$lib/server/consoleAuth';
import { deviceFor } from '$lib/server/deviceKeys';
import { THEME_COOKIE } from '$lib/ds/pack';
import { building } from '$app/environment';
import { themeStamp } from '$lib/server/themeStamp';

// The socket relays in `server/*.js` are copied into build/ beside this bundle and
// cannot import it, so the sign-in check reaches them through a global. Unset (the
// relays running without this hook), they refuse every unkeyed socket.
(globalThis as { __jarvisSignedIn?: (cookie: string | undefined) => Promise<boolean> }).__jarvisSignedIn =
	(cookie) => signedInByCookieHeader(env, cookie);

// Session id: random, httpOnly (never readable by page JS, never in
// localStorage). Identifies the browser session to the server only.
export const handle: Handle = async ({ event, resolve }) => {
	// T-0387: the relays and the API act as the owner, and the LAN can reach this
	// port. `admit` says who may (see the gate in consoleAuth.ts).
	const authorization = event.request.headers.get('authorization');
	const verdict = await admit(env, {
		path: event.url.pathname,
		session: event.cookies.get(SESSION_COOKIE),
		authorization,
		accept: event.request.headers.get('accept')
	});
	if (verdict === 'refuse') {
		return new Response(JSON.stringify({ ok: false, error: 'sign in to the console first' }), {
			status: 401,
			headers: { 'content-type': 'application/json' }
		});
	}
	if (verdict === 'sign-in') {
		// The phone's and the car's web view: the page load carried the adapter key,
		// so its own fetches and sockets (which carry none) ride this session.
		// A device's own key tags the session with the device, so revoking it ends this too
		// (T-0569); the lookup is the one `admit` just made, remembered.
		const device = bearerAdmits(env, authorization) ? null : await deviceFor(env, authorization);
		event.cookies.set(SESSION_COOKIE, openSession(Date.now(), device ?? undefined, true), {
			path: '/',
			httpOnly: true,
			sameSite: 'strict',
			secure: secureCookie(event.request.headers),
			maxAge: Math.floor(SESSION_TTL_MS / 1000)
		});
	}
	if (!event.cookies.get('jarvis_sid')) {
		event.cookies.set('jarvis_sid', crypto.randomUUID(), {
			path: '/',
			httpOnly: true,
			sameSite: 'lax',
			secure: secureCookie(event.request.headers),
			maxAge: 60 * 60 * 24 * 30
		});
	}
	// The web-kind panel's frame allow-list travels as a meta tag stamped server-side:
	// this app's CSP is `script-src: 'self'` with no unsafe-inline, so an inline script
	// could not carry it, and the client must not guess which origins it may frame.
	const hudWebOrigins = hudWebOriginsAttr(env);
	// The worn pack's look, from the cookie `setTheme` keeps: on the first byte, so a cold load
	// never paints the default pack and swaps (T-0879).
	// Not while prerendering: the offline page is one file for everyone (and the phone's
	// bundled copy of it), so it wears no one's cookie and no stamp.
	const worn = building ? '' : themeStamp(event.cookies.get(THEME_COOKIE));
	return resolve(event, {
		transformPageChunk: ({ html }) =>
			html
				.replace('%jarvis.theme%', worn)
				.replace('%jarvis.hud_web_origins%', hudWebOrigins)
	});
};
