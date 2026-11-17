import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { NO_CAR, askPackRoute, callTime, initials, groundRoute, newestDestination, reactorOfCar, readCarState, readPackRoute, readDestination, sayInCar, tookRoute, voiceAct, type CarAct } from './car.svelte';

describe('readCarState', () => {
	it('reads what the phone sends, field by field', () => {
		const s = readCarState({
			media: { title: 'Song', artist: 'Band', app: 'Spotify', playing: true, position: 12, duration: 200 },
			call: { state: 'ringing', name: 'Mum', number: '+1', photo: '', since: null },
			messages: [{ id: 'm1', from: 'Sam', app: 'Signal', text: 'On my way', at: 1 }, { id: '', text: 'no id' }],
			place: { lat: 41.9, lon: -87.6, heading: 90, speed: 12 },
			navApps: ['organic', 'google']
		});
		expect(s.linked).toBe(true);
		expect(s.media?.playing).toBe(true);
		expect(s.call?.name).toBe('Mum');
		expect(s.messages.map((m) => m.id)).toEqual(['m1']);
		expect(s.place?.heading).toBe(90);
		expect(s.navApps).toEqual(['organic', 'google']);
	});

	it('reads the newest notice and the music level for the reactor', () => {
		const s = readCarState({
			notice: { id: 'n1', app: 'Gmail', title: 'Invoice', text: 'Due Friday', at: 5 },
			music: { pulse: true, level: 1.7 }
		});
		expect(s.notice).toEqual({ id: 'n1', app: 'Gmail', title: 'Invoice', text: 'Due Friday', at: 5 });
		expect(s.music).toEqual({ pulse: true, level: 1 });
		expect(readCarState({ notice: { id: 'n2' } }).notice).toBeNull();
	});

	it('reads nothing as nothing, never throws', () => {
		expect(readCarState(null)).toEqual({ ...NO_CAR, linked: true });
		expect(readCarState({ media: { artist: 'x' }, place: { lat: 'north' } }).media).toBeNull();
	});

	it('a car turn drives the reactor while it runs, and gives it back when idle', () => {
		const at = (phase: string) => reactorOfCar(readCarState({ voice: { phase } }).voice);
		expect(at('listening')).toBe('listening');
		expect(at('speaking')).toBe('speaking');
		expect(at('error')).toBe('alert');
		expect(at('idle')).toBeNull();
		expect(reactorOfCar(null)).toBeNull();
	});
});

describe('a route the car page was sent itself', () => {
	it('is a destination, and the newer of it and the phone\'s wins', () => {
		const mine = readDestination({ lat: 41.26, lon: -40.6, label: "Casey's", app: '' }, 200);
		expect(mine).toEqual({ lat: 41.26, lon: -40.6, label: "Casey's", app: '', at: 200, via: [] });
		expect(readDestination({ label: 'nowhere' }, 1)).toBeNull();
		const phone = { lat: 1, lon: 2, label: 'old', app: '', at: 100, via: [] };
		expect(newestDestination(phone, mine)).toBe(mine);
		expect(newestDestination(null, mine)).toBe(mine);
		expect(newestDestination(phone, null)).toBe(phone);
	});
});

describe('a route with stops', () => {
	it('reads the stops in order and keeps the page\'s copy over the phone\'s stopless one', () => {
		const page = readDestination({ lat: 42, lon: -87, label: 'Home', via: [{ lat: 41.9, lon: -87.6, label: 'Pharmacy' }, { lat: 'x', lon: 1 }] }, 1_000)!;
		expect(page.via).toEqual([{ lat: 41.9, lon: -87.6, label: 'Pharmacy' }]);
		const phone = { lat: 42, lon: -87, label: 'Home', app: '', at: 1_500, via: [] };
		expect(newestDestination(phone, page)).toBe(page);
		expect(newestDestination(page, { ...phone, lat: 43, at: 2_000 })!.lat).toBe(43);
	});
});

/** The phone's side of this shape is CarHud.state; both suites read the one table (T-0326). */
const CONTRACT = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../tests/contracts/car-state.json', import.meta.url)), 'utf8'));

describe('the car state contract', () => {
	it('reads what the phone can see and the driver\'s apps', () => {
		const s = readCarState(CONTRACT.example);
		expect(s.access).toEqual(CONTRACT.reads.access);
		expect(s.apps).toEqual(CONTRACT.reads.apps);
	});

	it('assumes it can see, and offers no apps, when an older phone sends neither', () => {
		const s = readCarState({});
		expect(s.access).toEqual(CONTRACT.absent.access);
		expect(s.apps).toEqual(CONTRACT.absent.apps);
		expect(NO_CAR.apps).toEqual([]);
	});

	it('names the acts the phone answers', () => {
		// The page builds these in CarBody; the phone reads them in CarHud.appOf / wantsNotificationAccess.
		const app: CarAct = { kind: 'app', pkg: CONTRACT.acts.app.pkg };
		const access: CarAct = { kind: 'access', op: 'notifications' };
		expect(app).toEqual(CONTRACT.acts.app);
		expect(access).toEqual(CONTRACT.acts.access);
	});

	it('hands the phone its ground and route as CarHud.groundOf / routeOf read them (T-0741)', () => {
		const ground: CarAct = { kind: 'ground', on: true };
		expect(ground).toEqual(CONTRACT.acts.ground);
		const steps = [
			{ lat: 59.33, lon: 18.06, text: 'Head north on Larch Way', type: 'depart', modifier: '', name: 'Larch Way' },
			{ lat: 59.3308, lon: 18.06, text: 'Turn right onto Quarry Road', type: 'turn', modifier: 'right', name: 'Quarry Road' },
			{ lat: 59.3308, lon: 18.0638, text: 'You have arrived', type: 'arrive', modifier: '', name: 'Quarry Road' }
		];
		const route = { points: CONTRACT.acts.route.points, steps, distance: 300, duration: 40 };
		expect(groundRoute(route, 1)).toEqual(CONTRACT.acts.route);
		expect(groundRoute(null, 0)).toEqual(CONTRACT.acts.noRoute);
	});

	it('drops an app with no package', () => {
		expect(readCarState({ apps: [{ label: 'Nameless' }, { pkg: 'a.b', label: '' }] }).apps).toEqual([{ pkg: 'a.b', label: 'a.b' }]);
	});
});

// T-1075: with the house's router away the page had no route at all, though the phone under it
// holds the roads. It asks the phone, which answers with an event.
describe('a route from the phone\'s map packs (T-1075)', () => {
	const g = globalThis as { JarvisCar?: unknown };
	const P = CONTRACT.packRoute;
	afterEach(() => {
		delete g.JarvisCar;
	});

	it('reads the phone\'s answer as a route and words each turn itself', () => {
		const r = readPackRoute(P.answer, P.act.id)!;
		expect(r.points).toEqual(P.answer.route.points);
		expect(r.steps.map((s) => s.text)).toEqual(['Turn right onto Quarry Road', 'You have arrived']);
		expect(r.steps[0]).toMatchObject({ lat: 59.3308, lon: 18.06, type: 'turn', modifier: 'right', name: 'Quarry Road' });
		expect([r.distance, r.duration]).toEqual([320, 40]);
	});

	it('reads no route, another ask\'s answer and a broken one as none', () => {
		expect(readPackRoute(P.none, P.act.id)).toBeNull();
		expect(readPackRoute(P.answer, P.act.id + 1)).toBeNull();
		expect(readPackRoute({ id: 7, route: { points: [[1, 2]], steps: [] } }, 7)).toBeNull();
		expect(readPackRoute({ id: 7, route: { points: [[1, 2], ['x', 3]], steps: 4 } }, 7)).toBeNull();
		expect(readPackRoute(null, 7)).toBeNull();
	});

	it('asks the phone as the contract has it and takes the answer with its id', async () => {
		const target = new EventTarget();
		const acts: { kind: string; id: number }[] = [];
		g.JarvisCar = {
			state: () => '{}',
			act: (json: string) => {
				const act = JSON.parse(json);
				acts.push(act);
				// An older ask's answer first: it is not this one's.
				target.dispatchEvent(new CustomEvent('jarvis:car-route', { detail: { ...P.answer, id: act.id - 1 } }));
				target.dispatchEvent(new CustomEvent('jarvis:car-route', { detail: { ...P.answer, id: act.id } }));
			},
		};
		const r = await askPackRoute(P.act.from, P.act.to, 1000, target);
		expect(acts).toEqual([{ ...P.act, id: acts[0].id }]);
		expect(r?.steps).toHaveLength(2);
	});

	it('is no route with no phone, and when the phone never answers', async () => {
		expect(await askPackRoute(P.act.from, P.act.to, 1000, new EventTarget())).toBeNull();
		g.JarvisCar = { state: () => '{}', act: () => {} };
		expect(await askPackRoute(P.act.from, P.act.to, 20, new EventTarget())).toBeNull();
	});
});

describe('sayInCar (T-0743)', () => {
	const g = globalThis as { JarvisCar?: unknown };
	afterEach(() => {
		delete g.JarvisCar;
	});

	it("hands a line to the phone's car voice, which needs no screen share", () => {
		const acts: string[] = [];
		g.JarvisCar = { state: () => '{}', act: (json: string) => acts.push(json) };
		expect(sayInCar('Fuel is low.')).toBe(true);
		expect(acts.map((a) => JSON.parse(a))).toEqual([{ kind: 'say', text: 'Fuel is low.' }]);
	});

	it('says no when there is no phone to say it, so the page plays it itself', () => {
		expect(sayInCar('Fuel is low.')).toBe(false);
	});
});

// The owner, 2026-11-09: a route Jarvis said was open had opened nowhere. `car_navigate` now
// waits for the screen that took the route to say so; this page draws it on its own map.
describe('tookRoute', () => {
	it('answers the waiting call that the car has the route, and only for a route with an id', () => {
		expect(tookRoute('s1', { id: 'a1b2c3d4e5f60718', lat: 41.1, lon: -40.6 })).toBe(
			'@s1 phone-result a1b2c3d4e5f60718 {"status":"ok","opened":"car"}'
		);
		expect(tookRoute('s1', { lat: 41.1, lon: -40.6 })).toBeNull(); // an older Jarvis waits on nothing
		expect(tookRoute('', { id: 'a1b2c3d4e5f60718' })).toBeNull();
	});
});

// T-0760: the call card shows who is calling (their picture, or initials) and how long it has run.
describe("which voice says a reply in the car (T-1161)", () => {
	it('is handed to the phone as the act the contract has', () => {
		expect(voiceAct('phone')).toEqual(CONTRACT.voice.act);
		for (const mode of CONTRACT.voice.modes) expect(voiceAct(mode)).toEqual({ kind: 'voice', mode });
	});
	it('is auto for a value the phone would drop, or none', () => {
		expect(voiceAct('loud')).toEqual({ kind: 'voice', mode: 'auto' });
		expect(voiceAct(undefined)).toEqual({ kind: 'voice', mode: 'auto' });
	});
});

describe('a live call', () => {
	it('is read as the contract has it, photo and start', () => {
		expect(readCarState({ call: CONTRACT.call.active }).call).toEqual(CONTRACT.call.active);
		expect(readCarState({ call: CONTRACT.call.ringing }).call).toEqual(CONTRACT.call.ringing);
	});
	it('is answered and ended by the acts the phone reads', () => {
		const answer: CarAct = { kind: 'call', op: 'answer' };
		const hangup: CarAct = { kind: 'call', op: 'hangup' };
		expect(answer).toEqual(CONTRACT.call.answer);
		expect(hangup).toEqual(CONTRACT.call.hangup);
	});
	it('from an older phone has no photo and no start', () => {
		expect(readCarState({ call: { state: 'active', name: 'Sam', number: '' } }).call).toEqual({ state: 'active', name: 'Sam', number: '', photo: '', since: null });
	});
	it('never draws a photo that is not an image data URL', () => {
		for (const photo of ['https://example.com/a.jpg', 'javascript:alert(1)', 'data:text/html;base64,AAAA', 'data:image/svg+xml;base64,AAAA', 42]) {
			expect(readCarState({ call: { ...CONTRACT.call.active, photo } }).call?.photo).toBe('');
		}
	});
	it('counts its time up, and shows none before it began or for a clock that is behind', () => {
		expect(callTime(1000, 1000)).toBe('0:00');
		expect(callTime(1000, 66_000)).toBe('1:05');
		expect(callTime(1, 3_725_001)).toBe('1:02:05');
		expect(callTime(null, 5000)).toBe('');
		expect(callTime(9000, 5000)).toBe('0:00');
	});
	it('has initials for a caller with no picture', () => {
		expect(initials('Sam Example')).toBe('SE');
		expect(initials('  mum ')).toBe('M');
		expect(initials('Ana María de la Cruz')).toBe('AC');
		expect(initials('+1 555 0100')).toBe('#');
		expect(initials('')).toBe('#');
	});
});
