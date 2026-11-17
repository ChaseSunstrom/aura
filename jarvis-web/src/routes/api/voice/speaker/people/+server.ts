import { relaySpeakerWrite } from '$lib/server/speakerRelay';
import { SESSION_COOKIE, sessionValid } from '$lib/server/consoleAuth';
import type { RequestHandler } from './$types';

// Name a person and set their level: JSON `{label, level}` to jarvis-core's
// `POST /api/voice/speaker/people` (Settings → People, and the first-run setup
// naming the owner).
//
// The same rule as enrolling, for the same reason: a person's level is what
// the agent's approval matrix reads once their voice is recognised, so
// writing one is authorised by something the caller HOLDS -- the phone's own
// token, relayed unchanged, or the console password's session -- and never by
// reaching this port. `relaySpeakerWrite` is the one place that rule is kept.
export const POST: RequestHandler = async ({ request, cookies }) =>
	relaySpeakerWrite(
		request,
		globalThis.fetch,
		'people',
		sessionValid(cookies.get(SESSION_COOKIE))
	);
