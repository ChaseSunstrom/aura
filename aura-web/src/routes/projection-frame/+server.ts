// The page a custom_html projection runs in (`$lib/ds/gen/CustomHtml.svelte`).
//
// The HTML is model-written, so it gets a document of its own with a policy of
// its own: `default-src 'none'` with inline script and style only, so nothing
// it writes can fetch, open a socket, load an image from the network or post a
// form. A `srcdoc` frame would inherit the console's CSP instead, which allows
// same-origin fetches and sockets, which is why this is a route. The frame
// that loads it is `sandbox="allow-scripts"` without `allow-same-origin`, and
// the `sandbox` directive below makes the same true when the URL is opened on
// its own: an opaque origin, no cookies, no storage, no `parent.document`.
//
// The bootstrap takes one message from its parent, the HTML, and writes it in
// place; `document.open` keeps the document and so keeps this policy. It then
// gives the document the console's dark scheme (a meta, so the HTML's own
// `color-scheme` still wins): a frame whose scheme differs from its host's is
// painted opaque white, and default text would be black on the HUD.
//
// Does not cover: CPU. A `while (true) {}` holds the thread the frame shares
// with the console (no site isolation for a same-site frame); the parent's
// time cap cannot fire until it lets go.

import type { RequestHandler } from './$types';

const BOOTSTRAP =
	'<!doctype html><html><head><meta charset="utf-8"><script>' +
	"addEventListener('message', function once(e) {" +
	'if (e.source !== parent) return;' +
	"removeEventListener('message', once);" +
	'document.open(); document.write(String(e.data)); document.close();' +
	"var m = document.createElement('meta'); m.name = 'color-scheme'; m.content = 'dark';" +
	'document.head.prepend(m);' +
	'});' +
	'</script></head><body></body></html>';

const CSP = [
	"default-src 'none'",
	"script-src 'unsafe-inline'",
	"style-src 'unsafe-inline'",
	'img-src data:',
	"base-uri 'none'",
	"form-action 'none'",
	"frame-ancestors 'self'",
	'sandbox allow-scripts'
].join('; ');

export const GET: RequestHandler = () =>
	new Response(BOOTSTRAP, {
		headers: {
			'content-type': 'text/html; charset=utf-8',
			'content-security-policy': CSP,
			'cache-control': 'no-store',
			'x-content-type-options': 'nosniff',
			'referrer-policy': 'no-referrer'
		}
	});
