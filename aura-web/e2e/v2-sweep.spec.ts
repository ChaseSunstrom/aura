// The console's pictures (T-0611): every v2 screen, and the states the mocks can
// drive, at a phone (390), a tablet (768) and a desktop (1440) width, written to
// docs/screens/v2/console/<screen>-<state>-<width>.png. `make sweep` runs it
// (V2_SCREENS=1); a bare run skips it, because it rewrites committed pictures.
//
// It asserts only that each picture is of what its name says (the screen is up,
// the state is showing): what each state does is the other v2 specs' job. Not
// covered: loading, which the mocks answer too fast to photograph honestly, and
// the car with a phone attached (`e2e/v2-car.spec.ts` drives that bridge).
import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { APPS } from '../src/lib/ds/shell/routes';
import { GITLAB } from '../src/lib/hud/data/apps';
import { SNAPSHOT_APPROVAL, demo, emit, openShell, releases, settledAt } from './v2';

const OUT = new URL('../../docs/screens/v2/console/', import.meta.url).pathname;
const WIDTHS = [
	[390, 844],
	[768, 1024],
	[1440, 900]
] as const;

/** The adapter away: the Hermes socket closes on every connect. */
const away = (page: Page) => page.routeWebSocket(/\/hermes\/ws/, (ws) => ws.close());

const scene = (name: string) => async (page: Page) => {
	await openShell(page);
	await settledAt(page, 'idle');
	await demo(page, name);
	await settledAt(page, name);
};

const SHOTS: Record<string, (page: Page) => Promise<void>> = {
	'core-idle': scene('idle'),
	'core-listening': scene('listening'),
	'core-answer': scene('answer'),
	// Thinking and working from Hermes's own frames, not the demo override: the
	// override sets the scene with no calls, so the pictures showed a bare core.
	'core-thinking': async (page) => {
		await openShell(page);
		await settledAt(page, 'idle');
		await emit(page, 'tool.start', { tool_id: 't1', name: 'web_search', context: 'restaurants near me', args: { query: 'restaurants near me' } });
		await emit(page, 'tool.complete', { tool_id: 't1', duration: 1.2 });
		await emit(page, 'tool.start', { tool_id: 't2', name: 'web_extract', context: 'the menu', args: { url: 'https://example.org/menu' } });
		await settledAt(page, 'thinking');
		await expect(page.getByTestId('stage-steps').locator('li.step')).toHaveCount(2);
	},
	'core-working': async (page) => {
		await openShell(page);
		await settledAt(page, 'idle');
		await emit(page, 'status.update', { kind: 'goal', text: '⊙ Goal set: Book a table for two (20-turn budget)' });
		await emit(page, 'tool.start', { tool_id: 'w1', name: 'web_search', context: 'restaurants near me', args: { query: 'restaurants near me' } });
		await settledAt(page, 'working');
		await expect(page.getByTestId('presence-strip')).toBeVisible();
	},
	'core-approval': async (page) => {
		await openShell(page);
		await settledAt(page, 'idle');
		await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
		await settledAt(page, 'alert');
	},
	'core-offline': async (page) => {
		await away(page);
		await openShell(page);
		await expect(page.getByTestId('shell')).toHaveAttribute('data-reactor', 'offline', { timeout: 10_000 });
	},
	'launcher-open': async (page) => {
		await openShell(page);
		await page.getByRole('button', { name: 'All apps' }).click();
		await expect(page.getByTestId('launcher')).toBeVisible();
	},
	'palette-open': async (page) => {
		await openShell(page);
		await page.getByRole('button', { name: 'Search or command' }).click();
		await expect(page.getByTestId('palette')).toBeVisible();
		await page.keyboard.type('set');
	},
	...Object.fromEntries(
		APPS.map((app) => [
			`${app.screen}-${app.screen === 'approvals' ? 'empty' : 'ready'}`,
			async (page: Page) => {
				if (app.screen === 'devices') await releases(page);
				await openShell(page, app.path);
				await expect(page.getByTestId(`screen-${app.screen}`)).toBeVisible();
			}
		])
	),
	'approvals-waiting': async (page) => {
		await openShell(page, '/approvals');
		await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
		await expect(page.getByRole('alertdialog')).toBeVisible();
	},
	'texts-error': async (page) => {
		await page.route('**/api/messages*', (route) => route.fulfill({ status: 503, body: 'down' }));
		await openShell(page, '/messages');
		await expect(page.getByTestId('texts-state')).toContainText('503');
	},
	'devices-offline': async (page) => {
		await openShell(page, '/devices');
		await expect(page.getByTestId('apps')).toHaveAttribute('data-status', 'offline');
	},
	'settings-offline': async (page) => {
		await away(page);
		await openShell(page, '/settings');
		await expect(page.getByTestId('shell')).toHaveAttribute('data-reactor', 'offline', { timeout: 10_000 });
	},
	'notfound-error': async (page) => {
		await page.goto('/nowhere/at/all');
		await expect(page.getByTestId('route-error')).toBeVisible();
	},
	'popup-idle': async (page) => {
		await page.goto('/?popup=1');
		await expect(page.getByTestId('popup')).toHaveAttribute('data-reactor', 'idle', { timeout: 15_000 });
	},
	'car-browser': async (page) => {
		await page.goto('/car');
		await expect(page.getByTestId('screen-car')).toBeVisible({ timeout: 15_000 });
	},
	'offline-page': async (page) => {
		await page.goto('/offline');
		await expect(page.getByTestId('offline-page')).toBeVisible();
	}
};

test.skip(!process.env.V2_SCREENS, 'V2_SCREENS=1 rewrites docs/screens/v2/console/');

test.beforeEach(async ({ page }) => {
	// GitLab is off this network: Devices says so at once rather than after its timeout.
	await page.route(`${GITLAB}/**`, (route) => route.abort());
});

// One test per picture: each gets a fresh page, so a closed socket or a 503 never
// leaks into the next picture.
for (const [width, height] of WIDTHS) {
	for (const [name, go] of Object.entries(SHOTS)) {
		test(`${name} at ${width}`, async ({ page }) => {
			mkdirSync(OUT, { recursive: true });
			await page.setViewportSize({ width, height });
			await go(page);
			// The projections and lines finish coming out of the core.
			await page.waitForTimeout(1200);
			await page.screenshot({ path: `${OUT}${name}-${width}.png` });
		});
	}
}
