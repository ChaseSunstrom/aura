<!--
@component
The car's display (`/car`) on the v2 design system. The phone's web view on the
head unit loads it (`android-app/…/car/dash/CarHud.kt`) and hands over what
only the phone knows through `window.JarvisCar` (`hud/car.svelte.ts`).

Landscape and glanceable, laid out as `docs/design/v2/Car.dc.html`: the street
round the car is the ground, the next turn is at the top left, the speed (with
the time and where the route ends) at the top right, the core at the bottom
right, the one card it projects beside it, and what is being said at the
bottom left. Nothing needs a tap to be read; a button only acts.

One card at a time, by what matters most: a request waiting on the driver, a
call, the newest panel or chart Jarvis launched (until dismissed), what is playing,
the newest message. A phone without notification access says so where the music
would be, with the one switch that fixes it. The last notification has its own card
under that one, until dismissed or replaced (T-0754).

What the legacy car HUD did still happens here: a destination Jarvis chose is
routed on the car's own map once there is a fix, each turn is said on the car's
voice in time (route.ts), and Jarvis says to slow down (speed.ts).

Does not cover: the legacy car's panels, tabs, tiles, drag, pinch and Apps
panel (the car shell's own launcher opens the driver's apps), or a reply typed
on the head unit (a reply is a car turn, spoken).
-->
<script lang="ts">
	import { wornName } from '$lib/ds/theme.svelte';
	import { onMount, untrack } from 'svelte';

	import { useHermes } from '$lib/hermes/store.svelte';
	import { CarLink, askPackRoute, callTime, groundRoute, initials, newestDestination, readDestination, reactorOfCar, tookRoute, voiceAct, type CarDestination } from '$lib/hud/car.svelte';
	import { contentPanels } from '$lib/hud/data';
	import { settingsCatalogue, settingsValues } from '$lib/hud/data/settings';
	import type { Feeds } from '$lib/hud/feeds.svelte';
	import { worldPx } from '$lib/hud/mapTiles';
	import { groundBox, groundPaths, needsStreets, type Streets } from './carGround';
	import { OFF_ROUTE_M, ROUTE_TIMEOUT_MS, turnWord, coming, findRoute, houseOrPack, metres, nextStep, offRoute, type Refused, type Route } from '$lib/hud/route';
	import { BAND_WORD, DEFAULT_PREFS, MPS_TO_MPH, SpeedWindow, limitHere, reading, roadLimit, warning, type Band, type Reading, type RoadLimit, type SpeedPrefs } from '$lib/hud/speed';
	import { tintFor, type Persona } from '$lib/orb/personas';
	import type { ReactorStateName } from '$lib/reactor';
	import type { Tone } from '../tone';
	import Core from '../core/Core.svelte';
	import Figure from '../Figure.svelte';
	import Label from '../Label.svelte';
	import Projected from '../gen/Projected.svelte';
	import Projection from '../Projection.svelte';
	import Subtitles from '../Subtitles.svelte';
	import Button from '../ui/Button.svelte';
	import { contentLine } from '../shell/projections';
	import { sceneFor } from '../shell/scene';
	import { SvelteSet } from 'svelte/reactivity';

	interface Props {
		/** The primary persona's name: what the core is called, and whose words say slow down. */
		name: string;
		/** The page's own state; a car turn's phase wins over it. */
		reactor: ReactorStateName;
		/** The settings feed, for `hud.car_*`. */
		feeds: Feeds;
	}
	let { name, reactor, feeds }: Props = $props();

	const car = new CarLink();
	const s = $derived(car.state);
	const hermes = useHermes();
	/** False once the screen is gone: a route that comes back later is not said. */
	let live = true;

	// --- the car's settings (`hud.car_*`; Jarvis may set them) -----------------

	const values = $derived(settingsValues(settingsCatalogue(feeds.settings.data.core).specs, feeds.settings.data));
	/** Jarvis's own map, or the app a route is handed to. */
	const nav = $derived(String(values['hud.car_navigation'] ?? 'jarvis'));
	const speak = $derived(String(values['hud.car_speak_directions'] ?? 'true') !== 'false');
	const clockShown = $derived(String(values['hud.car_clock'] ?? 'bar') !== 'off');
	const speedWarnings = $derived(String(values['hud.car_speed_warnings'] ?? 'true') !== 'false');
	/** Which voice says a reply (`hud.car_voice`): the phone is told, at load and when it changes. */
	const carVoice = $derived(values['hud.car_voice']);
	$effect(() => {
		car.act(voiceAct(carVoice));
	});
	/** The `hud.car_speed_*` numbers; a missing or unreadable one is its default. */
	const prefs = $derived.by((): SpeedPrefs => {
		const n = (key: string, fallback: number) => {
			const v = Number(values[key]);
			return Number.isFinite(v) && v > 0 ? v : fallback;
		};
		return {
			warnOver: n('hud.car_speed_warn_over', DEFAULT_PREFS.warnOver),
			fullOver: n('hud.car_speed_full_over', DEFAULT_PREFS.fullOver),
			windowMs: n('hud.car_speed_average_s', DEFAULT_PREFS.windowMs / 1000) * 1000,
			warnEveryMs: n('hud.car_speed_repeat_s', DEFAULT_PREFS.warnEveryMs / 1000) * 1000
		};
	});
	const say = (text: string) => {
		if (live) car.act({ kind: 'say', text });
	};

	// --- the core ----------------------------------------------------------------

	/** The music moves the core only while no turn is running: a voice is answered first. */
	const turnOpen = $derived(!!s.voice?.phase?.match(/listening|thinking|speaking/));
	const music = $derived(s.music?.pulse && !turnOpen ? s.music.level : 0);
	const shows = $derived(reactorOfCar(s.voice) ?? reactor);
	const look = $derived(sceneFor({ reactor: shows, music: music > 0 }).core);
	const tint = $derived(tintFor(name as Persona, shows));
	/** The mic is the car's: a press asks the phone for a turn on the head unit's microphone. */
	function talk(): void {
		car.act({ kind: 'talk' });
	}

	// --- the speed (speed.ts) ----------------------------------------------------

	const speedWindow = new SpeedWindow();
	let limit = $state<RoadLimit | null>(null);
	/** The last fix's speed and place; the reading is derived, so a limit that arrives after it shows at once (T-0817). */
	let shown = $state<{ mph: number; at: { lat: number; lon: number } | null } | null>(null);
	const maxAt = (lim: RoadLimit | null, at: { lat: number; lon: number } | null) =>
		at ? (limitHere(lim, at.lat, at.lon)?.limit_mph ?? null) : (lim?.limit_mph ?? null);
	const speed = $derived<Reading | null>(shown ? reading(shown.mph, maxAt(limit, shown.at), prefs.fullOver) : null);
	let limitAsked = { lat: 0, lon: 0, at: 0 };
	// The last few fixes, oldest first: the house matches them to the road the car is on, so a
	// junction's cross street does not win the way a single point did (T-0755).
	const recentFixes: [number, number][] = [];
	let lastWarned: { at: number; band: Band } | null = null;
	$effect(() => {
		const p = s.place;
		// The car's speedometer when it reports one, the phone's GPS otherwise.
		const carMps = s.carSpeed;
		if (!s.linked || (!p && carMps === null)) return;
		untrack(() => {
			const now = Date.now();
			const mps = carMps ?? p!.speed;
			speedWindow.add(now, mps, prefs.windowMs);
			if (p) {
				const last = recentFixes.at(-1);
				if (!last || metres(last[0], last[1], p.lat, p.lon) > FIX_APART_M) recentFixes.push([p.lat, p.lon]);
				if (recentFixes.length > MATCH_FIXES) recentFixes.shift();
			}
			if (p && (metres(p.lat, p.lon, limitAsked.lat, limitAsked.lon) > LIMIT_EVERY_M || now - limitAsked.at > LIMIT_EVERY_MS)) {
				limitAsked = { lat: p.lat, lon: p.lon, at: now };
				const at = { lat: p.lat, lon: p.lon };
				// The house's map-match (`/api/maps/match`, relayed to jarvis-core's Valhalla): the
				// position never leaves the house (T-0765).
				void fetch('/api/maps/match', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ points: recentFixes }) })
					.then((r) => (r.ok ? r.json() : null))
					.then((body) => {
						const got = roadLimit(body, at.lat, at.lon);
						if (got) limit = got;
					})
					.catch(() => undefined);
			}
			const max = maxAt(limit, p);
			// The speed as it is (the owner, 2026-09-26: "it should just be the speed from
			// the car"); only whether Jarvis speaks is judged on the last seconds' mean, so
			// an overtake is not a lecture.
			shown = { mph: mps * MPS_TO_MPH, at: p ? { lat: p.lat, lon: p.lon } : null };
			if (!speedWarnings) return;
			const said = warning(reading(speedWindow.mph, max, prefs.fullOver), name, now, lastWarned, prefs);
			if (said) {
				lastWarned = { at: now, band: said.band };
				say(said.text);
			}
		});
	});
	const BAND_TONE: Record<Band, Tone> = { safe: 'text', speedy: 'accent', risky: 'warn', dangerous: 'danger', deadly: 'danger' };

	// --- the route (route.ts) ----------------------------------------------------
	//
	// A destination Jarvis chose is routed on the car's own map and each turn is said
	// on the car's voice, timed from the car's speed (the owner, 2026-09-25: "it doesn't
	// update the map on the car HUD" and "can Jarvis say navigation's for turns").
	// `hud.car_navigation` hands it to an app instead; `hud.car_speak_directions` turns
	// the words off.

	let route = $state<Route | null>(null);
	let next = $state<{ text: string; modifier: string; type: string; metres: number } | null>(null);
	let ends = $state<{ label: string; at: number } | null>(null);
	let routeKey = '';
	/** The destination whose route has been asked for, so a fix does not ask again. */
	let routing = '';
	let routeSaid = new Set<string>();
	let routePassed = 0;
	/** The step the phone was last told the coming words from; -1 for a new route. */
	let preparedFrom = -1;
	/** The step ahead, for the phone's ground (`groundRoute`); `routePassed` is not reactive. */
	let turnAt = $state(0);
	let offSince = 0;
	/** The stops this route has reached, by "lat,lon", so a reroute does not go back to them. */
	let passedStops = new Set<string>();
	/** When a route was cancelled: a destination asked before it is not routed again. */
	let cancelledAt = 0;
	/** A route this page was sent itself (`hud.car.navigate` on its own session). */
	let pageDestination = $state<CarDestination | null>(null);

	/**
	 * A route from where the car is now (not where it was when first asked: a retry is seconds
	 * later), asked again while the request is lost and `wanted` holds (route.ts `findRoute`).
	 */
	function routeFrom(p: { lat: number; lon: number }, d: CarDestination, wanted: () => boolean = () => live): Promise<Route | Refused | null> {
		return findRoute(async () => {
			const at = s.place ?? p;
			const via = d.via.filter((v) => !passedStops.has(`${v.lat},${v.lon}`));
			const through = via.length ? `&via=${via.map((v) => `${v.lat},${v.lon}`).join(';')}` : '';
			// The house's router away or answering an error: the phone's own packs (T-1075).
			// It routes to one place, so a route with stops left stays the house's.
			return houseOrPack(
				() => fetch(`/api/route?from=${at.lat},${at.lon}&to=${d.lat},${d.lon}${through}`, { signal: AbortSignal.timeout(ROUTE_TIMEOUT_MS) }),
				() => (via.length ? Promise.resolve(null) : askPackRoute([at.lat, at.lon], [d.lat, d.lon]))
			);
		}, wanted);
	}
	function cancelRoute(): void {
		cancelledAt = Date.now();
		pageDestination = null;
		route = null;
		next = null;
		ends = null;
		routeKey = '';
		car.act({ kind: 'arrived' });
		if (speak) say('Route cancelled.');
	}
	$effect(() => {
		const d = newestDestination(s.destination, pageDestination);
		const p = s.place;
		// Read so a route arriving works out the next turn at once, not at the next fix.
		void route;
		// Read here, outside `untrack`, so the end of a voice turn runs this again at once: a turn's
		// word held while the microphone was open is said then, if the corner is still ahead (T-1080).
		const turnIsOpen = turnOpen;
		// The phone's copy lingers until its next update; one asked before a cancel stays cancelled.
		if (!s.linked || !d || d.at <= cancelledAt) return;
		untrack(() => {
			const key = `${d.lat},${d.lon},${d.at},${d.via.length}`;
			if (key !== routeKey) {
				routeKey = key;
				passedStops = new Set();
				route = null;
				next = null;
				ends = null;
				routeSaid = new Set();
				routePassed = 0;
				turnAt = 0;
				preparedFrom = -1;
				routing = '';
				const app = d.app || (nav !== 'jarvis' ? nav : '');
				if (app) {
					car.act({ kind: 'navigate', lat: d.lat, lon: d.lon, label: d.label, app });
					return;
				}
			}
			// Routed once there is a position: a destination that came before the car's
			// first fix was keyed and dropped, so "route me to Casey's" showed and said
			// nothing (2026-10-07). One request per destination, not one per fix.
			if (!route && p && routing !== key) {
				routing = key;
				void routeFrom(p, d, () => live && routeKey === key).then((r) => {
					if (!live || routeKey !== key) return;
					// The house called the request wrong: the driver is told why, once (this
					// destination is not asked again), and no route is drawn from the phone (T-1214).
					if (r && 'refused' in r) {
						route = null;
						notice = { id: `route:${key}`, head: `Route to ${d.label || 'your destination'}`, body: r.refused };
						if (speak) say(`I could not route to ${d.label || 'there'}: ${r.refused}.`);
						return;
					}
					route = r;
					if (!r) {
						if (speak) say(`I could not find a route to ${d.label || 'there'}.`);
						return;
					}
					ends = { label: d.label, at: Date.now() + r.duration * 1000 };
					const stops = d.via.map((v) => v.label).filter(Boolean);
					const by = stops.length ? ` by way of ${stops.join(' and ')}` : d.via.length ? ` with ${d.via.length} stop${d.via.length > 1 ? 's' : ''}` : '';
					if (speak) say(`Route to ${d.label || 'your destination'}${by}: ${Math.max(1, Math.round(r.duration / 60))} minutes.`);
				});
				return;
			}
			if (!route || !p) return;
			// Off the route for a while: routed again from here.
			if (offRoute(route, p.lat, p.lon) > OFF_ROUTE_M) {
				offSince ||= Date.now();
				if (Date.now() - offSince > REROUTE_AFTER_MS) {
					offSince = 0;
					routeSaid = new Set();
					routePassed = 0;
					preparedFrom = -1;
					void routeFrom(p, d, () => live && routeKey === key).then((r) => {
						// A refused reroute keeps the route in hand; the first ask already said why.
						if (live && routeKey === key && r && !('refused' in r)) route = r;
					});
				}
				return;
			}
			offSince = 0;
			const ahead = nextStep(route, p.lat, p.lon, routePassed);
			if (!ahead) return;
			// The words of the turns ahead go to the phone as each turn becomes the next one, so
			// they are voiced before they are due (route.ts `coming`).
			if (speak && ahead.index !== preparedFrom) {
				preparedFrom = ahead.index;
				car.act({ kind: 'prepare', texts: coming(route, ahead.index) });
			}
			routePassed = ahead.index;
			turnAt = ahead.index;
			const step = route.steps[ahead.index];
			next = { text: step.text, modifier: step.modifier, type: step.type, metres: ahead.metres };
			const word = turnWord(route, ahead.index, ahead.metres, s.carSpeed ?? p.speed, routeSaid, turnIsOpen);
			if (!word) return;
			routeSaid.add(word.key);
			if (speak) say(word.text);
			if (step.type === 'arrive' && word.key.startsWith('at:')) car.act({ kind: 'arrived' });
			if (step.type === 'stop' && word.key.startsWith('at:')) {
				const reached = d.via.find((v) => !passedStops.has(`${v.lat},${v.lon}`));
				if (reached) passedStops.add(`${reached.lat},${reached.lon}`);
			}
		});
	});

	// --- the map -----------------------------------------------------------------

	/**
	 * The phone draws the hologram drive under this page (T-0741; the owner, 2026-11-08: "the
	 * hologram map view isn't showing, only the default 2d open street map view"): the page asks
	 * for it, draws no map of its own, is see-through, and hands it the route. Its own map moved
	 * once a phone state, a second apart (T-0747); the phone's moves every frame.
	 */
	const ground = $derived(s.access.ground);
	$effect(() => {
		if (!ground) return;
		car.act({ kind: 'ground', on: true });
		document.documentElement.dataset.carGround = '';
		return () => {
			car.act({ kind: 'ground', on: false });
			delete document.documentElement.dataset.carGround;
		};
	});
	/** The route as the ground last had it, so a fix that changes nothing sends nothing. */
	let groundSent = '';
	$effect(() => {
		if (!ground) return;
		const key = route ? `${routeKey}:${route.points.length}:${turnAt}` : '';
		if (key === groundSent) return;
		groundSent = key;
		car.act(groundRoute(route, turnAt));
	});

	/** A few blocks either way: the scale the route is drawn at round the car. */
	const ZOOM = 16;
	let mapW = $state(0);
	let mapH = $state(0);
	/** The route as an SVG path in the map's pixels, centred on the car. */
	const path = $derived.by(() => {
		if (ground || !route || !s.place || !mapW || !mapH) return '';
		const c = worldPx(s.place.lat, s.place.lon, ZOOM);
		return route.points
			.map(([lat, lon], i) => {
				const q = worldPx(lat, lon, ZOOM);
				return `${i ? 'L' : 'M'}${(q.x - c.x + mapW / 2).toFixed(1)},${(q.y - c.y + mapH / 2).toFixed(1)}`;
			})
			.join(' ');
	});

	// The house's own streets round the car, under the route (carGround.ts says why). Asked again
	// only when the car has driven a quarter of the map, and no more often than STREETS_EVERY_MS
	// while the house has no pack here (a 404 would otherwise be asked on every fix).
	let streets = $state<Streets | null>(null);
	/** The car's world pixel when the drawn streets were asked for. */
	let streetsAt = $state<{ x: number; y: number } | null>(null);
	let streetsAsked = 0;
	$effect(() => {
		if (ground || !s.place || !mapW || !mapH) return;
		const at = worldPx(s.place.lat, s.place.lon, ZOOM);
		if (!needsStreets(untrack(() => streetsAt), at, mapW, mapH) || Date.now() - streetsAsked < STREETS_EVERY_MS) return;
		streetsAsked = Date.now();
		const box = groundBox(s.place.lat, s.place.lon, mapW, mapH, ZOOM);
		void fetch(`/api/maps/buildings?bbox=${box.map((v) => v.toFixed(5)).join(',')}`, { signal: AbortSignal.timeout(ROUTE_TIMEOUT_MS) })
			.then((r) => (r.ok ? r.json() : null))
			.then((body) => {
				if (!body) return;
				streets = groundPaths(body, at, ZOOM);
				streetsAt = at;
			})
			.catch(() => undefined); // no streets is the route alone, as before
	});
	/** Where the streets sit now: one translate as the car moves, not a redraw. */
	const streetsShift = $derived.by(() => {
		if (!streetsAt || !s.place) return '';
		const c = worldPx(s.place.lat, s.place.lon, ZOOM);
		return `translate(${(streetsAt.x - c.x + mapW / 2).toFixed(1)} ${(streetsAt.y - c.y + mapH / 2).toFixed(1)})`;
	});
	/** The next turn's arrow, turned the way the road goes. */
	const TURN: Record<string, number> = { left: -90, 'slight left': -45, 'sharp left': -135, right: 90, 'slight right': 45, 'sharp right': 135, uturn: 180 };
	/** "300 m", "1.2 km": the banner's distance, short enough to read in a glance. */
	const short = (m: number) => (m >= 1000 ? `${(Math.round(m / 100) / 10).toString()} km` : `${m >= 200 ? Math.round(m / 50) * 50 : Math.round(m / 10) * 10} m`);

	// --- the card ------------------------------------------------------------------

	/**
	 * The newest notification, once: the phone sends its newest on every update, and a
	 * card closed came straight back (the owner, 2026-09-25), and a reconnect reloads the
	 * page (1447), so the one shown is kept for the web view's session.
	 */
	const SHOWN_NOTICE_KEY = 'jarvis:car-notice-shown';
	let shownNoticeId = (() => {
		try {
			return sessionStorage.getItem(SHOWN_NOTICE_KEY) ?? '';
		} catch {
			return '';
		}
	})();
	let notice = $state<{ id: string; head: string; body: string } | null>(null);
	$effect(() => {
		const n = s.notice;
		if (!n) return;
		untrack(() => {
			if (shownNoticeId === n.id) return;
			shownNoticeId = n.id;
			try {
				sessionStorage.setItem(SHOWN_NOTICE_KEY, n.id);
			} catch {
				/* shown once for this page, as before */
			}
			const head = n.title || n.text;
			// Kept until the next one or a Dismiss: the slot under the music is "the last
			// notification" (the owner, T-0754), not a popup that leaves on a timer.
			notice = { id: n.id, head: n.app ? `${n.app} · ${head}` : head, body: n.title && n.text ? n.text : '' };
		});
	});
	/**
	 * What Jarvis launched on this screen: its panels (`hud_show`, not the kept ones, which are
	 * the console's tabs) and its charts (`hud_project`), each in the order it arrived; the
	 * newest one the driver has not closed is the card (T-0785: the car drew neither).
	 * Does not cover a panel's full body: the car reads its title and first line.
	 */
	const hud = $derived(hermes?.view.snap?.hud ?? null);
	const arrived = new WeakMap<object, number>();
	let arrivals = 0;
	const arrival = (o: object): number => arrived.get(o) ?? (arrived.set(o, ++arrivals), arrivals);
	const closed = new SvelteSet<string>();
	const launched = $derived.by(() => {
		const all = [
			...(hud?.turn.visuals ?? []).map((frame) => ({ key: `visual:${frame.id}:${arrival(frame)}`, at: arrival(frame), frame, panel: null })),
			...(hud?.panels ?? [])
				.filter((p) => !p.kept)
				.map((p) => ({ key: `panel:${p.id}:${arrival(p)}`, at: arrival(p), frame: null, panel: contentPanels([p])[0] }))
		];
		return all.filter((l) => !closed.has(l.key)).reduce<(typeof all)[number] | null>((a, l) => (!a || l.at > a.at ? l : a), null);
	});
	const newest = $derived(s.messages.reduce<(typeof s.messages)[number] | null>((a, m) => (!a || m.at > a.at ? m : a), null));
	const card = $derived.by(() => {
		if (s.ask) return { kind: 'ask', key: `ask:${s.ask.id}` } as const;
		if (s.call) return { kind: 'call', key: `call:${s.call.name}|${s.call.number}` } as const;
		if (launched) return { kind: 'launched', key: launched.key } as const;
		if (s.media) return { kind: 'media', key: `media:${s.media.title}` } as const;
		// Blind, not empty: "Nothing playing" over a song sent the driver looking for a
		// fault in the music (field log 2026-10-09, T-0326).
		if (!s.access.notifications) return { kind: 'blind', key: 'blind' } as const;
		if (newest) return { kind: 'message', key: `message:${newest.id}` } as const;
		return null;
	});
	/** "now", "5 min", "2 h": how long ago a message came. */
	const ago = (at: number) => {
		const min = Math.max(0, Math.round((now - at) / 60_000));
		return min < 1 ? 'now' : min < 60 ? `${min} min` : `${Math.round(min / 60)} h`;
	};

	// --- what is being said ------------------------------------------------------

	const heard = $derived(s.voice?.heard ?? '');
	const said = $derived(s.voice?.answer || s.voice?.caption || (reactor === 'offline' && !s.voice ? `${name} is offline. The phone still answers.` : ''));

	// --- the clock, and where the core is ---------------------------------------

	let now = $state(Date.now());
	const time = (at: number) => new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
	// A call's time runs by the second, and only while one is on: the clock above ticks four times a minute.
	let callNow = $state(Date.now());
	const callSince = $derived(s.call && s.call.state !== 'ringing' ? s.call.since : null);
	$effect(() => {
		if (callSince === null) return;
		callNow = Date.now();
		const tick = setInterval(() => (callNow = Date.now()), SECOND_MS);
		return () => clearInterval(tick);
	});
	let coreEl = $state<HTMLElement>();
	let coreAt = $state<{ x: number; y: number } | undefined>();
	function measure(): void {
		const r = coreEl?.getBoundingClientRect();
		if (r) coreAt = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
	}

	onMount(() => {
		measure();
		window.addEventListener('resize', measure);
		const tick = setInterval(() => (now = Date.now()), CLOCK_MS);
		// The car page holds its own Hermes session (its voice turns run there), so
		// `car_navigate` from a car turn reaches this page, not the phone's home screen.
		const off = hermes?.link.client.onEvent((event) => {
			if (event.type !== 'hud.car.navigate') return;
			if ((event.payload as { cancel?: unknown } | undefined)?.cancel === true) return cancelRoute();
			const d = readDestination(event.payload, Date.now());
			if (!d) return;
			pageDestination = d;
			const took = tookRoute(hermes?.link.sessionId ?? '', event.payload);
			if (took) void hermes?.link.dispatch('hud', took);
		});
		return () => {
			live = false;
			window.removeEventListener('resize', measure);
			clearInterval(tick);
			off?.();
			car.dispose();
		};
	});

	const REROUTE_AFTER_MS = 8_000;
	/** The road's limit is asked again past this far or this long. */
	const LIMIT_EVERY_M = 120;
	const LIMIT_EVERY_MS = 30_000;
	/** How often a place the house has no streets for is asked again: the speed limit's own beat. */
	const STREETS_EVERY_MS = LIMIT_EVERY_MS;
	/** How many fixes the match gets, and how far apart: about the last half kilometre. */
	const MATCH_FIXES = 6;
	const FIX_APART_M = 15;
	const CLOCK_MS = 15_000;
	const SECOND_MS = 1000;
</script>

<section class="car" class:over={ground} aria-label="{name}, in the car" data-testid="screen-car" data-reactor={reactorOfCar(s.voice) ?? reactor} data-linked={s.linked}>
	<!-- For a phone that draws no hologram ground (`ground`): the route and the car on the stage's
	     own ground, over the house's own streets. No street tiles: OpenStreetMap's servers heard
	     where the car was (T-0765); the streets come from the house's packs (T-1521). -->
	<div class="map" bind:clientWidth={mapW} bind:clientHeight={mapH} role="img" aria-label={s.place ? 'The route round the car' : 'No position yet'} data-testid="car-map" data-ground={ground}>
		{#if streets && streetsShift && !ground}
			<svg class="streets" viewBox="0 0 {mapW} {mapH}" aria-hidden="true" data-testid="car-streets">
				<g transform={streetsShift}>
					{#each streets.buildings as d, i (i)}<path class="building" {d} />{/each}
					{#each streets.roads as road, i (i)}<path class="road" data-band={road.band} d={road.d} />{/each}
				</g>
			</svg>
		{/if}
		{#if path}
			<svg class="path" viewBox="0 0 {mapW} {mapH}" aria-hidden="true" data-testid="car-route"><path d={path} /></svg>
		{/if}
		{#if s.place && !ground}<span class="you" class:moving={s.place.heading !== null} style:--turn="{s.place.heading ?? 0}deg"></span>{/if}
	</div>

	{#if next}
		<div class="turn">
			<Projection label="Next turn" testid="car-next">
				<div class="turn-body">
					<svg class="arrow" viewBox="0 0 24 24" aria-hidden="true" style:--turn="{TURN[next.modifier] ?? 0}deg">
						{#if next.type === 'arrive'}<circle cx="12" cy="10" r="4" /><path d="M12 14v7" />{:else}<path d="M12 21V5M6 11l6-6 6 6" />{/if}
					</svg>
					<div>
						<Figure value={short(next.metres)} size="display" />
						<p class="what">{next.text}</p>
					</div>
				</div>
			</Projection>
		</div>
	{/if}

	<div class="top">
		<Projection label="Speed">
			<!-- On a route, when it ends (the board's "Home 18:57"); otherwise the time, when it is wanted. -->
			{#if ends}
				<div data-testid="car-arrive">
					<Label>{ends.label || 'Arriving'}</Label>
					<p class="clock">{time(ends.at)}</p>
				</div>
			{:else if clockShown}
				<p class="clock" data-testid="car-clock">{time(now)}</p>
			{/if}
			{#if speed}
				<div class="speed" data-band={speed.band} data-testid="car-speed">
					<Figure value={Math.round(speed.mph)} unit="mph" size="display" tone={BAND_TONE[speed.band]} testid="car-mph" />
					<p class="limit" data-testid="car-band">
						{#if speed.limit !== null}{BAND_WORD[speed.band]}{speed.over > 0 ? ` · ${Math.round(speed.over)} over` : ''} · limit {speed.limit}{limit?.source === 'typical' ? ' (usual)' : ''}{:else}No limit known here{/if}
					</p>
				</div>
			{/if}
		</Projection>
		<!-- The card (music, a call, a panel) sits right under the time and speed (the owner, 2026-11-09). -->
		{#if card?.kind === 'launched' && launched?.frame}
			{@const frame = launched.frame}
			<div class="card" data-testid="car-launched">
				{#key card.key}
					<Projected type={frame.type} data={frame.data} placement={{ core: coreAt }} testid="car-card" />
				{/key}
				<div class="acts"><Button tone="quiet" testid="car-launched-close" onclick={() => closed.add(card.key)}>Dismiss</Button></div>
			</div>
		{:else if card}
			<div class="card">
				{#key card.key}
					<Projection core={coreAt} label="From {name}" testid="car-card">
						{#if card.kind === 'ask' && s.ask}
							{@const ask = s.ask}
							<div class="ask" data-testid="car-ask">
								<Label tone="warn">Waiting for your OK</Label>
								<p class="big">{ask.title}</p>
								{#if ask.detail}<p class="sub">{ask.detail}</p>{/if}
								<p class="sub">Say yes or no, or tap.</p>
								<div class="acts">
									<Button tone="primary" testid="car-ask-allow" onclick={() => car.act({ kind: 'answer', id: ask.id, ok: true })}>Allow</Button>
									<Button testid="car-ask-deny" onclick={() => car.act({ kind: 'answer', id: ask.id, ok: false })}>Deny</Button>
								</div>
							</div>
						{:else if card.kind === 'call' && s.call}
							{@const call = s.call}
							<!-- Who it is, with their picture (the owner, 2026-11-08: "the phone call + name + pfp"), and how long it has run (T-0760). -->
							<div class="caller">
								{#if call.photo}
									<img class="face" class:ringing={call.state === 'ringing'} src={call.photo} alt="" data-testid="car-call-photo" />
								{:else}
									<span class="face" class:ringing={call.state === 'ringing'} aria-hidden="true" data-testid="car-call-initials">{initials(call.name)}</span>
								{/if}
								<div>
									<Label tone="accent">{call.state === 'ringing' ? 'Calling you' : call.state === 'held' ? 'On hold' : 'On a call'}</Label>
									<p class="big">{call.name || call.number}</p>
									{#if call.state !== 'ringing' && call.since}<p class="sub time" data-testid="car-call-time">{callTime(call.since, callNow)}</p>{/if}
								</div>
							</div>
							<div class="acts">
								{#if call.state === 'ringing'}<Button tone="primary" testid="car-answer" onclick={() => car.act({ kind: 'call', op: 'answer' })}>Answer</Button>{/if}
								<Button testid="car-hangup" onclick={() => car.act({ kind: 'call', op: 'hangup' })}>{call.state === 'ringing' ? 'Decline' : 'Hang up'}</Button>
							</div>
						{:else if card.kind === 'launched' && launched?.panel}
							{@const panel = launched.panel}
							{@const line = contentLine(panel)}
							<div data-testid="car-launched">
								<div data-testid="car-panel">
									<Label tone="accent">{name}</Label>
									<p class="big">{panel.title}</p>
									{#if line}<p class="sub">{line}</p>{/if}
								</div>
								<div class="acts"><Button tone="quiet" testid="car-launched-close" onclick={() => closed.add(card.key)}>Dismiss</Button></div>
							</div>
						{:else if card.kind === 'media' && s.media}
							{@const media = s.media}
							<div data-testid="car-media">
								<Label>{media.playing ? 'Playing' : 'Paused'}{media.app ? ` · ${media.app}` : ''}</Label>
								<p class="big one" title={media.title}>{media.title}</p>
								{#if media.artist}<p class="sub one">{media.artist}</p>{/if}
								<div class="acts mid">
									<button type="button" class="round" aria-label="Previous" data-testid="car-previous" onclick={() => car.act({ kind: 'media', op: 'previous' })}>
										<svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M6 5h2v14H6zM20 5v14L9 12z" /></svg>
									</button>
									<button type="button" class="round main" aria-label={media.playing ? 'Pause' : 'Play'} data-testid="car-play" onclick={() => car.act({ kind: 'media', op: media.playing ? 'pause' : 'play' })}>
										<svg viewBox="0 0 24 24" aria-hidden="true">
											{#if media.playing}<path class="fill" d="M7 5h4v14H7zM13 5h4v14h-4z" />{:else}<path class="fill" d="M7 4v16l13-8z" />{/if}
										</svg>
									</button>
									<button type="button" class="round" aria-label="Next" data-testid="car-next-track" onclick={() => car.act({ kind: 'media', op: 'next' })}>
										<svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="M16 5h2v14h-2zM4 5v14l11-7z" /></svg>
									</button>
								</div>
							</div>
						{:else if card.kind === 'blind'}
							<div data-testid="car-blind">
								<Label tone="warn">Can't see the phone</Label>
								<p class="big">{name} can't see what's playing or your messages.</p>
								<p class="sub">Notification access is off for {wornName()} car panels on the phone.</p>
								<div class="acts"><Button tone="primary" testid="car-grant" onclick={() => car.act({ kind: 'access', op: 'notifications' })}>Open the setting on the phone</Button></div>
							</div>
						{:else if card.kind === 'message' && newest}
							<div data-testid="car-message">
								<Label>Message · {newest.from}{newest.app ? ` · ${newest.app}` : ''}{newest.at ? ` · ${ago(newest.at)}` : ''}</Label>
								<p class="big">{newest.text}</p>
								<p class="sub">Ask {name} to read it or to answer.</p>
							</div>
						{/if}
					</Projection>
				{/key}
			</div>
		{/if}
		<!-- The last notification, under the music rather than in its place (the owner, T-0754). -->
		{#if notice}
			<div class="card">
				{#key notice.id}
					<Projection core={coreAt} label="From {name}" testid="car-notice-card">
						<!-- One row, so it fits under the music above the core on an 800x480 head unit. -->
						<div class="note" data-testid="car-notice">
							<div class="note-text">
								<p class="note-head">{notice.head}</p>
								{#if notice.body}<p class="sub note-body">{notice.body}</p>{/if}
							</div>
							<button type="button" class="round" aria-label="Dismiss" data-testid="car-notice-close" onclick={() => (notice = null)}>
								<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
							</button>
						</div>
					</Projection>
				{/key}
			</div>
		{/if}
	</div>


	<div class="said">
		{#if !s.linked}
			<p class="sub" data-testid="car-unlinked">This is the car's screen. Open it on the car, with the phone linked.</p>
		{/if}
		{#if heard}<Subtitles text={heard} speaker="user" testid="car-heard" />{/if}
		{#if said}<Subtitles text={said} testid="car-said" />{/if}
	</div>

	<button type="button" class="core" bind:this={coreEl} aria-label="Talk to {name}" data-testid="car-talk" disabled={!s.linked} onclick={talk}>
		<Core state={look} {tint} music={music || undefined} />
	</button>
</section>

<style>
	.car {
		/* The core's size: the board's 168 of 600, so it stays the core on any head unit. */
		--car-core: 28vh;
		position: relative;
		flex: 1;
		min-block-size: 0;
		overflow: hidden;
		background: var(--jv-stage-ground);
		color: var(--jv-text);
		font-family: var(--jv-font-body);
	}
	/* Over the phone's hologram (`ground`): see-through down to the window, which the phone
	   also leaves clear (ConsolePage), so the drive under it is the car's ground. */
	:global(html[data-car-ground]),
	:global(html[data-car-ground] body),
	:global(html[data-car-ground] .shell),
	.car.over {
		background: transparent;
	}
	.map {
		position: absolute;
		inset: 0;
		overflow: hidden;
	}
	.path {
		position: absolute;
		inset: 0;
		inline-size: 100%;
		block-size: 100%;
		fill: none;
		stroke: var(--jv-accent);
		stroke-width: 4;
		stroke-linecap: round;
		stroke-linejoin: round;
		filter: drop-shadow(0 0 var(--jv-space-1) var(--jv-accent));
	}
	/* The streets are the ground's own lines, never the accent: the route is the one bright thing. */
	.streets {
		position: absolute;
		inset: 0;
		inline-size: 100%;
		block-size: 100%;
		fill: none;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.streets .building {
		fill: var(--jv-wash);
		stroke: var(--jv-line);
		stroke-width: 1;
	}
	.streets .road {
		stroke: var(--jv-tick);
		stroke-width: 2;
	}
	.streets .road[data-band='mid'] {
		stroke-width: 4;
	}
	.streets .road[data-band='major'] {
		stroke: var(--jv-text-faint);
		stroke-width: 6;
	}
	.you {
		position: absolute;
		inset-block-start: 50%;
		inset-inline-start: 50%;
		inline-size: var(--jv-space-4);
		block-size: var(--jv-space-4);
		translate: -50% -50%;
		border-radius: var(--jv-radius-pill);
		background: var(--jv-accent);
		box-shadow: var(--jv-glow-md);
	}
	/* Moving: a pointer the way the car faces. */
	.you.moving {
		border-radius: 0;
		clip-path: polygon(50% 0, 100% 100%, 50% 75%, 0 100%);
		rotate: var(--turn);
		transition: rotate var(--jv-dur-base) var(--jv-ease-out);
	}
	.turn,
	.top,
	.said {
		position: absolute;
		z-index: 1;
	}
	.turn {
		inset-block-start: var(--jv-space-5);
		inset-inline-start: var(--jv-space-5);
		max-inline-size: 40vw;
	}
	.turn-body {
		display: flex;
		align-items: center;
		gap: var(--jv-space-4);
	}
	.arrow {
		flex: none;
		inline-size: calc(var(--jv-touch) * 1.3);
		block-size: calc(var(--jv-touch) * 1.3);
		fill: none;
		stroke: var(--jv-accent);
		stroke-width: 2;
		stroke-linecap: round;
		stroke-linejoin: round;
		rotate: var(--turn);
		filter: drop-shadow(0 0 var(--jv-space-1) var(--jv-accent));
	}
	.what {
		margin: var(--jv-space-1) 0 0;
		color: var(--jv-text-bright);
		font-size: var(--jv-fs-lg);
	}
	/* The right column: the time and speed, then the one card under them, so a glance at
	   the speed finds the music and a notice too (the owner, 2026-11-09). One width for all
	   of them, each drawn whole: the owner, 2026-11-10, "why are the panels scroll panels,
	   why can't they both entirely fit, same with the speed one, can you make them all
	   horizontally the same size" (T-0976). */
	.top {
		--top-inset: var(--jv-space-5);
		--top-gap: var(--jv-space-4);
		inset-block-start: var(--top-inset);
		inset-inline-end: var(--jv-space-5);
		/* 31vw, not 36: "the panels on the side are slightly too wide" (the owner, 2026-11-10, T-0998). */
		inline-size: min(var(--jv-measure-dialog), 31vw);
		display: flex;
		flex-direction: column;
		gap: var(--top-gap);
		text-align: end;
	}
	/* An 800x480 head unit: the time, the music and the last notification fit above the
	   core only this much tighter (T-0754). */
	@media (max-height: 540px) {
		.car {
			--car-core: 25vh;
		}
		.top {
			--top-inset: var(--jv-space-3);
			--top-gap: var(--jv-space-2);
		}
	}
	.clock {
		margin: 0;
		color: var(--jv-text-bright);
		font: var(--jv-weight-display) var(--jv-fs-2xl) var(--jv-font-display);
		font-variant-numeric: tabular-nums;
	}
	.limit {
		margin: 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	/* The one card, beside the core it comes out of (the board's bottom right): the column's
	   width and its own height, never a scroll box (one clipped a chart's last axis label). */
	.card {
		flex: none;
		text-align: start;
	}
	.note {
		display: flex;
		align-items: center;
		gap: var(--jv-space-3);
	}
	.note-text {
		flex: 1;
		min-inline-size: 0;
	}
	.note-head {
		margin: 0;
		color: var(--jv-text-bright);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.note-body {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.note .round {
		flex: none;
		inline-size: var(--jv-touch);
		block-size: var(--jv-touch);
	}
	.note .round path {
		fill: none;
		stroke: currentColor;
		stroke-width: 2;
		stroke-linecap: round;
	}
	.big {
		margin: var(--jv-space-1) 0 0;
		color: var(--jv-text-bright);
		font: var(--jv-weight-display) var(--jv-fs-xl) var(--jv-font-display);
		overflow-wrap: anywhere;
	}
	.caller {
		display: flex;
		align-items: center;
		gap: var(--jv-space-4);
	}
	.face {
		flex: none;
		display: grid;
		place-items: center;
		inline-size: calc(var(--jv-touch) * 1.5);
		block-size: calc(var(--jv-touch) * 1.5);
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
		background: var(--jv-surface-2);
		color: var(--jv-text-dim);
		font: var(--jv-weight-display) var(--jv-fs-xl) var(--jv-font-display);
		object-fit: cover;
	}
	/* A call that is ringing is the one live thing on the screen: its picture breathes in the accent. */
	.face.ringing {
		border-color: var(--jv-accent);
		animation: ring var(--jv-dur-beat) var(--jv-ease-standard) infinite alternate;
	}
	@keyframes ring {
		from {
			box-shadow: none;
		}
		to {
			box-shadow: var(--jv-glow-md);
		}
	}
	.time {
		font-variant-numeric: tabular-nums;
	}
	.sub {
		margin: var(--jv-space-1) 0 0;
		color: var(--jv-text-dim);
		font-size: var(--jv-fs-sm);
	}
	/* The song on one line, so the music, the speed and a notice all fit above the core on an
	   800x480 head unit with no scroll box (T-0976); a long title ends in an ellipsis. */
	.one {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.acts {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--jv-space-3);
		margin-block-start: var(--jv-space-3);
	}
	/* The music's three controls in the middle of the card, under the song (the owner, 2026-11-10:
	   "the music buttons aren't centered", T-0991). */
	.acts.mid {
		justify-content: center;
	}
	/* The car's targets are larger than the console's: a driver reaches without looking long. */
	.round {
		display: inline-grid;
		place-items: center;
		inline-size: calc(var(--jv-touch) + var(--jv-space-2));
		block-size: calc(var(--jv-touch) + var(--jv-space-2));
		padding: 0;
		border: 1px solid var(--jv-line);
		border-radius: var(--jv-radius-pill);
		background: transparent;
		color: var(--jv-text-bright);
		cursor: pointer;
	}
	.round.main {
		border-color: var(--jv-accent);
		box-shadow: var(--jv-glow-md);
	}
	.round:hover {
		background: var(--jv-wash);
	}
	.round svg {
		inline-size: 40%;
		block-size: 40%;
	}
	.round .fill {
		fill: currentColor;
	}
	.said {
		inset-block-end: var(--jv-space-5);
		inset-inline-start: var(--jv-space-5);
		inline-size: min(var(--jv-measure-dialog), 29vw);
		display: flex;
		flex-direction: column;
		gap: var(--jv-space-1);
	}
	/* Read from the left edge, as the board has it, not centred under the core as on the stage. */
	.said :global(.subtitles) {
		text-align: start;
	}
	/* The core is the talk button. */
	.core {
		position: absolute;
		z-index: 2;
		inset-block-end: var(--jv-space-3);
		inset-inline-end: var(--jv-space-4);
		inline-size: var(--car-core);
		min-inline-size: var(--jv-touch);
		padding: 0;
		border: 0;
		border-radius: var(--jv-radius-pill);
		background: transparent;
		cursor: pointer;
	}
	.core:disabled {
		cursor: default;
	}
	.round:focus-visible,
	.core:focus-visible {
		outline: var(--jv-focus-outline);
		outline-offset: var(--jv-focus-offset);
	}
	@media (prefers-reduced-motion: reduce) {
		.you.moving {
			transition: none;
		}
		.face.ringing {
			animation: none;
			box-shadow: var(--jv-glow-sm);
		}
	}
</style>
