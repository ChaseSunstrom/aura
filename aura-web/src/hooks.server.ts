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

/**
 * The User-Agent `ManagementActivity` gives its WebView.
 *
 * Kept in step with the Kotlin by `console_parity_test.py`, which reads both:
 * change the agent and nothing here fails, the duplicate nav just comes back.
 */
const ANDROID_FRAME = /JarvisAndroid/;

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
	// Tell the page whether the Android app's console frame is around it, so it
	// can stop drawing the chrome that frame already draws — see the
	// `data-embed` rules in `src/routes/+layout.svelte`.
	//
	// Server-side, from the request header, rather than an inline script
	// sniffing `navigator.userAgent`: this app's CSP is `script-src: 'self'`
	// with no unsafe-inline, so such a script is BLOCKED — silently, since a
	// refused inline script leaves the page working and only the marker
	// missing. Doing it here also means the attribute is on the very first
	// byte, so the duplicate nav is never painted and then yanked.
	const embedded = ANDROID_FRAME.test(event.request.headers.get('user-agent') ?? '');
	// The web-kind panel's frame allow-list travels the same way (a meta tag stamped
	// server-side): the CSP forbids an inline script, and the client must not guess
	// which origins it may frame.
	const hudWebOrigins = hudWebOriginsAttr(env);
	// The worn pack's look, from the cookie `setTheme` keeps: on the first byte, so a cold load
	// never paints the default pack and swaps (T-0879).
	// Not while prerendering: the offline page is one file for everyone (and the phone's
	// bundled copy of it), so it wears no one's cookie and no stamp.
	const worn = building ? '' : themeStamp(event.cookies.get(THEME_COOKIE));
	return resolve(event, {
		transformPageChunk: ({ html }) =>
			html
				.replace('%jarvis.embed%', embedded ? 'android' : '')
				.replace('%jarvis.theme%', worn)
				.replace('%jarvis.hud_web_origins%', hudWebOrigins)
	});
};
