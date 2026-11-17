import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { APPS } from '../src/lib/ds/shell/routes';
import { GITLAB } from '../src/lib/hud/data/apps';
import { SNAPSHOT_APPROVAL, emit, openShell, settledAt } from './v2';

/**
 * The axe gate (the brief's "ACCESSIBILITY AND PERFORMANCE GATES"): every
 * screen of the v2 shell, the moments it draws over the core, and the pages
 * beside it audited by axe-core, failing on any serious or critical violation.
 *
 * Every screen once (the launcher's apps from `lib/ds/shell/routes.ts`, the
 * core and the car), not every path: since T-0611 most of the legacy paths are
 * the core, and auditing the same page twenty times audits nothing more. The
 * table is imported so a screen added there is audited without anybody
 * remembering. `@axe-core/playwright` is pinned exact: axe adds rules between
 * minors, and a floating version would turn the gate red on a day nobody
 * changed the UI.
 *
 * What it does not cover: contrast against the rendered core (axe reads the
 * declared colours; a11y-contrast.spec.ts samples pixels), keyboard reach
 * (a11y-keyboard.spec.ts) and the OS preferences (a11y-preferences.spec.ts).
 * Moderate and minor findings are written to `.verify/hud-a11y.json` for the
 * record and do not fail the gate.
 *
 * ## The baseline
 *
 * `e2e/a11y-baseline.json` lists serious/critical findings that were known when
 * the gate was introduced, one (route, rule, node) triple each; it has been
 * empty since the glass migration. A finding in it is reported, not failed; a
 * finding outside it fails; an entry the run no longer finds ALSO fails, so the
 * file only ever shrinks. Node targets are keyed without Svelte's scoped-style
 * hash (`.svelte-1avw3g2`), which changes whenever a component's CSS does.
 */

const ROOT = new URL('../../', import.meta.url).pathname;
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const BLOCKING = new Set(['serious', 'critical']);

/** Every screen once, and the pages that are not the shell: the offline page, a 404 and the galleries. */
const ROUTES = ['/', ...APPS.map((a) => a.path), '/car', '/offline', '/nowhere', '/gallery/tokens', '/gallery/core'];

type Finding = { route: string; scope: string; id: string; impact: string; help: string; nodes: string[] };
type Known = { route: string; id: string; target: string };
const BASELINE = JSON.parse(readFileSync(new URL('./a11y-baseline.json', import.meta.url), 'utf8')) as { known: Known[] };
const REPORT = `${ROOT}.verify/hud-a11y.json`;

/** A node target without Svelte's per-component style hash. */
const stable = (target: string) => target.replace(/\.svelte-[a-z0-9]+/g, '') || target;

// Findings are appended to the report file rather than held in memory: a
// failed test makes Playwright restart the worker, and an array in module
// scope restarts with it. The first worker of the run clears the file.
test.beforeAll(() => {
	mkdirSync(`${ROOT}.verify`, { recursive: true });
	if (test.info().workerIndex === 0) rmSync(REPORT, { force: true });
});
function record(rows: Finding[]): void {
	const doc = existsSync(REPORT) ? JSON.parse(readFileSync(REPORT, 'utf8')) : { recorded_at: new Date().toISOString(), tags: TAGS, findings: [] as Finding[] };
	doc.findings.push(...rows);
	writeFileSync(REPORT, JSON.stringify(doc, null, 2));
}

/** Every finite animation finished (or three seconds): axe reads contrast through the opacity of the moment. */
const settle = (page: Page) =>
	page.evaluate(() =>
		Promise.race([
			Promise.all(
				document
					.getAnimations()
					.filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
					.map((a) => a.finished.catch(() => undefined))
			),
			new Promise((resolve) => setTimeout(resolve, 3_000))
		])
	);

async function audit(page: Page, route: string, scope: string, include?: string): Promise<Finding[]> {
	await settle(page);
	const run = () => (include ? new AxeBuilder({ page }).withTags(TAGS).include(include) : new AxeBuilder({ page }).withTags(TAGS)).analyze();
	let result = await run();
	// A projection that came out after the settle above is caught mid-fade. Settle
	// again and look once more; a real low-contrast text fails both reads.
	if (result.violations.some((v) => v.id === 'color-contrast')) {
		await settle(page);
		result = await run();
	}
	const rows = result.violations.map((v) => ({
		route,
		scope,
		id: v.id,
		impact: v.impact ?? 'unknown',
		help: v.help,
		nodes: v.nodes.slice(0, 5).map((n) => n.target.join(' '))
	}));
	record(rows);
	return rows;
}

/**
 * The serious/critical findings of a route against the baseline: what is new
 * (fails), what is known (reported) and what the baseline lists but the run no
 * longer sees (fails: the entry must be removed, not left as slack).
 */
function judge(route: string, rows: Finding[]): { fresh: string[]; known: string[]; stale: string[] } {
	const seen = new Set<string>();
	const fresh: string[] = [];
	const known: string[] = [];
	const expected = new Map(BASELINE.known.filter((k) => k.route === route).map((k) => [`${k.id} @ ${stable(k.target)}`, k]));
	for (const r of rows) {
		if (!BLOCKING.has(r.impact)) continue;
		for (const target of r.nodes) {
			const key = `${r.id} @ ${stable(target)}`;
			seen.add(key);
			(expected.has(key) ? known : fresh).push(`${route}: ${key} (${r.impact}: ${r.help})`);
		}
	}
	const stale = [...expected.keys()].filter((k) => !seen.has(k)).map((k) => `${route}: ${k}`);
	if (known.length) console.log(`hud-a11y known (baseline): ${known.join('; ')}`);
	return { fresh, known, stale };
}

async function gate(page: Page, route: string, scope: string, include?: string): Promise<void> {
	const { fresh, stale } = judge(route, await audit(page, route, scope, include));
	expect(fresh, 'serious/critical findings not in a11y-baseline.json').toEqual([]);
	expect(stale, 'baseline entries no longer found: remove them from a11y-baseline.json').toEqual([]);
}

test.beforeEach(async ({ page }) => {
	// Devices reads GitLab's releases for the downloads; the audit must not depend on reaching it.
	await page.route(`${GITLAB}/**`, (r) => r.abort());
	await page.setViewportSize({ width: 1440, height: 900 });
});

test.describe('axe over every screen', () => {
	for (const route of ROUTES) {
		test(`${route} has no serious or critical violation`, async ({ page }) => {
			await page.goto(route);
			// The screens render their real state from the mock backends; a
			// loading skeleton would audit as a page with no content.
			await page.waitForLoadState('networkidle').catch(() => {});
			await expect(page.locator('main, [role="main"], [data-testid="screen-car"]').first()).toBeVisible({ timeout: 15_000 });
			await gate(page, route, 'route');
		});
	}
});

test('the core with an approval card out has no serious or critical violation', async ({ page }) => {
	await openShell(page);
	await emit(page, 'approval.request', SNAPSHOT_APPROVAL);
	await settledAt(page, 'alert');
	await expect(page.getByTestId('approval-card')).toBeVisible();
	await gate(page, '/ (approval)', 'approval');
	await page.getByTestId('approval-card').getByRole('button', { name: 'Deny' }).click();
});

test('the launcher and the palette, open, have no serious or critical violation', async ({ page }) => {
	await openShell(page);
	await page.getByRole('button', { name: 'All apps' }).click();
	await expect(page.getByTestId('launcher')).toBeVisible();
	await gate(page, '/ (launcher)', 'launcher');
	await page.keyboard.press('Escape');
	await page.keyboard.press('Control+k');
	await expect(page.getByTestId('palette')).toBeVisible();
	await gate(page, '/ (palette)', 'palette');
});

test('the console with the adapter away has no serious or critical violation', async ({ page }) => {
	// The offline core and every screen's offline state are drawn where a person meets them.
	await page.routeWebSocket(/\/hermes\/ws/, (ws) => ws.close());
	await openShell(page);
	await expect(page.getByTestId('shell')).toHaveAttribute('data-reactor', 'offline', { timeout: 20_000 });
	await gate(page, '/ (offline)', 'offline');
	await page.goto('/approvals');
	await expect(page.getByTestId('screen-approvals')).toBeVisible({ timeout: 15_000 });
	await gate(page, '/approvals (offline)', 'offline');
});

test('every token group has no serious or critical violation', async ({ page }) => {
	await page.goto('/gallery/tokens');
	await expect(page.getByTestId('gallery-tokens')).toBeVisible();
	// Each token group is its own scope, so a finding names the group rather than "the tokens page".
	const entries = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('[data-tokens]')].map((el) => `[data-tokens="${el.dataset.tokens}"]`));
	expect(entries.length, 'token groups found').toBeGreaterThan(3);
	for (const selector of entries) await gate(page, `/gallery/tokens ${selector}`, selector, selector);
});

test.afterAll(() => {
	if (!existsSync(REPORT)) return;
	const findings = (JSON.parse(readFileSync(REPORT, 'utf8')) as { findings: Finding[] }).findings;
	const counts: Record<string, number> = {};
	for (const f of findings) counts[f.impact] = (counts[f.impact] ?? 0) + f.nodes.length;
	console.log(`hud-a11y axe: ${findings.length} rule findings ${JSON.stringify(counts)} by node; serious/critical block unless in a11y-baseline.json (${BASELINE.known.length} known)`);
});
