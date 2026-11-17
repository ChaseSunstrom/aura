/**
 * The car's own panels, fed by the phone (`/car`, the owner 2026-09-25: the
 * console's HUD on the head unit, "the reactor as the main thing", with maps,
 * phone and messages as panels).
 *
 * The console runs on the car display inside the phone app's web view, and
 * the phone is what knows what is playing, who is calling, what arrived and
 * where the car is. It hands that over through a bridge the app installs:
 * `window.JarvisCar.state()` answers the whole state as JSON at load, a
 * `jarvis:car` event carries each change, and `window.JarvisCar.act(json)`
 * is how a panel asks the phone to do something (play, answer, navigate).
 * Outside the car (a browser at `/car`) there is no bridge: the panels say so.
 *
 * Pure parsing below, so a malformed or partial state from the phone is read
 * as what it is rather than thrown on.
 */

import type { ReactorStateName } from '$lib/reactor';
import { phoneResultArg } from './phone';
import { sayStep, type Route, type Stop } from './route';

export interface Media {
	title: string;
	artist: string;
	app: string;
	playing: boolean;
	/** Seconds; 0 when unknown. */
	position: number;
	duration: number;
}

export interface Call {
	/** "ringing", "active" or "held". */
	state: string;
	name: string;
	number: string;
	/** A small picture of the caller, a base64 image data URL, or '' (T-0760). */
	photo: string;
	/** When an active call began (ms), or null while it rings or when the phone does not say. */
	since: number | null;
}

export interface Message {
	id: string;
	from: string;
	app: string;
	text: string;
	/** Epoch milliseconds. */
	at: number;
}

export interface Place {
	lat: number;
	lon: number;
	/** Degrees, 0 = north; null when the car is not moving. */
	heading: number | null;
	/** Metres per second. */
	speed: number;
}

/**
 * The car's own voice turn (the steering wheel, or the HUD's mic in the car):
 * recorded from the car's microphone and answered on the car's speakers by the
 * phone, so the page only draws it -- the reactor walks the same states.
 */
export interface CarVoice {
	/** "idle", "listening", "thinking", "speaking" or "error". */
	phase: string;
	heard: string;
	answer: string;
	caption: string;
}

export interface CarState {
	/** True when the phone's bridge is present: this is the car. */
	linked: boolean;
	media: Media | null;
	call: Call | null;
	messages: Message[];
	place: Place | null;
	/**
	 * The car's own speedometer, m/s (the AAP sensor channel), or null when the car has
	 * not said. The Speed panel reads this before the phone's GPS: a fix under a locked
	 * screen, or one without a speed, left it on "No speed yet" (the owner, 2026-09-26).
	 */
	carSpeed: number | null;
	/** The navigation apps installed on the phone, in the order it offers them. */
	navApps: string[];
	voice: CarVoice | null;
	/** The newest notification, for the console's own notice card on the car screen. */
	notice: CarNotice | null;
	/** The music's level, when the reactor moves with it (the phone's setting). */
	music: CarMusic | null;
	/** An approval a car turn is waiting on: a small card, answered by a tap or aloud. */
	ask: CarAsk | null;
	/** Where Jarvis was asked to go (`car_navigate`): the page routes it, or hands it to an app. */
	destination: CarDestination | null;
	/**
	 * What the phone may see (`tests/contracts/car-state.json`). Without notification access the
	 * media, messages and notice are empty because the phone cannot read them, and a panel that
	 * said "Nothing playing" over music was wrong (T-0326).
	 */
	access: CarAccess;
	/** The driver's allowed (or pinned) phone apps, to open on the car (`act({kind: 'app', pkg})`). */
	apps: CarApp[];
}

export interface CarAccess {
	notifications: boolean;
	/** Screen sharing: an app opened on the car has a picture only with it. */
	capture: boolean;
	/**
	 * The phone draws the hologram drive under the page (T-0741): the page asks for it, draws
	 * no map of its own and hands it the route. False from an older phone: the page's tiles.
	 */
	ground: boolean;
}

export interface CarApp {
	pkg: string;
	label: string;
}

export interface CarDestination {
	lat: number;
	lon: number;
	label: string;
	/** The app Jarvis named, or empty for the owner's choice (`hud.car_navigation`). */
	app: string;
	/** When it was asked, so the same place asked twice is routed twice. */
	at: number;
	/** Stops on the way, in order; only Jarvis's own map routes through them. */
	via: Stop[];
}

export interface CarAsk {
	id: string;
	title: string;
	detail: string;
}

export interface CarNotice {
	id: string;
	app: string;
	title: string;
	text: string;
	at: number;
}

export interface CarMusic {
	pulse: boolean;
	/** 0..1. */
	level: number;
}

/** The page puts the photo in an `img`: only a raster image carried in the state itself, so a call can make the car fetch nothing. */
const PHOTO = /^data:image\/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/;
const PHOTO_MAX = 200_000;
const photoOf = (v: unknown): string => (typeof v === 'string' && v.length <= PHOTO_MAX && PHOTO.test(v) ? v : '');

/** How long a call has run, `m:ss` (`h:mm:ss` past an hour); '' when the phone gave no start. */
export function callTime(since: number | null, now: number): string {
	if (since === null) return '';
	const s = Math.max(0, Math.floor((now - since) / 1000));
	const two = (n: number) => String(n).padStart(2, '0');
	const m = Math.floor(s / 60) % 60;
	return s >= 3600 ? `${Math.floor(s / 3600)}:${two(m)}:${two(s % 60)}` : `${m}:${two(s % 60)}`;
}

/** What stands in for a caller's picture: the first letters of the first and last name, `#` for a number. */
export function initials(name: string): string {
	const words = name.trim().split(/\s+/).filter((w) => /^\p{L}/u.test(w));
	if (!words.length) return '#';
	const ends = words.length > 1 ? [words[0], words[words.length - 1]] : [words[0]];
	return ends.map((w) => w[0].toUpperCase()).join('');
}

export const NO_CAR: CarState = { linked: false, media: null, call: null, messages: [], place: null, carSpeed: null, navApps: [], voice: null, notice: null, music: null, ask: null, destination: null, access: { notifications: true, capture: true, ground: false }, apps: [] };

const str = (v: unknown): string => (typeof v === 'string' ? v : v == null ? '' : String(v));
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

/** The phone's JSON, read field by field; anything missing is absent, not an error. */
export function readCarState(raw: unknown, linked = true): CarState {
	const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
	const m = o.media as Record<string, unknown> | null | undefined;
	const c = o.call as Record<string, unknown> | null | undefined;
	const p = o.place as Record<string, unknown> | null | undefined;
	const v = o.voice as Record<string, unknown> | null | undefined;
	const n = o.notice as Record<string, unknown> | null | undefined;
	const mu = o.music as Record<string, unknown> | null | undefined;
	const q = o.ask as Record<string, unknown> | null | undefined;
	const dst = o.destination as Record<string, unknown> | null | undefined;
	const acc = (o.access && typeof o.access === 'object' ? o.access : {}) as Record<string, unknown>;
	return {
		linked,
		media:
			m && typeof m === 'object' && str(m.title)
				? { title: str(m.title), artist: str(m.artist), app: str(m.app), playing: m.playing === true, position: num(m.position), duration: num(m.duration) }
				: null,
		call: c && typeof c === 'object' && str(c.state) ? { state: str(c.state), name: str(c.name), number: str(c.number), photo: photoOf(c.photo), since: typeof c.since === 'number' && c.since > 0 ? c.since : null } : null,
		messages: Array.isArray(o.messages)
			? o.messages
					.filter((x): x is Record<string, unknown> => !!x && typeof x === 'object')
					.map((x) => ({ id: str(x.id), from: str(x.from), app: str(x.app), text: str(x.text), at: num(x.at) }))
					.filter((x) => x.id && x.text)
			: [],
		place:
			p && typeof p === 'object' && typeof p.lat === 'number' && typeof p.lon === 'number'
				? { lat: p.lat, lon: p.lon, heading: typeof p.heading === 'number' ? p.heading : null, speed: num(p.speed) }
				: null,
		carSpeed: typeof o.carSpeed === 'number' && Number.isFinite(o.carSpeed) && o.carSpeed >= 0 ? o.carSpeed : null,
		navApps: Array.isArray(o.navApps) ? o.navApps.map(str).filter(Boolean) : [],
		voice: v && typeof v === 'object' && str(v.phase)
			? { phase: str(v.phase), heard: str(v.heard), answer: str(v.answer), caption: str(v.caption) }
			: null,
		notice: n && typeof n === 'object' && str(n.id) && (str(n.title) || str(n.text))
			? { id: str(n.id), app: str(n.app), title: str(n.title), text: str(n.text), at: num(n.at) }
			: null,
		music: mu && typeof mu === 'object' ? { pulse: mu.pulse === true, level: Math.max(0, Math.min(1, num(mu.level))) } : null,
		ask: q && typeof q === 'object' && str(q.id) && str(q.title) ? { id: str(q.id), title: str(q.title), detail: str(q.detail) } : null,
		destination:
			dst && typeof dst === 'object' && typeof dst.lat === 'number' && typeof dst.lon === 'number'
				? { lat: dst.lat, lon: dst.lon, label: str(dst.label), app: str(dst.app), at: num(dst.at), via: [] }
				: null,
		// Only an explicit `false` is a missing grant: an older phone that never says can see.
		// The ground is the other way round: only an explicit `true`, since an older phone draws none.
		access: { notifications: acc.notifications !== false, capture: acc.capture !== false, ground: acc.ground === true },
		apps: Array.isArray(o.apps)
			? o.apps
					.filter((x): x is Record<string, unknown> => !!x && typeof x === 'object' && !!str(x.pkg))
					.map((x) => ({ pkg: str(x.pkg), label: str(x.label) || str(x.pkg) }))
			: []
	};
}

/**
 * A `hud.car.navigate` this page received itself. The car page holds its own Hermes
 * session (its voice turns run there), so `car_navigate` from a car turn reaches this
 * page, not the phone's home screen, and was dropped: "on the way" said, no path drawn
 * (2026-10-08). Missing coordinates are no destination.
 */
export function readDestination(payload: unknown, at: number): CarDestination | null {
	// `ev`, not `o`: this is Hermes' hud.car.navigate payload, not the phone's JSON,
	// and the bridge check reads every `o.` key here as one the phone must write.
	const ev = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {};
	const lat = Number(ev.lat);
	const lon = Number(ev.lon);
	if (!Number.isFinite(lat) || !Number.isFinite(lon) || (lat === 0 && lon === 0)) return null;
	const via = Array.isArray(ev.via)
		? ev.via
				.map((x) => (x && typeof x === 'object' ? (x as Record<string, unknown>) : {}))
				.map((x) => ({ lat: Number(x.lat), lon: Number(x.lon), label: str(x.label) }))
				.filter((x) => Number.isFinite(x.lat) && Number.isFinite(x.lon))
				.slice(0, 5)
		: [];
	return { lat, lon, label: str(ev.label), app: str(ev.app), at, via };
}

/**
 * The answer a waiting `car_navigate` gets when this page took its route: the car draws it on
 * its own map. Without one Jarvis cannot tell a route that opened from one that did not
 * (the owner, 2026-11-09: "it doesn't show anything", after "your phone has it open").
 */
export function tookRoute(sessionId: string, payload: unknown): string | null {
	const id = payload && typeof payload === 'object' ? (payload as Record<string, unknown>).id : undefined;
	return typeof id === 'string' ? phoneResultArg(sessionId, id, { status: 'ok', opened: 'car' }) : null;
}

/** The later of two destinations: the phone's and the page's own. */
export function newestDestination(a: CarDestination | null, b: CarDestination | null): CarDestination | null {
	if (!a) return b;
	if (!b) return a;
	// The same request seen twice -- the phone forwards it without its stops, the page
	// has them -- is the one with the stops, whichever clock said later.
	if (a.lat === b.lat && a.lon === b.lon && Math.abs(a.at - b.at) < SAME_REQUEST_MS && a.via.length !== b.via.length) {
		return a.via.length > b.via.length ? a : b;
	}
	return b.at > a.at ? b : a;
}

/** Two sightings of one `car_navigate` this close together are one request. */
const SAME_REQUEST_MS = 60_000;

/** What a panel asks the phone to do. */
export type CarAct =
	| { kind: 'media'; op: 'play' | 'pause' | 'next' | 'previous' }
	| { kind: 'call'; op: 'answer' | 'hangup' }
	| { kind: 'reply'; id: string; text: string }
	| { kind: 'navigate'; lat: number; lon: number; label: string; app: string }
	/** Start a turn on the car's microphone, as the steering-wheel button does. */
	| { kind: 'talk' }
	/** The answer to a waiting approval (`ask`). */
	| { kind: 'answer'; id: string; ok: boolean }
	/** A line said on the car's own voice at once: a turn, with no model in it. */
	| { kind: 'say'; text: string }
	/** Lines that will be said soon (the coming turns): voiced now and kept, so saying one needs no link. */
	| { kind: 'prepare'; texts: string[] }
	/** Which voice says a reply in the car (the house's `hud.car_voice`). */
	| { kind: 'voice'; mode: 'auto' | 'house' | 'phone' }
	/** The route is over: arrived, or stopped. */
	| { kind: 'arrived' }
	/** Open one of the driver's apps on the car (its app pane). */
	| { kind: 'app'; pkg: string }
	/** Open the phone's own setting for a missing grant. */
	| { kind: 'access'; op: 'notifications' }
	/** Show the phone's hologram ground under the page, or stop it (`access.ground`). */
	| { kind: 'ground'; on: boolean }
	/** The route for the phone's ground (`groundRoute`); no points once there is none. */
	| { kind: 'route'; points: [number, number][]; street?: string; turn?: { lat: number; lon: number; street: string } | null }
	/** A route over the phone's map packs, for when the house's does not answer; the answer is a `jarvis:car-route` event with this id. */
	| { kind: 'packroute'; id: number; from: [number, number]; to: [number, number] };

/**
 * The `route` act for the phone's ground (T-0741): the path, the road the car is on (the one
 * after the last maneuver passed) and the next maneuver at [ahead]. `CarHud.routeOf` reads it.
 */
export function groundRoute(route: Route | null, ahead: number): CarAct {
	if (!route) return { kind: 'route', points: [] };
	const turn = route.steps[ahead];
	return {
		kind: 'route',
		points: route.points,
		street: route.steps[ahead - 1]?.name ?? '',
		turn: turn ? { lat: turn.lat, lon: turn.lon, street: turn.name } : null
	};
}

const REACTOR_OF: Record<string, ReactorStateName> = {
	listening: 'listening',
	thinking: 'thinking',
	speaking: 'speaking',
	error: 'alert'
};

/** The reactor state a car turn shows, or null when it is idle and the page's own links decide. */
export function reactorOfCar(voice: CarVoice | null): ReactorStateName | null {
	return voice ? (REACTOR_OF[voice.phase] ?? null) : null;
}

interface Bridge {
	state(): string;
	act(json: string): void;
}

function bridge(): Bridge | null {
	const b = (globalThis as { JarvisCar?: Bridge }).JarvisCar;
	return b && typeof b.state === 'function' && typeof b.act === 'function' ? b : null;
}

/**
 * A line JARVIS says on purpose (`hud.say`), handed to the phone's car voice: the car's guidance
 * channel, which needs no screen share. Played by this page's own audio it reached the car only
 * through playback capture, the screen-share grant (T-0743). False when there is no phone to
 * say it, and the page plays it itself.
 */
export function sayInCar(text: string): boolean {
	const b = bridge();
	if (!b) return false;
	b.act(JSON.stringify({ kind: 'say', text } satisfies CarAct));
	return true;
}

/** The house's `hud.car_voice` as the act the phone reads; a value it would drop is `auto`. */
export function voiceAct(value: unknown): CarAct {
	const mode = value === 'house' || value === 'phone' ? value : 'auto';
	return { kind: 'voice', mode };
}

/** The phone's router is given three seconds (PackRouter's budget); past this it is not answering. */
export const PACK_ROUTE_MS = 5_000;

// Not from 1: an answer to the page before a reload must not match this page's first ask. Within the phone's int.
let packAsks = Date.now() % 1_000_000_000;

/**
 * The phone's answer to a `packroute` act as a Route (`packRoute` in the car-state contract),
 * each turn worded here; null when it is another ask's, says no route, or does not read.
 */
export function readPackRoute(detail: unknown, id: number): Route | null {
	const o = detail as { id?: unknown; route?: { points?: unknown; steps?: unknown; distance?: unknown; duration?: unknown } | null } | null;
	const r = o?.route;
	if (o?.id !== id || !r || !Array.isArray(r.points) || r.points.length < 2) return null;
	const points = r.points as unknown[];
	if (!points.every((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1]))) return null;
	const steps = (Array.isArray(r.steps) ? r.steps : []).flatMap((raw) => {
		const s = raw as { lat?: unknown; lon?: unknown; type?: unknown; modifier?: unknown; name?: unknown };
		if (!Number.isFinite(s?.lat) || !Number.isFinite(s?.lon)) return [];
		const [type, modifier, name] = [String(s.type ?? 'turn'), String(s.modifier ?? ''), String(s.name ?? '')];
		return [{ lat: s.lat as number, lon: s.lon as number, type, modifier, name, text: sayStep(type, modifier, name) }];
	});
	return { points: points as [number, number][], steps, distance: Number(r.distance) || 0, duration: Number(r.duration) || 0 };
}

/**
 * A route over the phone's map packs (T-1075), for when the house's router does not answer:
 * the phone holds the roads the car is on. Null with no phone, no route in its packs, or no
 * answer in [timeoutMs] (a phone older than the act never answers).
 *
 * Not covered: stops on the way; the phone routes from one place to one other.
 */
export function askPackRoute(from: [number, number], to: [number, number], timeoutMs = PACK_ROUTE_MS, target: Pick<EventTarget, 'addEventListener' | 'removeEventListener'> = globalThis): Promise<Route | null> {
	const b = bridge();
	if (!b || !target.addEventListener) return Promise.resolve(null);
	const id = ++packAsks;
	return new Promise((resolve) => {
		const done = (r: Route | null): void => {
			clearTimeout(timer);
			target.removeEventListener('jarvis:car-route', heard);
			resolve(r);
		};
		const heard = (e: Event): void => {
			const detail = (e as CustomEvent).detail as { id?: unknown } | null;
			if (detail?.id === id) done(readPackRoute(detail, id));
		};
		const timer = setTimeout(() => done(null), timeoutMs);
		target.addEventListener('jarvis:car-route', heard);
		try {
			b.act(JSON.stringify({ kind: 'packroute', id, from, to } satisfies CarAct));
		} catch {
			done(null);
		}
	});
}

/** The live car state for the HUD; one per page. */
export class CarLink {
	state = $state<CarState>(NO_CAR);
	private readonly onCar = (e: Event): void => {
		this.state = readCarState((e as CustomEvent).detail);
	};

	constructor() {
		const b = bridge();
		if (!b) return;
		try {
			this.state = readCarState(JSON.parse(b.state()));
		} catch {
			this.state = { ...NO_CAR, linked: true };
		}
		globalThis.addEventListener?.('jarvis:car', this.onCar);
	}

	/** Stop hearing the phone: the page left `/car`, and a link left listening drew a screen that is gone. */
	dispose(): void {
		globalThis.removeEventListener?.('jarvis:car', this.onCar);
	}

	/** Ask the phone; false when there is no phone to ask. */
	act(what: CarAct): boolean {
		const b = bridge();
		if (!b) return false;
		b.act(JSON.stringify(what));
		return true;
	}
}
