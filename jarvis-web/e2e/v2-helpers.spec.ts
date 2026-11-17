// A delegated subagent on the v2 stage: the shell's take on hud-companions.
//
// The legacy console drew a running subagent as a companion sphere on the
// orb's crew band; the v2 shell has no orbit, so a running helper comes out of
// the core as a projection named for the persona it wears (`companionFor`,
// the same hash the voice uses) and goes back when it finishes. Its report is
// still said in that persona's voice (the owner, 2026-09-09: "all of the
// subagents should be allowed as personas, and their own voices"), one short
// report rather than a narration.
import { expect, test, type Page } from '@playwright/test';

import { emit, openShell, settledAt } from './v2';

/** The stage's slots holding a helper (`data-shown` is the projection's id). */
const helpers = (page: Page) => page.locator('[data-shown^="helper:"]');

test('a running subagent comes out of the core as its persona, and goes back when it finishes', async ({ page }) => {
	await openShell(page);
	await settledAt(page, 'idle');
	await expect(helpers(page), 'nothing delegated, so no helpers').toHaveCount(0);

	await emit(page, 'subagent.start', { id: 'sub-1', goal: 'review the diff' });
	await expect(helpers(page)).toHaveCount(1);
	await expect(helpers(page).first()).toContainText('review the diff');

	await emit(page, 'subagent.start', { id: 'sub-2', goal: 'run the benchmark' });
	await expect(helpers(page)).toHaveCount(2);
	// Each wears a persona that is not the primary's.
	for (const h of await helpers(page).all()) await expect(h).not.toContainText(/JARVIS/i);

	// A background process is a row, not an identity.
	await emit(page, 'process.start', { pid: 42, command: 'npm test' });
	await page.waitForTimeout(500);
	await expect(helpers(page)).toHaveCount(2);
	await expect(page.getByTestId('stage-projection').filter({ hasText: 'npm test' })).toHaveCount(0);

	await emit(page, 'subagent.complete', { id: 'sub-1', status: 'done', summary: 'looks fine' });
	await expect(helpers(page)).toHaveCount(1);
	await expect(helpers(page).first()).toContainText('run the benchmark');
	await emit(page, 'subagent.complete', { id: 'sub-2', status: 'done', summary: 'all green' });
	await expect(helpers(page)).toHaveCount(0);
});

// The mock records which voice each line asked for, for its whole life (every
// spec in the job shares it), so only what this test adds is read.
test('a finished subagent reports in its own voice; the primary keeps its own', async ({ page }) => {
	await openShell(page);
	await settledAt(page, 'idle');
	const spoken = async () =>
		(await page.evaluate(() => fetch('/hermes/api/test/spoken').then((r) => r.json()))) as { spoken: string[]; voices: string[] };
	const before = (await spoken()).voices.length;
	await emit(page, 'subagent.start', { id: 'sub-9', goal: 'check the weather' });
	await expect(helpers(page)).toHaveCount(1);
	await emit(page, 'subagent.complete', {
		id: 'sub-9',
		status: 'done',
		summary: 'Clear skies tonight. Twelve degrees by morning. A third sentence nobody hears. And a fourth.'
	});
	await expect.poll(async () => (await spoken()).voices.length, { timeout: 10_000 }).toBeGreaterThanOrEqual(before + 2);
	const said = await spoken();
	const who = new Set(said.voices.slice(before));
	expect(who.size, 'one voice for the whole report').toBe(1);
	// The companion is the persona that is not primary: AURA, under the mock's JARVIS.
	expect([...who][0]).toBe('aura');
	// The two sentences, not their order: the queue synthesises them together and
	// the mock logs each when its request arrives, which can be swapped under load.
	expect([...said.spoken.slice(before)].sort(), 'the first two sentences').toEqual(['Clear skies tonight.', 'Twelve degrees by morning.']);

	// A reply of the primary's own, on the core where replies are said aloud: still the primary's voice.
	const mid = (await spoken()).voices.length;
	await page.getByTestId('chat-input').fill('and tomorrow?');
	await page.getByRole('button', { name: 'Send' }).click();
	await expect.poll(async () => (await spoken()).voices.length, { timeout: 15_000 }).toBeGreaterThan(mid);
	expect((await spoken()).voices.slice(mid).every((v) => v === 'jarvis')).toBe(true);
});
