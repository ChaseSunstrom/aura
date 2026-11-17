// Helpers for the v2 shell's specs (every `e2e/v2-*.spec.ts`, the a11y four and
// hud-frametime).

import { expect, type Page } from '@playwright/test';
import { GITLAB } from '../src/lib/hud/data/apps';

/** The seven stage scenes, in the order `lib/ds/stage.ts` lists them. */
/**
 * A length as layout reads it. Chromium lays out in 1/64 px, so a target drawn at 44 px can come
 * back from boundingBox() as 43.9998 (pipeline 929: a /devices button at 390 wide failed `>= 44`
 * there and passed here). Rounding to that grid makes such a reading 44 and keeps a real
 * 43.98 px a fail. Not covered: a target that is short by a whole layout unit or more.
 */
export const layoutPx = (n: number) => Math.round(n * 64) / 64;

export const SCENES = ['idle', 'listening', 'answer', 'thinking', 'music', 'alert', 'working'] as const;
export type Scene = (typeof SCENES)[number];

/** Force a scene (`jarvis:orb-demo`); '' follows the links again. */
export const demo = (page: Page, scene: string) =>
	page.evaluate((s) => window.dispatchEvent(new CustomEvent('jarvis:orb-demo', { detail: s })), scene);

/** The shell has the scene whole and the core in place. */
export async function settledAt(page: Page, scene: string): Promise<void> {
	const shell = page.getByTestId('shell');
	await expect(shell).toHaveAttribute('data-scene', scene, { timeout: 5000 });
	await expect(shell).toHaveAttribute('data-settled', 'true', { timeout: 5000 });
}

/** Open the console and wait for the shell to be up and mounted, its listeners attached. */
export async function openShell(page: Page, path = '/'): Promise<void> {
	await page.goto(path);
	// Visible is not enough: the server-rendered shell shows before it hydrates, and a demo()
	// sent then was lost on a loaded runner (T-0831, pipeline 715).
	await expect(page.getByTestId('shell')).toHaveAttribute('data-ready', 'true', { timeout: 15_000 });
}

/**
 * Start recording every `data-scene`/`data-phase` the shell wears, so a spec
 * can say what was never shown (a turned-round exit never shows its target).
 */
export const record = (page: Page) =>
	page.evaluate(() => {
		const el = document.querySelector('[data-testid="shell"]')!;
		const w = window as unknown as { __seen: string[]; __obs?: MutationObserver };
		w.__obs?.disconnect();
		w.__seen = [`${el.getAttribute('data-scene')}:${el.getAttribute('data-phase')}`];
		w.__obs = new MutationObserver(() => {
			const now = `${el.getAttribute('data-scene')}:${el.getAttribute('data-phase')}`;
			if (w.__seen[w.__seen.length - 1] !== now) w.__seen.push(now);
		});
		w.__obs.observe(el, { attributes: true, attributeFilter: ['data-scene', 'data-phase'] });
	});

export const seen = (page: Page) => page.evaluate(() => (window as unknown as { __seen: string[] }).__seen);

/**
 * Emit session events through the mock adapter, as `hermes serve` would
 * (`test.emit` on its socket).
 */
export const emit = (page: Page, type: string, payload: Record<string, unknown>) =>
	page.evaluate(
		async ([t, body]) => {
			const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/hermes/ws`);
			await new Promise((resolve) => socket.addEventListener('open', resolve));
			socket.send(JSON.stringify({ jsonrpc: '2.0', id: 9, method: 'test.emit', params: { events: [{ type: t, payload: body }] } }));
			await new Promise((resolve) => setTimeout(resolve, 200));
			socket.close();
		},
		[type, payload] as [string, Record<string, unknown>]
	);

/** Put the mock Hermes back at its seed (`test.reset`), for a spec that changes what it seeded. */
export const resetHermes = (page: Page) =>
	page.evaluate(async () => {
		const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/hermes/ws`);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		const done = new Promise((resolve) => socket.addEventListener('message', resolve));
		socket.send(JSON.stringify({ jsonrpc: '2.0', id: 9, method: 'test.reset', params: {} }));
		await done;
		socket.close();
	});

/**
 * Start the mic the way the console ships it: open. Every context is seeded
 * muted (playwright.config.ts `storageState`) so the fake microphone's looping
 * sentence does not start turns in specs that are not about it. Once per tab,
 * so a reload finds what the console wrote and not this script's hand again.
 */
export const micAsShipped = (page: Page) =>
	page.addInitScript(() => {
		if (sessionStorage.getItem('jarvis:mic-as-shipped')) return;
		sessionStorage.setItem('jarvis:mic-as-shipped', '1');
		localStorage.removeItem('jarvis:open-mic');
	});

/** What the mock adapter has spoken, in which voices, and how much it transcribed, for its whole life. */
export const spoken = async (page: Page) =>
	(await page.evaluate(() => fetch('/hermes/api/test/spoken').then((r) => r.json()))) as {
		spoken: string[];
		streamed: string[];
		voices: string[];
		transcripts: number;
	};

/** Type a line into the core's composer and send it. */
export async function say(page: Page, text: string): Promise<void> {
	await page.getByTestId('chat-input').fill(text);
	await page.getByTestId('chat-input').press('Enter');
}

/**
 * Elements with a live `backdrop-filter` on screen. Counted from computed
 * style, so a blur that arrives through a class the tokens do not name is
 * still counted. Reactor II and v2 draw none (DECISIONS 54 for the popup).
 */
export const blurLayers = (page: Page) =>
	page.evaluate(() => {
		let n = 0;
		for (const el of document.querySelectorAll<HTMLElement>('*')) {
			const cs = getComputedStyle(el);
			const filter = cs.backdropFilter || (cs as unknown as { webkitBackdropFilter?: string }).webkitBackdropFilter || 'none';
			if (filter === 'none' || cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) continue;
			const r = el.getBoundingClientRect();
			if (r.width === 0 || r.height === 0 || r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) continue;
			n++;
		}
		return n;
	});

/**
 * GitLab's releases, as it answered on 2026-09-24, for Devices → Get the apps (ADR-0050).
 * Intercepted: what the console does with them, not that GitLab is up. Read
 * cross-origin, so without the CORS header the browser discards the reply.
 */
export const PKG = `${GITLAB}/api/v4/projects/3/packages/generic`;
export const RELEASES_ROUTE = `${GITLAB}/api/v4/projects/3/releases**`;
const RELEASES = [
	{ tag_name: 'v1.0.1395+1395', released_at: '2026-09-24T15:00:00Z', description: 'Jarvis `d99` from claude/jarvis-overhaul', assets: { links: [{ name: 'jarvis-release.apk', url: `${PKG}/jarvis-android/1395/jarvis-release.apk` }] } },
	{ tag_name: 'desktop-v1.0.1395', released_at: '2026-09-24T15:01:00Z', assets: { links: [{ name: 'Jarvis-1.0.1395.AppImage', url: `${PKG}/jarvis-desktop-app/1.0.1395/Jarvis-1.0.1395.AppImage` }] } }
];
export const releases = (page: Page) =>
	page.route(RELEASES_ROUTE, (route) => route.fulfill({ json: RELEASES, headers: { 'access-control-allow-origin': '*' } }));

/** An approval whose reason carries the five card lines (`hud/approvalCard.ts`). */
export const SNAPSHOT_APPROVAL = {
	request_id: 'e2e-snapshots',
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
};
