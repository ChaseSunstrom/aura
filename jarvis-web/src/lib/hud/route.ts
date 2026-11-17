/**
 * A drive's route on Jarvis's own map, and when to say each turn (the owner,
 * 2026-09-25: "I ask it to route me and it doesn't update the map on the car HUD and
 * gets stuck on a white screen in my map app", and "can Jarvis say navigation's for
 * turns and stuff for maps out loud, if enabled ... so by the time it speaks I'm
 * actually at the correct distance, because theres a delay").
 *
 * The route is in OSRM's shape, from the house's router (`/api/route`, the console's
 * proxy). A turn is said twice: once early ("In 400 metres, turn left onto Main
 * Street") and once at the turn ("Turn left onto Main Street"). Each is said when
 * the car will reach the turn in the wanted number of seconds PLUS the time the
 * words take to reach a driver -- the phone's own voice, not the model, so there is
 * no thinking in it -- measured from the car's speed, with a distance floor for a
 * crawl and a stop.
 *
 * Pure: the car page feeds it fixes; `route.test.ts` pins the timing.
 */

export interface Step {
	lat: number;
	lon: number;
	/** What to say at the turn: "Turn left onto Main Street". */
	text: string;
	/** The maneuver's kind, for the banner's arrow: "turn", "arrive", ... */
	type: string;
	modifier: string;
	/** The road after the maneuver ("" when the map has none): the phone's ground labels it. */
	name: string;
}

export interface Route {
	/** The path, [lat, lon] pairs, for the map. */
	points: [number, number][];
	steps: Step[];
	/** Metres and seconds, the whole way. */
	distance: number;
	duration: number;
}

/** Seconds before a turn its early word is said, and its at-the-turn word. */
export const EARLY_S = 30;
export const AT_S = 7;
/** What the words cost to reach a driver: the phone's voice starting, and a short line said. */
export const SPEECH_LEAD_S = 2.5;
/** Never nearer than these, for a crawl or a stop. */
export const EARLY_MIN_M = 250;
export const AT_MIN_M = 40;
/** Further than this off the path, for a while, is off the route: routed again. */
export const OFF_ROUTE_M = 80;

const R = 6_371_000;

export function metres(aLat: number, aLon: number, bLat: number, bLon: number): number {
	const p1 = (aLat * Math.PI) / 180;
	const p2 = (bLat * Math.PI) / 180;
	const dp = p2 - p1;
	const dl = ((bLon - aLon) * Math.PI) / 180;
	const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
	return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

const TURN_WORDS: Record<string, string> = {
	left: 'turn left',
	right: 'turn right',
	'slight left': 'bear left',
	'slight right': 'bear right',
	'sharp left': 'turn sharp left',
	'sharp right': 'turn sharp right',
	uturn: 'make a U-turn',
	straight: 'continue straight'
};

/** One maneuver in words, the way a driver hears it. */
export function sayStep(type: string, modifier: string, name: string, exit?: number): string {
	const onto = name ? ` onto ${name}` : '';
	switch (type) {
		case 'arrive':
			return 'You have arrived';
		case 'stop':
			return 'You have reached your stop';
		case 'depart':
			return `Head out${onto}`;
		case 'roundabout':
		case 'rotary':
			return exit ? `At the roundabout, take exit ${exit}${onto}` : `Enter the roundabout${onto}`;
		case 'merge':
			return `Merge${onto}`;
		case 'on ramp':
			return `Take the ramp${onto}`;
		case 'off ramp':
			return `Take the exit${onto}`;
		case 'fork':
			return `Keep ${modifier.includes('left') ? 'left' : 'right'} at the fork${onto}`;
		default: {
			const words = TURN_WORDS[modifier] ?? 'continue';
			return `${words[0].toUpperCase()}${words.slice(1)}${onto}`;
		}
	}
}

/** OSRM's `route` answer as a Route; null when it has no route. */
export function readRoute(body: unknown): Route | null {
	const o = body as { routes?: { geometry?: { coordinates?: [number, number][] }; legs?: { steps?: unknown[] }[]; distance?: number; duration?: number }[] };
	const r = o?.routes?.[0];
	const coords = r?.geometry?.coordinates;
	if (!r || !Array.isArray(coords) || coords.length < 2) return null;
	const steps: Step[] = [];
	const legs = r.legs ?? [];
	for (const [li, leg] of legs.entries()) {
		for (const raw of leg.steps ?? []) {
			const s = raw as { maneuver?: { location?: [number, number]; type?: string; modifier?: string; exit?: number }; name?: string };
			const m = s.maneuver;
			if (!m?.location) continue;
			let type = String(m.type ?? 'turn');
			if (type === 'depart') continue;
			// Each leg but the last ends at a stop on the way, not the destination: it is
			// said, and the route is not finished there.
			if (type === 'arrive' && li < legs.length - 1) type = 'stop';
			steps.push({ lon: m.location[0], lat: m.location[1], type, modifier: String(m.modifier ?? ''), name: String(s.name ?? ''), text: sayStep(type, String(m.modifier ?? ''), String(s.name ?? ''), m.exit) });
		}
	}
	return { points: coords.map(([lon, lat]) => [lat, lon]), steps, distance: Number(r.distance ?? 0), duration: Number(r.duration ?? 0) };
}

/** How far the car is from the route's path, roughly: the nearest of its points. */
export function offRoute(route: Route, lat: number, lon: number): number {
	let best = Infinity;
	for (const [plat, plon] of route.points) best = Math.min(best, metres(lat, lon, plat, plon));
	return best;
}

/** The next turn the car has not yet passed: the first step it is still short of. */
export function nextStep(route: Route, lat: number, lon: number, passed: number): { index: number; metres: number } | null {
	for (let i = passed; i < route.steps.length; i++) {
		const d = metres(lat, lon, route.steps[i].lat, route.steps[i].lon);
		if (d > AT_MIN_M / 2 || i === route.steps.length - 1) return { index: i, metres: d };
	}
	return null;
}

/** "400 metres", "1.2 kilometres": how a distance is said. */
export function sayDistance(m: number): string {
	if (m >= 1000) return `${(Math.round(m / 100) / 10).toString()} kilometres`;
	const rounded = m >= 200 ? Math.round(m / 50) * 50 : Math.round(m / 10) * 10;
	return `${rounded} metres`;
}

/**
 * What to say now about the step `index`, `m` metres ahead at `speed` m/s, given
 * what has been said already ("early:3", "at:3"); null for nothing. The distance at
 * which a word is due is the speed times its seconds plus the speech lead.
 */
export function announcement(route: Route, index: number, m: number, speed: number, said: ReadonlySet<string>): { key: string; text: string } | null {
	const step = route.steps[index];
	if (!step) return null;
	const v = Math.max(0, speed);
	const atDue = Math.max(AT_MIN_M, v * (AT_S + SPEECH_LEAD_S));
	const earlyDue = Math.max(EARLY_MIN_M, v * (EARLY_S + SPEECH_LEAD_S));
	if (m <= atDue && !said.has(`at:${index}`)) return { key: `at:${index}`, text: step.text };
	// The early word only when there is time for both, and not for an arrival.
	if (m <= earlyDue && m > atDue * 1.5 && step.type !== 'arrive' && !said.has(`early:${index}`) && !said.has(`at:${index}`)) {
		return { key: `early:${index}`, text: `In ${sayDistance(m)}, ${step.text[0].toLowerCase()}${step.text.slice(1)}` };
	}
	return null;
}

/** Nearer than this to the corner, a word is said over an open turn: later it would be no word. */
export const HOLD_FLOOR_M = 100;

/**
 * `announcement`, held while a voice turn is open on the car (the microphone, Jarvis thinking
 * or answering: the phase the phone reports, T-1080) so a turn is not spoken over the driver.
 * Nothing is queued and nothing is marked said: the caller asks again at each fix and when the
 * turn ends, so the one line that is pending is worked out from where the car is then. Still
 * ahead, it is said (worded for the new distance); passed, `index` has moved on and the old
 * corner has no word. Within [HOLD_FLOOR_M] it is said anyway.
 *
 * Not covered: tried on a car; the head unit's own audio focus (it may duck or cut the guidance
 * channel itself); a turn that stays open past the last 100 m of every corner is simply not told.
 */
export function turnWord(route: Route, index: number, m: number, speed: number, said: ReadonlySet<string>, turnOpen: boolean): { key: string; text: string } | null {
	if (turnOpen && m > HOLD_FLOOR_M) return null;
	return announcement(route, index, m, speed, said);
}

/** A route request with no answer in this long was lost on the way: the house's router answers in well under a second. */
/** A request the house refused as wrong, with its reason for the driver (T-1214). */
export type Refused = { refused: string };

/**
 * One ask for a route: the house's answer when it gives one, and [packed] (the phone's own
 * map packs, T-1075) when the house is lost or answers an error. The house's "no route" (404)
 * is believed and the phone is not asked. `'lost'` when neither answered, for `findRoute`.
 *
 * A request the house called wrong (a 4xx the relay passes on with its reason, T-1083: a bad
 * via, 400) is wrong for the pack too, so it is `Refused`, not routed. 408 and 429 are the link
 * or the load, not the request, and keep the pack; so do the relay's own 5xx. Does not cover a
 * request the house refuses that the pack could have routed (a place outside the house's region).
 */
export async function houseOrPack(house: () => Promise<Response>, packed: () => Promise<Route | null>): Promise<Route | Refused | null | 'lost'> {
	let res: Response | null = null;
	try {
		res = await house();
		if (res.ok) return readRoute(await res.json());
	} catch {
		res = null;
	}
	if (res?.status === 404) return null;
	if (res && res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429) {
		const message = await res.json().then((b) => b?.message, () => null);
		return { refused: typeof message === 'string' && message ? message.slice(0, 200) : `the house refused the route (${res.status})` };
	}
	return (await packed()) ?? (res ? null : 'lost');
}

export const ROUTE_TIMEOUT_MS = 8_000;
/** How often a lost request is asked again, and how long between. */
export const ROUTE_TRIES = 5;
export const ROUTE_RETRY_MS = 3_000;

/**
 * A route, asked for until it is answered: `ask` gives the route, null when the house said
 * there is none (believed at once) or refused it (the same), or 'lost' when no answer came, which is asked again up to
 * `ROUTE_TRIES` times while `wanted` holds. One request with no timeout and no second try left
 * the car without a route for minutes on a slow link (the owner's drive, 2026-11-11).
 * Does not cover a link dead for longer than the tries: null, as for no route.
 */
export async function findRoute(
	ask: () => Promise<Route | Refused | null | 'lost'>,
	wanted: () => boolean,
	sleep: (ms: number) => Promise<void> = (ms) => new Promise((done) => setTimeout(done, ms))
): Promise<Route | Refused | null> {
	for (let tries = 1; ; tries++) {
		const got = await ask();
		if (got !== 'lost') return got;
		if (tries >= ROUTE_TRIES || !wanted()) return null;
		await sleep(ROUTE_RETRY_MS);
		if (!wanted()) return null;
	}
}

/** How many turns ahead the phone is told the words of. */
export const PREPARE_AHEAD = 3;

/**
 * The words of the turns from step `from` on, as `announcement` says them at the turn, for
 * the phone to voice before they are due: voiced when asked for, on a slow link, a turn was
 * said after the corner (the owner's drive, 2026-11-11). Does not cover the early word ("In
 * 400 metres, ..."): its distance is only known when it is due.
 */
export function coming(route: Route, from: number, count = PREPARE_AHEAD): string[] {
	return route.steps.slice(Math.max(0, from), Math.max(0, from) + count).map((step) => step.text).filter(Boolean);
}

/** A point on the way, as `car_navigate`'s `via` sends it. */
export interface Stop {
	lat: number;
	lon: number;
	label: string;
}
