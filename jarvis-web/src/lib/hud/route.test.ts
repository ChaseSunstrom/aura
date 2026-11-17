import { describe, expect, it } from 'vitest';
import { AT_S, HOLD_FLOOR_M, ROUTE_TRIES, SPEECH_LEAD_S, turnWord, announcement, coming, findRoute, houseOrPack, nextStep, readRoute, sayDistance, sayStep, type Route } from './route';

const osrm = {
	routes: [
		{
			distance: 1500,
			duration: 120,
			geometry: { coordinates: [[-40.6, 41.26], [-40.6, 41.27], [-40.59, 41.27]] },
			legs: [
				{
					steps: [
						{ maneuver: { location: [-40.6, 41.26], type: 'depart', modifier: '' }, name: 'Quarry Road' },
						{ maneuver: { location: [-40.6, 41.27], type: 'turn', modifier: 'left' }, name: 'Main Street' },
						{ maneuver: { location: [-40.59, 41.27], type: 'arrive', modifier: '' }, name: '' }
					]
				}
			]
		}
	]
};

describe('the route and its words', () => {
	it('reads OSRM, drops the departure, and says each turn as a driver hears it', () => {
		const r = readRoute(osrm)!;
		expect(r.points[0]).toEqual([41.26, -40.6]);
		expect(r.steps.map((s) => s.text)).toEqual(['Turn left onto Main Street', 'You have arrived']);
		expect(sayStep('roundabout', 'right', 'High St', 2)).toBe('At the roundabout, take exit 2 onto High St');
		expect(readRoute({ routes: [] })).toBeNull();
		expect(sayDistance(430)).toBe('450 metres');
		expect(sayDistance(1240)).toBe('1.2 kilometres');
	});

	it('says the turn when the car is the speech lead plus seven seconds away, at its speed', () => {
		const r = readRoute(osrm) as Route;
		const v = 20; // 72 km/h
		const due = v * (AT_S + SPEECH_LEAD_S); // 190 m
		const said = new Set<string>();
		expect(announcement(r, 0, due + 5, v, new Set(['early:0']))).toBeNull();
		expect(announcement(r, 0, due - 5, v, said)?.text).toBe('Turn left onto Main Street');
		// The early word comes first, further out, with the distance in it; once each.
		const early = announcement(r, 0, 600, v, said)!;
		expect(early.key).toBe('early:0');
		expect(early.text).toBe('In 600 metres, turn left onto Main Street');
		expect(announcement(r, 0, 600, v, new Set(['early:0']))).toBeNull();
	});

	it('a word due while a turn is open waits, and is said on release only if the corner is still ahead (T-1080)', () => {
		const r = readRoute(osrm) as Route;
		const v = 20;
		const said = new Set<string>();
		// Due at 150 m with the microphone open: nothing is said, and nothing is marked said.
		expect(turnWord(r, 0, 150, v, said, true)).toBeNull();
		// The turn ends 120 m on, the corner still ahead: its word is said now, from here.
		expect(turnWord(r, 0, 120, v, said, false)?.key).toBe('at:0');
		// The early word held the same way is not queued: released, it is worded for where the car is.
		expect(turnWord(r, 0, 600, v, said, true)).toBeNull();
		expect(turnWord(r, 0, 500, v, said, false)?.text).toBe('In 500 metres, turn left onto Main Street');
		// Passed while held: the old corner has no word (nextStep moved on), the next one is judged on its own.
		expect(turnWord(r, 1, 900, v, said, false)).toBeNull();
	});

	it('inside the last 100 m a turn is said over an open turn, since a late word is no word', () => {
		const r = readRoute(osrm) as Route;
		expect(turnWord(r, 0, HOLD_FLOOR_M + 1, 20, new Set(), true)).toBeNull();
		expect(turnWord(r, 0, HOLD_FLOOR_M, 20, new Set(), true)?.key).toBe('at:0');
		expect(turnWord(r, 0, 35, 1, new Set(), true)?.key).toBe('at:0');
	});

	it('at a crawl the turn is still said before it, and the next step follows the car', () => {
		const r = readRoute(osrm) as Route;
		expect(announcement(r, 0, 35, 1, new Set())?.key).toBe('at:0');
		expect(nextStep(r, 41.265, -40.6, 0)?.index).toBe(0);
	});
});

describe('the words the phone voices ahead', () => {
	it('are the coming turns exactly as they will be said at the turn, a few at a time', () => {
		const r = readRoute(osrm) as Route;
		expect(coming(r, 0)).toEqual(['Turn left onto Main Street', 'You have arrived']);
		// What is prepared is what `announcement` says, to the letter: the phone keeps a line by its text.
		expect(coming(r, 0)[0]).toBe(announcement(r, 0, 1, 1, new Set())!.text);
		expect(coming(r, 1)).toEqual(['You have arrived']);
		expect(coming(r, 0, 1)).toEqual(['Turn left onto Main Street']);
		expect(coming(r, 5)).toEqual([]);
		expect(coming(r, -1, 1)).toEqual(['Turn left onto Main Street']);
	});
});

describe('a route asked for on a slow link', () => {
	const now = async () => undefined;
	it('is asked again when the request is lost, and the answer that comes is taken', async () => {
		const r = readRoute(osrm) as Route;
		const answers: (Route | null | 'lost')[] = ['lost', 'lost', r];
		let asks = 0;
		expect(await findRoute(async () => answers[asks++], () => true, now)).toBe(r);
		expect(asks).toBe(3);
	});
	it("believes the house's no at once, and stops when the tries are spent or the route is no longer wanted", async () => {
		let asks = 0;
		expect(await findRoute(async () => (asks++, null), () => true, now)).toBeNull();
		expect(asks).toBe(1);
		asks = 0;
		expect(await findRoute(async () => (asks++, { refused: 'bad via' }), () => true, now)).toEqual({ refused: 'bad via' });
		expect(asks).toBe(1);
		asks = 0;
		expect(await findRoute(async () => (asks++, 'lost'), () => true, now)).toBeNull();
		expect(asks).toBe(ROUTE_TRIES);
		asks = 0;
		expect(await findRoute(async () => (asks++, 'lost'), () => false, now)).toBeNull();
		expect(asks).toBe(1);
	});
});

describe('routes through stops', () => {
	it('says a stop at the end of each leg but the last, and arrives only at the end', () => {
		const step = (type: string, lon: number) => ({ maneuver: { location: [lon, 41], type }, name: '' });
		const r = readRoute({
			routes: [{ geometry: { coordinates: [[-87, 41], [-86, 41]] }, legs: [{ steps: [step('depart', -87), step('arrive', -86.5)] }, { steps: [step('depart', -86.5), step('arrive', -86)] }], distance: 1, duration: 1 }]
		})!;
		expect(r.steps.map((s) => s.type)).toEqual(['stop', 'arrive']);
		expect(r.steps[0].text).toBe('You have reached your stop');
	});
});

// T-1075: with the house's router away the page asks the phone under it, which holds the roads.
describe('houseOrPack', () => {
	const body = { routes: [{ geometry: { coordinates: [[18.06, 59.33], [18.07, 59.34]] }, legs: [], distance: 10, duration: 2 }] };
	const packed: Route = { points: [[1, 2], [3, 4]], steps: [], distance: 5, duration: 1 };
	const never = (): Promise<Route | null> => Promise.reject(new Error('the phone was asked'));

	it("takes the house's route, and believes its no route, without asking the phone", async () => {
		expect((await houseOrPack(async () => Response.json(body), never) as Route).distance).toBe(10);
		expect(await houseOrPack(async () => new Response('', { status: 404 }), never)).toBeNull();
	});

	it("asks the phone when the house is lost or answers an error", async () => {
		const lost = (): Promise<Response> => Promise.reject(new Error('offline'));
		expect(await houseOrPack(lost, async () => packed)).toBe(packed);
		expect(await houseOrPack(async () => new Response('', { status: 502 }), async () => packed)).toBe(packed);
		expect(await houseOrPack(async () => new Response('not json'), async () => packed)).toBe(packed);
	});

	// T-1214: a request the house called wrong (a bad via, 400) is wrong for the pack too; the
	// driver is told why and nothing is routed. Only a house that cannot answer falls back.
	it("says the house's reason for a refused request, and routes nothing from the phone", async () => {
		const refuse = (status: number, body: unknown) => async () => Response.json(body, { status });
		expect(await houseOrPack(refuse(400, { message: 'via is up to five "lat,lon"' }), never)).toEqual({ refused: 'via is up to five "lat,lon"' });
		expect(await houseOrPack(refuse(422, { message: 'x'.repeat(500) }), never)).toEqual({ refused: 'x'.repeat(200) });
		// No reason given: still told, and still not routed.
		expect(await houseOrPack(async () => new Response('', { status: 400 }), never)).toEqual({ refused: 'the house refused the route (400)' });
		expect(await houseOrPack(refuse(400, { message: 42 }), never)).toEqual({ refused: 'the house refused the route (400)' });
	});

	it('keeps the phone for a 4xx that is the link or the load and not the request (408, 429), and for the relay\'s own 500 and 502', async () => {
		for (const status of [408, 429, 500, 502, 503]) {
			expect(await houseOrPack(async () => Response.json({ message: 'busy' }, { status }), async () => packed)).toBe(packed);
		}
	});

	it('is lost, to be asked again, only when the house never answered and the phone has none', async () => {
		expect(await houseOrPack(() => Promise.reject(new Error('offline')), async () => null)).toBe('lost');
		expect(await houseOrPack(async () => new Response('', { status: 502 }), async () => null)).toBeNull();
	});
});
