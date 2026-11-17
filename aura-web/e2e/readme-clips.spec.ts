// The README's clips (T-0865): the console's own build, filmed a frame at a time while a
// scripted session plays (`make readme-clips`). The pixels are the product's; the house,
// the people and the answers are made up, here and in the mock this suite runs against.
//
// Recorded, not asserted: a clip is read by a person. What binds a clip to the README, its
// preview and its MP4 is tests/contracts/readme-clips.json (deploy/tests/test_readme_media.py).
//
// Not covered: sound (the clips are silent), the native car and phone apps (the car clip is
// the console's /car route with a stand-in for the phone's bridge, the phone clip the console
// at a phone's size), and a real model: every turn here is played by the script.
import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';

import { Film, LiveFilm, type Cut } from './film';
import { startModelStub, startSetupServer } from './setup-stage';
import { demo, micAsShipped, openShell, resetHermes } from './v2';

const FRAMES = process.env.README_CLIPS ?? '';
const ONLY = (process.env.README_CLIP ?? '').split(',').filter(Boolean);
const ROOT = new URL('../../', import.meta.url).pathname;
const TABLE = JSON.parse(readFileSync(`${ROOT}tests/contracts/readme-clips.json`, 'utf8')) as {
	clips: { name: string; width: number; height: number; preview_width: number; poster_at: number }[];
};
const EXAMPLES = (JSON.parse(readFileSync(`${ROOT}tests/contracts/projections.json`, 'utf8')) as { examples: Record<string, unknown> }).examples;

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** The console in AURA's theme, as the house wears it: the mock's persona is AURA. */
async function openAura(page: Page, path = '/'): Promise<void> {
	await page.request.post('/hermes/api/test/status', { data: { persona: 'aura' } });
	await openShell(page, path);
	await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe('aura');
}

interface Turn {
	text: string;
	send: (type: string, payload?: Record<string, unknown>) => void;
}

interface Wire {
	/** A session event put on the page's socket, as `hermes serve` broadcasts one. */
	event: (type: string, payload?: Record<string, unknown>) => void;
	/** The prompts the page has submitted and nobody has played yet. */
	taken: Turn[];
}

/**
 * The page's socket, with the film on the line. A prompt the page submits is answered
 * "streaming" and kept from the mock, whose own reply runs on a real clock and would land
 * whole in one frame; the film plays the turn instead. Everything else passes through.
 * Events are put on the socket here because a socket opened while the clock is held never
 * connects. Call before the page opens.
 */
async function wire(page: Page): Promise<Wire> {
	let socket: WebSocketRoute | undefined;
	let seq = 0;
	const taken: Turn[] = [];
	const put = (type: string, session: string, payload: Record<string, unknown>) =>
		socket?.send(JSON.stringify({ jsonrpc: '2.0', method: 'event', params: { type, session_id: session, seq: session ? ++seq : 0, payload } }));
	await page.routeWebSocket(/\/hermes\/ws$/, (ws) => {
		socket = ws;
		const server = ws.connectToServer();
		server.onMessage((m) => {
			try {
				const f = JSON.parse(String(m));
				if (typeof f?.params?.seq === 'number') seq = Math.max(seq, f.params.seq);
			} catch {
				// not ours to read
			}
			ws.send(m);
		});
		ws.onMessage((m) => {
			const f = JSON.parse(String(m));
			if (f.method !== 'prompt.submit') return server.send(m);
			const session = String(f.params.session_id);
			ws.send(JSON.stringify({ jsonrpc: '2.0', id: f.id, result: { status: 'streaming' } }));
			taken.push({ text: String(f.params.text ?? ''), send: (type, payload = {}) => put(type, session, payload) });
		});
	});
	return { event: (type, payload = {}) => put(type, '', payload), taken };
}

/** A reply said a few words at a time, as a model streams it; `beat` waits between them. */
async function stream(turn: Turn, text: string, beat: () => Promise<void>): Promise<void> {
	const words = text.split(' ');
	for (let i = 0; i < words.length; i += 2) {
		turn.send('message.delta', { text: words.slice(i, i + 2).join(' ') + (i + 2 < words.length ? ' ' : '') });
		await beat();
	}
	turn.send('message.complete', { text, status: 'complete', reasoning: '', usage: { total: 12, calls: 1 } });
}

/** A typed question played to its answer: thought, one tool, something drawn, the reply. */
async function ask(
	page: Page,
	film: Film,
	line: Wire,
	turn: { question: string; thought: string; tool: Record<string, unknown>; draw: Record<string, unknown>; reply: string }
): Promise<void> {
	await page.getByTestId('chat-input').focus();
	await film.type(turn.question);
	await film.roll(0.4);
	await page.keyboard.press('Enter');
	await film.until(() => line.taken.length > 0, 'the page submitted the question');
	const t = line.taken.shift()!;
	t.send('message.start');
	await film.roll(0.5);
	for (const word of turn.thought.split(' ')) {
		t.send('reasoning.delta', { text: word + ' ' });
		await film.roll(2 / 30);
	}
	t.send('tool.start', turn.tool);
	await film.roll(1.2);
	t.send('tool.complete', { ...turn.tool, duration: 0.4, result: 'ok' });
	await film.roll(0.3);
	line.event('hud.projection', { ...turn.draw, origin: 'agent' });
	await film.roll(0.6);
	await stream(t, turn.reply, () => film.roll(3 / 30));
}

const persona = (active: 'aura' | 'jarvis') => {
	const other = active === 'aura' ? 'jarvis' : 'aura';
	return { active, display: active.toUpperCase(), voice: active, wake_word: active, pool: [other], previous: other, changed: true, at: '' };
};

/** A made-up drive: north along a quay, then right. Open sea on any real map. */
const DRIVE = {
	from: { lat: 41.26, lon: -40.6 },
	turn: { lat: 41.264, lon: -40.6 },
	to: { lat: 41.264, lon: -40.594 },
	/** Metres a second: about thirty miles an hour. */
	speed: 13
};

interface Stage {
	page: Page;
	line: Wire;
	film: () => Promise<Film>;
	live: () => Promise<LiveFilm>;
}

const SCRIPTS: Record<string, (stage: Stage) => Promise<void>> = {
	// The nine states of the core, in the order `CORE_STATES` lists them.
	core: async ({ page, film: start }) => {
		await openAura(page);
		const film = await start();
		// Each state is named under the core while it shows: the film's caption, since the
		// console itself never prints the state's name.
		film.caption('idle');
		await film.roll(1.8);
		// The shell's names for them: `warming` is embedding, `tool` is acting.
		const shown: Record<string, string> = { warming: 'embedding', tool: 'acting' };
		for (const state of ['listening', 'speaking', 'thinking', 'warming', 'tool', 'music', 'alert', 'working']) {
			await demo(page, state);
			film.caption(shown[state] ?? state);
			await film.roll(2);
		}
		await demo(page, 'idle');
		film.caption('idle');
		await film.roll(1.4);
	},

	// The name wakes it and the next sentence is the command, through the open microphone: the
	// browser's stand-in microphone loops a sentence, and what Whisper would have heard is
	// answered here. Taken live: a microphone keeps real time.
	voice: async ({ page, line, live }) => {
		let heard = 'Aura.';
		let asked = 0;
		await page.route('**/hermes/api/audio/transcribe', async (route) => {
			asked += 1;
			await route.fulfill({ json: { ok: true, transcript: heard, speaker: null } });
		});
		await micAsShipped(page);
		await openAura(page);
		const film = await live();
		await pause(1200);
		await expect.poll(() => asked, { timeout: 30_000 }).toBeGreaterThan(0);
		heard = 'Turn on the den lamp.';
		await expect.poll(() => line.taken.length, { timeout: 30_000 }).toBeGreaterThan(0);
		// Nothing more is a command: the looping sentence would start a second turn.
		heard = '';
		const t = line.taken.shift()!;
		t.send('message.start');
		await pause(500);
		const call = { tool_id: 'call-1', name: 'ha_call_service', context: 'den lamp', args: { entity_id: 'light.den_lamp', brightness: 60 } };
		t.send('tool.start', call);
		await pause(900);
		t.send('tool.complete', { ...call, duration: 0.3, result: 'ok' });
		line.event('hud.projection', { id: 'den-lamp', type: 'device', data: { name: 'Den lamp', kind: 'light', state: 'on', value: 60, unit: '%' }, origin: 'agent' });
		await pause(400);
		await stream(t, 'The den lamp is on, at sixty percent.', () => pause(110));
		await pause(4500);
		console.log(`README clip voice: ${film.rate().toFixed(1)} pictures a second`);
	},

	// A typed question, a tool call, and an answer drawn as well as said.
	answer: async ({ page, line, film: start }) => {
		await openAura(page);
		const film = await start();
		await film.roll(0.8);
		await ask(page, film, line, {
			question: 'How much power did the house use today?',
			thought: 'The house meter and the solar inverter both keep today, hour by hour.',
			tool: { tool_id: 'call-1', name: 'ha_get_history', context: 'power today', args: { entity_id: 'sensor.house_power' } },
			draw: { id: 'power-today', type: 'line', data: EXAMPLES.line },
			reply: 'The house used 14.2 kilowatt-hours today, and the roof made a little over half of it. The evening peak was the oven.'
		});
		await film.roll(4.5);
	},

	// Something that cannot be undone waits for a held press, never a click.
	approval: async ({ page, line, film: start }) => {
		await openAura(page);
		const film = await start();
		await film.roll(1);
		line.event('approval.request', {
			request_id: 'clip-snapshots',
			command: 'proxmox_snapshot_delete vm-104',
			reason: [
				'Three snapshots on vm-104 are older than 30 days.',
				'What: Delete 3 old snapshots?',
				'Why: They are older than 30 days and the disk is 91% full',
				'Impact: Frees 61 GB · on pve01 · newest stays',
				'Undoable: no',
				'Home: yes'
			].join('\n'),
			choices: ['once', 'session', 'always', 'deny']
		});
		await film.roll(2.8);
		await film.press(page.getByTestId('approval-hold').first(), 2.8);
		await film.roll(2.2);
	},

	// "Switch to JARVIS", said from anywhere: the plugin tells every console, and each tweens.
	themes: async ({ page, line, film: start }) => {
		await openAura(page);
		const film = await start();
		await film.roll(1.5);
		line.event('persona.switch', persona('jarvis'));
		await film.roll(3.4);
		line.event('persona.switch', persona('aura'));
		await film.roll(3.4);
	},

	// The head unit: the phone reports the drive, the music, a turn of talk and a call, and the
	// page draws them. A stand-in plays the phone's bridge (`window.JarvisCar`).
	car: async ({ page, film: start }) => {
		const { from, turn, to, speed } = DRIVE;
		await page.route('**/api/route?**', (route) =>
			route.fulfill({
				contentType: 'application/json',
				body: JSON.stringify({
					routes: [
						{
							distance: 940,
							duration: 240,
							geometry: { coordinates: [from, turn, to].map((p) => [p.lon, p.lat]) },
							legs: [
								{
									steps: [
										{ maneuver: { location: [from.lon, from.lat], type: 'depart' }, name: 'Quay Street' },
										{ maneuver: { location: [turn.lon, turn.lat], type: 'turn', modifier: 'right' }, name: 'Harbour Road' },
										{ maneuver: { location: [to.lon, to.lat], type: 'arrive' }, name: '' }
									]
								}
							]
						}
					]
				})
			})
		);
		await page.route('**/api/maps/match', (route) =>
			route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...from, street: 'Quay Street', limit_mph: 30, limit_source: 'posted', road: 'residential' }) })
		);
		const music = { title: 'Night Drive', artist: 'Example Artist', app: 'Music', playing: true, position: 61, duration: 243 };
		let metres = 0;
		const at = (extra: Record<string, unknown> = {}) => ({
			place: { lat: from.lat + metres / 111_000, lon: from.lon, heading: 0, speed },
			media: { ...music, position: music.position + metres / speed },
			messages: [],
			navApps: ['organic'],
			destination: { lat: to.lat, lon: to.lon, label: 'Ober', app: '', at: 1 },
			...extra
		});
		await page.request.post('/hermes/api/test/status', { data: { persona: 'aura' } });
		await page.addInitScript((s) => {
			(window as unknown as { JarvisCar: unknown }).JarvisCar = { state: () => JSON.stringify(s), act: () => undefined };
		}, at());
		await page.goto('/car');
		await expect(page.getByTestId('screen-car')).toBeVisible({ timeout: 15_000 });
		await expect(page.getByTestId('car-route')).toBeVisible({ timeout: 10_000 });
		const film = await start();
		/** Drive on for this long, the phone reporting five times a second. */
		const drive = async (seconds: number, extra: Record<string, unknown> = {}) => {
			for (let i = 0; i < Math.round(seconds * 5); i++) {
				metres += speed / 5;
				await page.evaluate((d) => window.dispatchEvent(new CustomEvent('jarvis:car', { detail: d })), at(extra));
				await film.roll(0.2);
			}
		};
		await drive(3);
		await drive(0.6, { voice: { phase: 'listening', heard: '', answer: '', caption: 'Listening' } });
		await drive(0.6, { voice: { phase: 'listening', heard: 'How long', answer: '', caption: 'Listening' } });
		await drive(1, { voice: { phase: 'listening', heard: 'How long until we get there?', answer: '', caption: 'Listening' } });
		await drive(0.8, { voice: { phase: 'thinking', heard: 'How long until we get there?', answer: '', caption: 'Thinking' } });
		await drive(2.8, { voice: { phase: 'speaking', heard: 'How long until we get there?', answer: 'Four minutes. Turn right onto Harbour Road.', caption: 'Speaking' } });
		await drive(0.6);
		await drive(2.2, { call: { state: 'ringing', name: 'Sam Example', number: '', photo: '', since: null } });
		await drive(2.2, { call: { state: 'active', name: 'Sam Example', number: '', photo: '', since: null } });
	},

	// The phone's home is the console in a web view: the same turn, at a phone's size.
	phone: async ({ page, line, film: start }) => {
		await openAura(page);
		const film = await start();
		await film.roll(0.8);
		await ask(page, film, line, {
			question: "What's on today?",
			thought: 'The calendar has three things today.',
			tool: { tool_id: 'call-1', name: 'calendar_list', context: 'today', args: { day: 'today' } },
			draw: { id: 'today', type: 'timeline', data: EXAMPLES.timeline },
			reply: 'Stand-up at nine, then dinner with Ana at half past six. The table at Ober is held for two.'
		});
		await film.roll(4);
		await film.press(page.getByRole('button', { name: 'All apps', exact: true }));
		await film.roll(2.4);
	},

	// What AURA has been doing, and the machines she runs on, from her own records.
	ops: async ({ page, film: start }) => {
		await openAura(page);
		const film = await start();
		await film.roll(0.8);
		const launcher = page.getByTestId('launcher');
		await film.press(page.getByRole('button', { name: 'All apps', exact: true }));
		await film.roll(1.2);
		await film.press(launcher.getByText('Board', { exact: true }));
		await film.until(() => page.getByTestId('screen-board').isVisible(), 'the board opened');
		await film.roll(3);
		await page.mouse.move(900, 400);
		for (let i = 0; i < 20; i++) {
			await page.mouse.wheel(0, 24);
			await film.roll(1 / 30);
		}
		await film.roll(1.6);
		await film.press(page.getByRole('button', { name: 'All apps', exact: true }));
		await film.roll(1);
		await film.press(launcher.getByText('Ops', { exact: true }));
		await film.until(() => page.getByTestId('screen-ops').isVisible(), 'the ops screen opened');
		await film.roll(4);
	},

	// The one-line install's setup page: six questions, a review, then the install as the
	// installer reports it. The page is deploy/setup's own; the installer's side is played here.
	setup: async ({ page, film: start }) => {
		const models = await startModelStub(['house-model', 'small-model']);
		const stage = await startSetupServer({ addresses: ['192.0.2.10'], gpu: false, timezone: 'Etc/UTC' });
		try {
			await page.goto(`${stage.origin}/?t=${stage.token}`);
			await expect(page.locator('#panel')).toHaveAttribute('data-view', 'steps');
			const film = await start();
			const next = async (title: string) => {
				await film.press(page.locator('#next'));
				await film.until(() => page.locator(title).evaluate((el) => el === document.activeElement), `${title} is up`);
			};
			await film.roll(1);
			const password = ['correct', 'horse', 'battery'].join(' ');
			await page.locator('#password').focus();
			await film.type(password, 1);
			await page.locator('#password2').focus();
			await film.type(password, 1);
			await film.roll(0.4);
			await next('#t-models');
			await film.roll(0.5);
			await page.locator('#llm-url').focus();
			await film.type(models.url, 1);
			await page.locator('#llm-key').focus();
			await film.type(['key', 'for', 'the', 'stub'].join('-'), 1);
			await film.press(page.locator('#test-model'));
			await film.until(() => page.locator('#test-result').evaluate((el) => el.getAttribute('data-ok') === 'true'), 'the endpoint answered', 6);
			await film.roll(1);
			await next('#t-voice');
			await film.roll(1.1);
			await next('#t-wake');
			await film.roll(1.1);
			await next('#t-devices');
			await film.roll(0.4);
			await film.press(page.locator('input[name="device"][value="car"]'));
			await film.roll(0.9);
			await next('#t-theme');
			await film.roll(0.8);
			await film.press(page.locator('.look[data-look="jarvis"]'));
			await film.roll(1.5);
			await film.press(page.locator('.look[data-look="aura"]'));
			await film.roll(1.3);
			await next('#t-review');
			await film.roll(1.8);
			await film.press(page.locator('#next'));
			await film.until(() => page.locator('#panel').evaluate((el) => el.getAttribute('data-view') === 'install'), 'the install view is up');
			const phases = ['preflight', 'setup', 'secrets', 'n8n', 'images', 'start', 'verify', 'pair'];
			for (let i = 0; i < phases.length; i++) {
				stage.progress({ phase: phases[i], state: 'running', done: phases.slice(0, i), detail: '' });
				await film.roll(0.7);
			}
			stage.progress({ phase: 'done', state: 'done', done: phases, console: 'http://192.0.2.10:8199' });
			await film.until(() => page.locator('#done').isVisible(), 'the install is done', 6);
			await film.roll(2.5);
		} finally {
			stage.stop();
			await models.close();
		}
	}
};

// The persona goes back for the next spec, from a page whose clocks run.
test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
	await page.goto('/gallery/tokens').catch(() => undefined);
	await page.request.post('/hermes/api/test/status', { data: { persona: 'jarvis' } }).catch(() => undefined);
	await resetHermes(page).catch(() => undefined);
});

for (const clip of TABLE.clips) {
	test(`${clip.name}: filmed from the console's own build`, async ({ page }) => {
		test.skip(!FRAMES, 'README_CLIPS=<a scratch folder for frames> films the README clips (make readme-clips)');
		test.skip(ONLY.length > 0 && !ONLY.includes(clip.name), 'README_CLIP names other clips');
		const script = SCRIPTS[clip.name];
		expect(script, `a script for ${clip.name}`).toBeTruthy();
		test.setTimeout(60 * 60_000);
		await page.setViewportSize({ width: clip.width, height: clip.height });
		const line = await wire(page);
		const held: { film?: Film | LiveFilm } = {};
		const frames = `${FRAMES}/${clip.name}`;
		try {
			await script({
				page,
				line,
				film: async () => (held.film = await Film.start(page, frames)) as Film,
				live: async () => (held.film = await LiveFilm.start(page, frames)) as LiveFilm
			});
		} finally {
			// Whatever happened, the page gets its clocks back: the hooks after this open pages.
			await held.film?.stop();
		}
		expect(held.film, 'the script started its film').toBeTruthy();
		for (const dir of ['site/media', 'site/posters', 'docs/media']) mkdirSync(`${ROOT}${dir}`, { recursive: true });
		// A caption is set in the console's own label face and colour, read from the page it is
		// laid over: twice the label's size, so it survives the preview's scaling.
		const label = await page.evaluate(() => {
			const probe = document.createElement('span');
			probe.style.cssText = 'color: var(--jv-text-dim); font-family: var(--jv-font-chrome); font-size: var(--jv-fs-2xs)';
			document.body.append(probe);
			const style = getComputedStyle(probe);
			const found = { colour: style.color, family: style.fontFamily, size: parseFloat(style.fontSize) };
			probe.remove();
			return found;
		});
		const [r, g, b] = (label.colour.match(/[\d.]+/g) ?? []).map(Number);
		const captions = {
			family: label.family.split(',')[0].replace(/["']/g, '').trim(),
			fonts: `${ROOT}android-app/app/src/main/res/font`,
			colour: [r, g, b] as [number, number, number],
			size: Math.round(label.size * 2),
			lift: Math.round(clip.height / 7)
		};
		const to: Cut = { mp4: `${ROOT}site/media/${clip.name}.mp4`, webp: `${ROOT}docs/media/${clip.name}.webp`, previewWidth: clip.preview_width, captions, poster: { jpg: `${ROOT}site/posters/${clip.name}.jpg`, at: clip.poster_at } };
		held.film!.cut(to);
	});
}
