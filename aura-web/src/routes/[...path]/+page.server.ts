// The one console route, server side: a path the shell does not know is a 404
// with the console's error page and the status to match.
//
// The page renders on the server like any other (the shell draws its root and
// the core; the links are null until the browser dials them), which is what
// lets this 404 carry a real status. With `ssr` off it would be an error page
// painted over a 200 shell, fine to a person and invisible to a link checker.

import { error } from '@sveltejs/kit';
import { knownPath } from '$lib/ds/shell/routes';

export function load({ url }: { url: URL }) {
	if (!knownPath(url.pathname)) error(404, `There is no screen at ${url.pathname}.`);
	return {};
}
