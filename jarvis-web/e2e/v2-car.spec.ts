// The car's display (`/car`) on the v2 shell: the core, the one card it
// projects, the next turn, the speed and what is being said, laid out as
// `docs/design/v2/Car.dc.html`. The phone's web view on the head unit loads it
// (`CarHud.kt`) and plays the phone through `window.JarvisCar`; a stand-in
// bridge plays it here.
//
// Carried from hud-car.spec.ts: what the phone reports is drawn and its answers
// go back, the notice once, a destination routed before and after the first
// fix with its turns said in time, the speed against the limit and the word
// to slow down, and the blind phone that says so with its fix. Retired with
// it: the car's panels, tabs, tiles, drag, resize and pinch (the v2 car is the
// core and one card zone, Car.dc.html), the Apps panel (the car shell's own
// launcher opens the driver's apps) and the tools opened from the wordmark.
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

import { emit, layoutPx, resetHermes, spoken } from './v2';

type Phone = Record<string, unknown>;

/** The phone's bridge, with every act it is asked kept on `window.__acts`. */
async function openCar(page: Page, state: Phone | null, size = { width: 800, height: 480 }): Promise<void> {
	await page.addInitScript((s) => {
		const acts: string[] = [];
		(window as unknown as { __acts: string[] }).__acts = acts;
		if (s) (window as unknown as { JarvisCar: unknown }).JarvisCar = { state: () => JSON.stringify(s), act: (json: string) => acts.push(json) };
	}, state);
	await page.setViewportSize(size);
	await page.goto('/car');
	await expect(page.getByTestId('screen-car')).toBeVisible({ timeout: 15_000 });
}

const acts = (page: Page) => page.evaluate(() => (window as unknown as { __acts: string[] }).__acts.map((a) => JSON.parse(a)));
const says = async (page: Page) => (await acts(page)).filter((a) => a.kind === 'say').map((a) => a.text as string);
const phone = (page: Page, detail: Phone) => page.evaluate((d) => window.dispatchEvent(new CustomEvent('jarvis:car', { detail: d })), detail);

const ROUTE = (coordinates: [number, number][], steps: unknown[]) =>
	JSON.stringify({ routes: [{ distance: 1500, duration: 180, geometry: { coordinates }, legs: [{ steps }] }] });

test('the car draws what the phone reports, one card at a time, and sends its answers back', async ({ page }) => {
	const asked: string[] = [];
	page.on('request', (r) => asked.push(r.url()));
	await openCar(page, {
		media: { title: 'Midnight City', artist: 'M83', app: 'Spotify', playing: true, position: 61, duration: 243 },
		call: { state: 'ringing', name: 'Mum', number: '+1 555', photo: '', since: null },
		messages: [{ id: 'm1', from: 'Sam', app: 'Signal', text: 'Running ten minutes late', at: 1 }],
		place: { lat: 41.8781, lon: -87.6298, heading: 90, speed: 14 },
		navApps: ['organic', 'google']
	});
	const screen = page.getByTestId('screen-car');
	await expect(screen).toHaveAttribute('data-linked', 'true');
	// No console chrome on the head unit.
	for (const name of ['All apps', 'Search or command', 'Devices']) await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
	await expect(page.getByTestId('chat-input')).toHaveCount(0);
	// The car at the middle, facing its way, and nothing asked of anywhere but the console: the
	// street tiles came from OpenStreetMap's servers, which heard where the car was (T-0765).
	await expect(page.locator('.you.moving')).toBeVisible();
	const own = new URL(page.url()).origin;
	expect(asked.filter((u) => new URL(u).origin !== own || new URL(u).pathname.startsWith('/api/tiles/'))).toEqual([]);
	// The ringing call is the card; one card only.
	await expect(page.getByTestId('car-card')).toHaveCount(1);
	await expect(page.getByTestId('car-card')).toContainText('Mum');
	await page.getByTestId('car-answer').click();
	expect(await acts(page)).toContainEqual({ kind: 'call', op: 'answer' });
	// The call over, what is playing takes the card.
	await phone(page, { media: { title: 'Midnight City', artist: 'M83', app: 'Spotify', playing: true, position: 62, duration: 243 }, messages: [{ id: 'm1', from: 'Sam', app: 'Signal', text: 'Running ten minutes late', at: 1 }], place: { lat: 41.8781, lon: -87.6298, heading: 90, speed: 14 }, navApps: [] });
	await expect(page.getByTestId('car-media')).toContainText('Midnight City');
	await page.getByTestId('car-play').click();
	expect(await acts(page)).toContainEqual({ kind: 'media', op: 'pause' });
	// No music: the newest message, readable without a tap.
	await phone(page, { media: null, messages: [{ id: 'm1', from: 'Sam', app: 'Signal', text: 'Running ten minutes late', at: 1 }], navApps: [] });
	await expect(page.getByTestId('car-message')).toContainText('Running ten minutes late');
	await expect(page.getByTestId('car-message')).toContainText('Sam');
	// The core is the car's talk button: a press asks the phone for a turn on the head
	// unit's microphone, and the turn's phase is what the core wears.
	await page.getByTestId('car-talk').click();
	expect(await acts(page)).toContainEqual({ kind: 'talk' });
	await phone(page, { voice: { phase: 'listening', heard: 'route me home', answer: '', caption: 'Listening' } });
	await expect(screen).toHaveAttribute('data-reactor', 'listening');
	await expect(page.getByTestId('car-heard')).toContainText('route me home');
	await expect(page.getByTestId('car-said')).toContainText('Listening');
	await phone(page, { voice: { phase: 'idle' } });
	await expect(screen).not.toHaveAttribute('data-reactor', 'listening');
});

// T-0760 (the owner, 2026-11-08: "have it popup in a nice/cool way as the phone call + name + pfp").
test('a call shows who it is: their picture or initials, and how long it has run', async ({ page }) => {
	const PHOTO = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
	const place = { lat: 41.8781, lon: -87.6298, heading: 90, speed: 14 };
	await openCar(page, { call: { state: 'ringing', name: 'Sam Example', number: '', photo: '', since: null }, messages: [], place, navApps: [] });
	await expect(page.getByTestId('car-call-initials')).toHaveText('SE');
	await expect(page.getByTestId('car-call-time')).toHaveCount(0);
	await expect(page.getByTestId('car-hangup')).toHaveText(/Decline/i);
	await page.waitForTimeout(1500); // the card's way out of the core, so the picture is of the card
	await page.screenshot({ path: 'test-results/car-call-ringing-800x480.png' });
	// Answered: the picture the phone sent, and the time counting from when it began.
	await phone(page, { call: { state: 'active', name: 'Sam Example', number: '', photo: PHOTO, since: Date.now() - 65_000 }, messages: [], place, navApps: [] });
	await expect(page.getByTestId('car-call-photo')).toBeVisible();
	await expect(page.getByTestId('car-call-time')).toHaveText(/^1:0[5-9]$/);
	await expect(page.getByTestId('car-answer')).toHaveCount(0);
	const card = (await page.getByTestId('car-card').boundingBox())!;
	const core = (await page.getByTestId('car-talk').boundingBox())!;
	expect(card.y + card.height, 'the card ends above the core').toBeLessThanOrEqual(core.y);
	await page.screenshot({ path: 'test-results/car-call-active-800x480.png' });
	// A photo that is not an image in the state itself is never fetched: initials instead.
	await phone(page, { call: { state: 'active', name: 'Sam Example', number: '', photo: 'https://example.com/a.jpg', since: null }, messages: [], place, navApps: [] });
	await expect(page.getByTestId('car-call-initials')).toBeVisible();
	await expect(page.getByTestId('car-call-time')).toHaveCount(0);
	await page.getByTestId('car-hangup').click();
	expect(await acts(page)).toContainEqual({ kind: 'call', op: 'hangup' });
});

test('a waiting request is the card, and outranks the call', async ({ page }) => {
	await openCar(page, { call: { state: 'active', name: 'Mum', number: '', photo: '', since: null }, ask: { id: 'a1', title: 'Unlock the front door?', detail: 'Casey is at the door' }, navApps: [] });
	const ask = page.getByTestId('car-ask');
	await expect(ask).toContainText('Unlock the front door?');
	await expect(ask).toContainText('Casey is at the door');
	await page.getByTestId('car-ask-deny').click();
	expect(await acts(page)).toContainEqual({ kind: 'answer', id: 'a1', ok: false });
	await phone(page, { call: { state: 'active', name: 'Mum', number: '', photo: '', since: null }, navApps: [] });
	await expect(page.getByTestId('car-card')).toContainText('On a call');
	await expect(page.getByTestId('car-hangup')).toHaveText('Hang up');
});

// The owner, 2026-09-25: "fix the weird notification pop up in the car hud". The phone
// hands the notice to the page, which draws its own card, once. T-0754, the owner: "the
// notification goes under the music box and shows the last notification", not in its place.
test('a notice from the phone sits under the music, and once', async ({ page }) => {
	const media = { title: 'Night Drive', artist: 'Example Artist', app: 'Spotify', playing: true, position: 10, duration: 200 };
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await openCar(page, { media, navApps: [] });
	const notice = { media, navApps: [], notice: { id: 'n1', app: 'Gmail', title: 'Invoice', text: 'Due Friday', at: 1 } };
	await phone(page, notice);
	const card = page.getByTestId('car-notice');
	await expect(card).toContainText('Gmail · Invoice');
	await expect(card).toContainText('Due Friday');
	await expect(page.getByTestId('car-media'), 'the music stays').toBeVisible();
	const music = (await page.getByTestId('car-card').boundingBox())!;
	const under = (await page.getByTestId('car-notice-card').boundingBox())!;
	expect(under.y, 'the notice is below the music').toBeGreaterThanOrEqual(music.y + music.height);
	expect(Math.abs(under.x + under.width - (music.x + music.width)), 'right edges line up').toBeLessThan(2);
	const core = (await page.getByTestId('car-talk').boundingBox())!;
	expect(under.y + under.height, 'and it ends above the core, on an 800x480 head unit too').toBeLessThanOrEqual(core.y);
	// The owner, 2026-11-10: the cards' star read as a triangle. Centred on the corner, the card's
	// scroll box clipped three quarters of it; it sits inside the corner now, whole.
	await page.evaluate(() => (document.documentElement.dataset.marker = 'star'));
	for (const id of ['car-card', 'car-notice-card']) {
		const star = await page.getByTestId(id).locator('.body').evaluate((b) => {
			const a = getComputedStyle(b, '::after');
			return { clip: a.clipPath.startsWith('polygon'), translate: a.translate, left: a.left, top: a.top };
		});
		expect(star, id).toEqual({ clip: true, translate: 'none', left: '0px', top: '0px' });
	}
	// Closed, it stays closed while the phone keeps sending the same newest notice.
	await page.getByTestId('car-notice-close').click();
	await expect(card).toHaveCount(0);
	await phone(page, notice);
	await page.waitForTimeout(300);
	await expect(card).toHaveCount(0);
	// A reconnect reloads the page; the notice it had shown does not come back (1447).
	await page.reload();
	await expect(page.getByTestId('screen-car')).toBeVisible({ timeout: 15_000 });
	await phone(page, notice);
	await page.waitForTimeout(300);
	await expect(card).toHaveCount(0);
});

// "Route me to Casey's" (2026-10-07) went to the car page and nothing showed or was said:
// a destination that lands before the first position fix was keyed and dropped.
test('a destination sent before the first position fix is routed when the fix arrives', async ({ page }) => {
	await page.route('**/api/route?**', (route) =>
		route.fulfill({
			contentType: 'application/json',
			body: ROUTE([[-40.6, 41.26], [-40.59, 41.27]], [
				{ maneuver: { location: [-40.6, 41.26], type: 'depart' }, name: '' },
				{ maneuver: { location: [-40.59, 41.27], type: 'arrive' }, name: '' }
			])
		})
	);
	const destination = { lat: 41.27, lon: -40.59, label: "Casey's", app: '', at: 1 };
	await openCar(page, { place: null, navApps: [], destination });
	await page.waitForTimeout(1000);
	await phone(page, { place: { lat: 41.26, lon: -40.6, heading: 0, speed: 0 }, navApps: [], destination });
	await expect(page.getByTestId('car-route')).toBeVisible({ timeout: 10_000 });
	await expect.poll(() => says(page)).toContainEqual(expect.stringMatching(/^Route to Casey's/));
});

// T-1214: a route the house refused as wrong (a bad via, 400) is told on the notice line and
// not drawn from the phone's packs (the fall-back for a 502 is pinned in route.test.ts).
test('a route the house refused says why on the notice line and draws nothing', async ({ page }) => {
	await page.route('**/api/route?**', (route) =>
		route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ message: 'via is up to five "lat,lon"' }) })
	);
	const destination = { lat: 41.27, lon: -40.59, label: "Casey's", app: '', at: 1 };
	await openCar(page, { place: { lat: 41.26, lon: -40.6, heading: 0, speed: 0 }, navApps: [], destination });
	await expect(page.getByTestId('car-notice')).toContainText('via is up to five');
	await expect(page.getByTestId('car-route')).toHaveCount(0);
	await expect.poll(() => says(page)).toContainEqual(expect.stringMatching(/^I could not route to Casey's: via is up to five/));
});

// The owner, 2026-09-25: "it doesn't update the map on the car HUD", and the turns said
// aloud, in time.
test('a destination is routed on the car map, its next turn is up top, and it is said before the car reaches it', async ({ page }) => {
	await page.route('**/api/route?**', (route) =>
		route.fulfill({
			contentType: 'application/json',
			body: ROUTE([[-40.6, 41.26], [-40.6, 41.2615], [-40.59, 41.27]], [
				{ maneuver: { location: [-40.6, 41.26], type: 'depart' }, name: '' },
				{ maneuver: { location: [-40.6, 41.2615], type: 'turn', modifier: 'left' }, name: 'Main Street' },
				{ maneuver: { location: [-40.59, 41.27], type: 'arrive' }, name: '' }
			])
		})
	);
	const state = { place: { lat: 41.26, lon: -40.6, heading: 0, speed: 20 }, navApps: ['google'], destination: { lat: 41.27, lon: -40.59, label: 'Hy-Vee', app: '', at: 1 } };
	await openCar(page, state);
	await expect(page.getByTestId('car-route')).toBeVisible({ timeout: 10_000 });
	// 167 m out at 20 m/s is inside seven seconds plus the speech lead: said now, once.
	await phone(page, state);
	await expect(page.getByTestId('car-next')).toContainText('Turn left onto Main Street');
	await expect(page.getByTestId('car-next')).toContainText(/\d+ m/);
	await expect.poll(() => says(page)).toContain('Turn left onto Main Street');
	expect((await says(page)).some((t) => t.startsWith('Route to Hy-Vee'))).toBe(true);
	// Where the route ends, and when, at the top right.
	await expect(page.getByTestId('car-arrive')).toContainText('Hy-Vee');
	await expect(page.getByTestId('car-arrive')).toContainText(/\d:\d\d/);
});

// T-0741, T-0747: the car showed this page's flat street map, moved once a phone state. A
// phone that draws the hologram drive (`access.ground`, tests/contracts/car-state.json) is
// asked for it and handed the route; the page draws no map and is see-through to it.
test('over the phone\'s hologram ground the page draws no map, asks for the ground and hands it the route', async ({ page }) => {
	await page.route('**/api/route?**', (route) =>
		route.fulfill({
			contentType: 'application/json',
			body: ROUTE([[-40.6, 41.26], [-40.6, 41.2615], [-40.59, 41.27]], [
				{ maneuver: { location: [-40.6, 41.26], type: 'depart' }, name: 'Quarry Road' },
				{ maneuver: { location: [-40.6, 41.2615], type: 'turn', modifier: 'left' }, name: 'Main Street' },
				{ maneuver: { location: [-40.59, 41.27], type: 'arrive' }, name: '' }
			])
		})
	);
	const state = {
		place: { lat: 41.26, lon: -40.6, heading: 0, speed: 20 },
		navApps: ['google'],
		destination: { lat: 41.27, lon: -40.59, label: 'Hy-Vee', app: '', at: 1 },
		access: { notifications: true, capture: true, ground: true }
	};
	// Under AURA too: its starfield was drawn over the phone's map, so the car showed no map (T-0976).
	await page.addInitScript(() => localStorage.setItem('jv.theme', 'aura'));
	await openCar(page, state);
	await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('aura');
	await expect(page.getByTestId('car-map')).toHaveAttribute('data-ground', 'true');
	await expect(page.getByTestId('starfield'), 'no sky over the map').toHaveCount(0);
	await expect.poll(async () => (await acts(page)).filter((a) => a.kind === 'ground')).toEqual([{ kind: 'ground', on: true }]);
	// The route and its next turn, handed over once the turn is known.
	await phone(page, state);
	await expect(page.getByTestId('car-next')).toContainText('Turn left onto Main Street');
	await expect
		.poll(async () => (await acts(page)).filter((a) => a.kind === 'route').at(-1))
		.toMatchObject({ kind: 'route', points: [[41.26, -40.6], [41.2615, -40.6], [41.27, -40.59]], turn: { lat: 41.2615, lon: -40.6, street: 'Main Street' } });
	// No map of its own: no route line, no marker, nothing painted under the zones.
	await expect(page.getByTestId('car-route')).toHaveCount(0);
	await expect(page.locator('.you')).toHaveCount(0);
	for (const sel of ['html', 'body', '[data-testid="shell"]', '[data-testid="screen-car"]']) {
		const paint = await page.locator(sel).evaluate((e) => `${getComputedStyle(e).backgroundImage} ${getComputedStyle(e).backgroundColor}`);
		expect(paint, sel).toBe('none rgba(0, 0, 0, 0)');
	}
});

// The house's map-match of the car's last fixes (jarvis-core integrations/maps, T-0755).
// The owner, 2026-11-09: "the music is still at the bottom and not under the time/speed". The
// card (music, a notice, a call) is the right column's second line: under the time and speed,
// its right edge on theirs, and never over the core, on each head unit the car is drawn for.
test('the card sits directly under the time and speed, clear of the core', async ({ page }) => {
	const media = { title: 'Night Drive', artist: 'Example Artist', app: 'Spotify', playing: true, position: 10, duration: 200 };
	// Measured where it lands: under reduced motion a projection is there at once, not mid-unroll.
	await page.emulateMedia({ reducedMotion: 'reduce' });
	for (const size of [{ width: 800, height: 480 }, { width: 1280, height: 720 }, { width: 1920, height: 720 }]) {
		await openCar(page, { place: { lat: 41.2, lon: -40.6, heading: 0, speed: 12 }, media, navApps: [] }, size);
		// The time-and-speed projection itself, the right column's first line.
		const speed = await page.locator('.top > *').first().boundingBox();
		const card = await page.getByTestId('car-card').boundingBox();
		const core = await page.getByTestId('car-talk').boundingBox();
		expect(speed && card && core, `${size.width}x${size.height}: speed, card and core drawn`).toBeTruthy();
		const at = `${size.width}x${size.height}`;
		expect(card!.y, `${at}: the card starts below the speed`).toBeGreaterThanOrEqual(speed!.y + speed!.height);
		expect(card!.y - (speed!.y + speed!.height), `${at}: directly below, not at the bottom`).toBeLessThan(size.height / 8);
		expect(Math.abs(card!.x + card!.width - (speed!.x + speed!.width)), `${at}: right edges line up`).toBeLessThan(2);
		expect(card!.y + card!.height, `${at}: the card ends above the core`).toBeLessThanOrEqual(core!.y);
		// The controls sit in the middle of the card, not at its left (the owner, 2026-11-10, T-0991).
		const first = (await page.getByTestId('car-previous').boundingBox())!;
		const last = (await page.getByTestId('car-next-track').boundingBox())!;
		const mid = (first.x + last.x + last.width) / 2;
		expect(Math.abs(mid - (card!.x + card!.width / 2)), `${at}: the controls are centred`).toBeLessThan(4);
		await page.screenshot({ path: `test-results/car-card-under-speed-${at}.png` });
	}
});

// The owner, 2026-11-10: "why are the panels scroll panels, why can't they both entirely fit, same
// with the speed one, can you make them all horizontally the same size". At 800x480 the time and
// speed, the music and a notice are one width, each drawn whole (no scroll box), above the core.
test('the speed, the music and a notice are one width and fit above the core without scrolling', async ({ page }) => {
	const media = { title: 'Night Drive Through the City Lights', artist: 'Example Artist', app: 'Spotify', playing: true, position: 10, duration: 200 };
	await page.emulateMedia({ reducedMotion: 'reduce' });
	const state = { place: { lat: 41.2, lon: -40.6, heading: 0, speed: 12 }, media, navApps: [] };
	await openCar(page, state);
	await phone(page, { ...state, notice: { id: 'n1', app: 'Gmail', title: 'Invoice', text: 'Due Friday', at: 1 } });
	await expect(page.getByTestId('car-notice')).toBeVisible();
	const column = await page.locator('.top > *').evaluateAll((els) =>
		els.map((e) => ({ width: Math.round(e.getBoundingClientRect().width), bottom: e.getBoundingClientRect().bottom, scrolls: [e, ...e.querySelectorAll('*')].some((x) => ['auto', 'scroll'].includes(getComputedStyle(x).overflowY) && x.scrollHeight > x.clientHeight + 1) }))
	);
	expect(column, 'the speed, the music and the notice').toHaveLength(3);
	expect(new Set(column.map((c) => c.width)).size, `one width: ${column.map((c) => c.width)}`).toBe(1);
	expect(column.filter((c) => c.scrolls), 'nothing scrolls').toEqual([]);
	// The owner, 2026-11-10 (T-0998): "the panels on the side are slightly too wide".
	expect(column[0].width, 'at most 31% of the screen').toBeLessThanOrEqual(Math.round(page.viewportSize()!.width * 0.31));
	const core = (await page.getByTestId('car-talk').boundingBox())!;
	await page.screenshot({ path: 'test-results/car-column-800x480.png' });
	expect(Math.max(...column.map((c) => c.bottom)), 'all above the core').toBeLessThanOrEqual(core.y);
});

// T-0785, the owner, 2026-11-09: "have the ability for jarvis to launch any kind of panel or
// whatever just like on the web console". The car page drew no panel and no chart at all:
// what Jarvis launches is the card now, under a call or an OK prompt, above a notice and the music.
test('Jarvis launches a chart or a panel as the car\'s card, under a call, over the music', async ({ page }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	const media = { title: 'Night Drive', artist: 'Example Artist', app: 'Spotify', playing: true, position: 10, duration: 200 };
	const chart = {
		title: 'Next 6 hours',
		unit: '°F',
		x: ['1 PM', '2 PM', '3 PM', '4 PM', '5 PM', '6 PM'],
		series: [{ name: 'Temperature', points: [51, 53, 54, 52, 49, 46], tone: 'accent' }]
	};
	for (const size of [{ width: 800, height: 480 }, { width: 1280, height: 720 }]) {
		const at = `${size.width}x${size.height}`;
		await openCar(page, { media, navApps: [] }, size);
		await expect(page.getByTestId('car-media')).toBeVisible();
		await emit(page, 'hud.projection', { id: `weather-${at}`, type: 'line', data: chart, origin: 'agent' });
		const launched = page.getByTestId('car-launched');
		await expect(launched.locator('[data-state="ready"][data-type="line"]')).toHaveCount(1);
		await expect(launched).toContainText('Next 6 hours');
		await expect(page.getByTestId('car-media')).toHaveCount(0);
		const card = (await launched.boundingBox())!;
		const core = (await page.getByTestId('car-talk').boundingBox())!;
		expect(card.x + card.width, `${at}: on screen`).toBeLessThanOrEqual(size.width);
		expect(card.y + card.height, `${at}: above the core`).toBeLessThanOrEqual(core.y);
		await page.screenshot({ path: `test-results/car-launched-chart-${at}.png` });
		// A panel shown after it is the newer launch, so it is the card.
		await emit(page, 'hud.panel.show', { id: `eta-${at}`, kind: 'text', title: 'Arrival', payload: { markdown: 'Home at 6:40, 12 minutes of traffic on the way.' }, rev: 1, origin: 'agent' });
		await expect(page.getByTestId('car-panel')).toContainText('Arrival');
		await expect(page.getByTestId('car-panel')).toContainText('Home at 6:40');
		await page.screenshot({ path: `test-results/car-launched-panel-${at}.png` });
		// Dismissed, the chart before it comes back, then the music.
		await page.getByTestId('car-launched-close').click();
		await expect(page.getByTestId('car-panel')).toHaveCount(0);
		await expect(launched).toContainText('Next 6 hours');
		await page.getByTestId('car-launched-close').click();
		await expect(page.getByTestId('car-media')).toBeVisible();
		// A call outranks anything launched.
		await emit(page, 'hud.panel.show', { id: `later-${at}`, kind: 'text', title: 'Later', payload: { markdown: 'x' }, rev: 1, origin: 'agent' });
		await expect(page.getByTestId('car-panel')).toContainText('Later');
		await phone(page, { media, call: { state: 'ringing', name: 'Sam', number: '', photo: '', since: null }, navApps: [] });
		await expect(page.getByTestId('car-answer')).toBeVisible();
		await expect(page.getByTestId('car-panel')).toHaveCount(0);
	}
});

const MATCH_55 = { lat: 41.2, lon: -40.6, street: 'Route 9', limit_mph: 55, limit_source: 'posted', road: 'trunk' };

// The owner, 2026-09-25: how fast, against the road's own limit, in words from safe to
// deadly, and Jarvis telling him to slow down.
test('the speed reads the road limit, says how far over, and Jarvis says to slow down', async ({ page }) => {
	await page.route('**/api/maps/match', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(MATCH_55) }));
	await openCar(page, { place: { lat: 41.2, lon: -40.6, heading: 0, speed: 35 }, navApps: [] });
	for (let i = 0; i < 3; i++) {
		await phone(page, { place: { lat: 41.2, lon: -40.6 + i * 0.00001, heading: 0, speed: 35 }, navApps: [] });
		await page.waitForTimeout(300);
	}
	const speed = page.getByTestId('car-speed');
	await expect(speed).toHaveAttribute('data-band', 'deadly', { timeout: 10_000 });
	await expect(page.getByTestId('car-mph')).toContainText('78');
	await expect(page.getByTestId('car-band')).toContainText('Deadly · 23 over');
	// The car's own speedometer wins over the phone's GPS, as it is, even with no fix.
	// 22.35 m/s is 50 mph.
	await phone(page, { place: null, carSpeed: 22.35, navApps: [] });
	await expect(page.getByTestId('car-mph')).toContainText('50');
	await expect(speed).toHaveAttribute('data-band', 'safe');
	await expect.poll(() => says(page)).toEqual(expect.arrayContaining([expect.stringMatching(/slow down/i)]));
});

// T-0755: "when it does have it, it's completely wrong". A failed ask left the last limit
// standing for the rest of the drive; now it holds only near where it was asked, and the limit
// is asked of the house with the car's fixes, never of a public service.
test('the limit is the house answer for where the car is, and goes when the car has left it', async ({ page }) => {
	const asked: unknown[] = [];
	let up = true;
	await page.route('**/api/maps/match', (route) => {
		asked.push(route.request().postDataJSON());
		return up ? route.fulfill({ contentType: 'application/json', body: JSON.stringify(MATCH_55) }) : route.fulfill({ status: 502, body: '{}' });
	});
	await openCar(page, { place: { lat: 41.2, lon: -40.6, heading: 0, speed: 20 }, navApps: [] });
	await phone(page, { place: { lat: 41.2002, lon: -40.6, heading: 0, speed: 20 }, navApps: [] });
	await expect(page.getByTestId('car-band')).toContainText('limit 55', { timeout: 10_000 });
	expect((asked[0] as { points: number[][] }).points[0]).toEqual([41.2, -40.6]);
	up = false;
	// About 450 m on, the ask fails: the old road's 55 is not shown as this road's.
	await phone(page, { place: { lat: 41.2042, lon: -40.6, heading: 0, speed: 20 }, navApps: [] });
	await expect(page.getByTestId('car-band')).toContainText('No limit known here', { timeout: 10_000 });
});

// T-0817: the limit was drawn only at the next fix after the house answered, so an answer that
// landed after the car's last fix left "No limit known here" up (pipeline 710's hud-overlap).
test('the limit shows as soon as the house answers, not at the next fix', async ({ page }) => {
	let release!: () => void;
	const held = new Promise<void>((r) => (release = r));
	await page.route('**/api/maps/match', async (route) => {
		await held;
		await route.fulfill({ contentType: 'application/json', body: JSON.stringify(MATCH_55) });
	});
	await openCar(page, { place: { lat: 41.2, lon: -40.6, heading: 0, speed: 20 }, navApps: [] });
	await expect(page.getByTestId('car-band')).toContainText('No limit known here');
	release();
	await expect(page.getByTestId('car-band')).toContainText('limit 55');
});

test.describe("in the phone's car web view", () => {
	test.use({ userAgent: 'Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 JarvisCar' });

	// "I have to screenshare for audio" (the owner, 2026-11-08): the page's own audio reaches the
	// car only through the screen-share grant; the phone's car voice needs none (T-0743).
	test("a line JARVIS says on purpose goes to the phone's car voice, not the page's audio", async ({ page }) => {
		await openCar(page, { place: null, navApps: [] });
		const before = (await spoken(page)).spoken.length;
		await emit(page, 'hud.say', { text: 'Fuel is low. The next exit has a station.', voice: 'jarvis' });
		await expect.poll(() => says(page), { timeout: 10_000 }).toContain('Fuel is low. The next exit has a station.');
		await page.waitForTimeout(1_000);
		expect((await spoken(page)).spoken.length, 'the page played nothing itself').toBe(before);
	});
});

// "my music/apps/everything isn't showing on the car hud" (the owner, 2026-10-09): the
// phone had no notification access, so it sent no music, and the car said "Nothing playing"
// over a song (T-0326, tests/contracts/car-state.json).
test('a phone that cannot see says so where the music would be, with its fix', async ({ page }) => {
	await openCar(page, { media: null, messages: [], navApps: ['google'], access: { notifications: false, capture: false } });
	const blind = page.getByTestId('car-blind');
	await expect(blind).toContainText("can't see what's playing");
	await expect(page.getByTestId('car-card')).not.toContainText('Nothing playing');
	const contract = JSON.parse(readFileSync(new URL('../../tests/contracts/car-state.json', import.meta.url), 'utf8'));
	await page.getByTestId('car-grant').click();
	expect(await acts(page)).toContainEqual(contract.acts.access);
});

test('a browser at /car says it is the car\'s screen, and the core asks nothing', async ({ page }) => {
	await openCar(page, null);
	await expect(page.getByTestId('screen-car')).toHaveAttribute('data-linked', 'false');
	await expect(page.getByTestId('car-unlinked')).toBeVisible();
	await expect(page.getByTestId('car-talk')).toBeDisabled();
});

// Glanceable (the brief: "nothing in it may need a tap to read"): on a head unit and on
// the board's own 1440x600, every zone is on the screen, none covers another, and every
// control is a car-sized target.
for (const size of [{ width: 800, height: 480 }, { width: 1440, height: 600 }]) {
	test(`at ${size.width}x${size.height} the zones are all on screen and apart`, async ({ page }) => {
		await page.route('**/api/route?**', (route) =>
			route.fulfill({
				contentType: 'application/json',
				body: ROUTE([[-40.6, 41.26], [-40.6, 41.2615], [-40.59, 41.27]], [
					{ maneuver: { location: [-40.6, 41.26], type: 'depart' }, name: '' },
					{ maneuver: { location: [-40.6, 41.2615], type: 'turn', modifier: 'right' }, name: 'Quarry Road' },
					{ maneuver: { location: [-40.59, 41.27], type: 'arrive' }, name: '' }
				])
			})
		);
		const state = {
			media: { title: 'Night Drive', artist: 'Example Artist', app: 'Spotify', playing: true, position: 10, duration: 200 },
			place: { lat: 41.26, lon: -40.6, heading: 0, speed: 0 },
			carSpeed: 18.8,
			navApps: [],
			destination: { lat: 41.27, lon: -40.59, label: 'Home', app: '', at: 1 },
			voice: { phase: 'speaking', heard: 'take me home', answer: 'Route to Home: three minutes.', caption: '' }
		};
		await openCar(page, state, size);
		await phone(page, state);
		await expect(page.getByTestId('car-next')).toBeVisible({ timeout: 10_000 });
		// Settled: no projection mid-entrance.
		await page.waitForFunction(() => document.getAnimations().every((a) => a.effect?.getTiming().iterations === Infinity || a.playState === 'finished'));
		const zones = ['car-next', 'car-speed', 'car-card', 'car-said', 'car-talk'];
		const boxes = await Promise.all(zones.map(async (z) => [z, (await page.getByTestId(z).boundingBox())!] as const));
		for (const [z, b] of boxes) {
			expect(b, z).toBeTruthy();
			expect(b.x, z).toBeGreaterThanOrEqual(0);
			expect(b.y, z).toBeGreaterThanOrEqual(0);
			expect(b.x + b.width, z).toBeLessThanOrEqual(size.width + 1);
			expect(b.y + b.height, z).toBeLessThanOrEqual(size.height + 1);
		}
		const apart = (a: { x: number; y: number; width: number; height: number }, b: typeof a) =>
			a.x + a.width <= b.x + 1 || b.x + b.width <= a.x + 1 || a.y + a.height <= b.y + 1 || b.y + b.height <= a.y + 1;
		for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) expect(apart(boxes[i][1], boxes[j][1]), `${boxes[i][0]} / ${boxes[j][0]}`).toBe(true);
		for (const b of await page.getByTestId('screen-car').getByRole('button').all()) {
			const box = (await b.boundingBox())!;
			expect(layoutPx(box.width)).toBeGreaterThanOrEqual(44);
			expect(layoutPx(box.height)).toBeGreaterThanOrEqual(44);
		}
		await page.screenshot({ path: `test-results/v2-car-${size.width}x${size.height}.png` });
	});
}

test('the clock shows the time, and is gone when the clock setting is off', async ({ page }) => {
	await openCar(page, { media: null });
	await expect(page.getByTestId('car-clock')).toHaveText(/\d{1,2}:\d{2}/);
	// Turned off the way Jarvis or Settings would: a proposal, then its yes (`jsettings`).
	const applied = await page.evaluate(async () => {
		const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/hermes/ws`);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		let id = 20;
		const call = (method: string, params: Record<string, unknown>) =>
			new Promise<Record<string, unknown>>((resolve) => {
				const mine = ++id;
				const heard = (m: MessageEvent) => {
					const frame = JSON.parse(String(m.data));
					if (frame.id !== mine) return;
					socket.removeEventListener('message', heard);
					resolve(frame.result ?? {});
				};
				socket.addEventListener('message', heard);
				socket.send(JSON.stringify({ jsonrpc: '2.0', id: mine, method, params }));
			});
		// The command is refused without a live session, so this socket opens its own.
		const sid = String((await call('session.create', {})).session_id);
		const ask = async (words: string) =>
			JSON.parse(String((await call('command.dispatch', { session_id: sid, name: 'jsettings', arg: `@${sid} ${words}` })).output ?? '{}'));
		const proposed = await ask('set hud.car_clock "off"');
		const done = await ask(`apply ${proposed.proposal.id}`);
		socket.close();
		return done.value;
	});
	expect(applied).toBe('off');
	try {
		await page.reload();
		await expect(page.getByTestId('screen-car')).toBeVisible({ timeout: 15_000 });
		// The rest of the bar is drawn, so the clock is absent and not just late.
		await expect(page.getByTestId('car-clock')).toHaveCount(0);
	} finally {
		// The mock keeps what was applied: the next spec must find the clock on.
		await resetHermes(page);
	}
});

