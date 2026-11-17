import { error, redirect } from '@sveltejs/kit';
import { STORE_URL, storedApk } from '$lib/hud/data/apps';
import type { RequestHandler } from './$types';

// One address that always downloads the newest phone build in the house's store.
//
// The listing is asked for through the console's own relay with the caller's session
// (`event.fetch` carries the cookie), so no key is handled here and a signed-out caller
// gets the relay's 401, not a link. The link it redirects to is the listing's signed URL,
// good for an hour: the address is stable, the link behind it is not.
export const GET: RequestHandler = async ({ fetch }) => {
	// A house that is restarting is said as that, not as a bare 500.
	// A 30x from the relay is refused, as on every proxying route: it could carry the session elsewhere.
	const res = await fetch(STORE_URL, { headers: { Accept: 'application/json' }, redirect: 'error' }).catch(() => null);
	if (!res) throw error(502, 'the house did not answer');
	if (!res.ok) throw error(res.status >= 400 ? res.status : 502, 'the house did not list its builds');
	const build = storedApk(await res.json().catch(() => null));
	if (!build) throw error(404, 'no phone build is in the house\'s store yet');
	throw redirect(302, build.files[0].url);
};
