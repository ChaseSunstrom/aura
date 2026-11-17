// What waits to be remembered, on the Approvals screen (T-1503), against the mock: a proposal
// is a card with the fact whole, where it would go, who proposed it and what was read; Drop
// takes it away on the plugin's answer; Keep stores nothing by itself, it asks, the gate's
// card shows under Waiting, and the proposal leaves on the yes and stays on the no; nothing
// overflows sideways at 1440 or 390.
// Not covered: the real plugin and the real gate (hermes/plugins/tests/test_memory_proposals.py),
// a model that does not make the call the Keep's sentence names, and the gateway's own look at
// the queue (the mock's `test.memory_announce` stands in for it; the plugin's tests hold it).
import { expect, test, type Page } from '@playwright/test';

import { openShell, resetHermes } from './v2';

const RULE = 'When he says the office he means the room above the garage';
const LONG = `He ${'really '.repeat(26)}likes tea`;

/** One call on a socket of its own: a test hook, or (`session: true`) a command as another app sends it. */
const rpc = (page: Page, method: string, params: Record<string, unknown>, session = false) =>
	page.evaluate(
		async ([m, p, withSession]) => {
			const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/hermes/ws`);
			await new Promise((resolve) => socket.addEventListener('open', resolve));
			let id = 40;
			const call = (name: string, body: Record<string, unknown>) =>
				new Promise<Record<string, unknown>>((resolve) => {
					const mine = ++id;
					const heard = (e: MessageEvent) => {
						const frame = JSON.parse(String(e.data));
						if (frame.id !== mine) return;
						socket.removeEventListener('message', heard);
						resolve(frame.result ?? {});
					};
					socket.addEventListener('message', heard);
					socket.send(JSON.stringify({ jsonrpc: '2.0', id: mine, method: name, params: body }));
				});
			const sid = withSession ? String((await call('session.create', {})).session_id) : '';
			const out = await call(m, withSession ? { ...p, session_id: sid, arg: `@${sid} ${String(p.arg ?? '')}` } : p);
			socket.close();
			return out;
		},
		[method, params, session] as [string, Record<string, unknown>, boolean]
	);

/** What `memory_propose` does in a conversation: one more waits, and the console hears the count. */
const propose = (page: Page, text: string, more: Record<string, unknown> = {}) => rpc(page, 'test.memory_propose', { text, ...more });
/** `jmemory <words>`, as the phone would send it. */
const jmemory = async (page: Page, words: string) =>
	JSON.parse(String((await rpc(page, 'command.dispatch', { name: 'jmemory', arg: words }, true)).output ?? '{}')) as { open: { id: string }[] | number };

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.afterEach(async ({ page }) => {
	// The mock keeps what waits.
	await resetHermes(page);
});

for (const size of [
	{ width: 1440, height: 900 },
	{ width: 390, height: 844 }
]) {
	test(`a proposal is its fact whole with where it came from; Drop takes it away (${size.width})`, async ({ page }) => {
		await page.setViewportSize(size);
		await openShell(page, '/approvals');
		await expect(page.getByTestId('approvals-memory-state')).toHaveAttribute('data-state', 'empty');
		await propose(page, RULE, { read: ['session_search'] });
		await propose(page, LONG, { store: 'memory', by: 'telegram', read: ['web_extract', 'mail_read'], instruction: true });

		// No reload: the plugin announces each one as a count, and the screen reads the list.
		const cards = page.getByTestId('memory-card');
		await expect(cards).toHaveCount(2);
		const rule = cards.nth(0);
		await expect(rule.getByTestId('memory-text')).toHaveText(RULE);
		await expect(rule.getByTestId('memory-where')).toHaveText('For the fact store');
		await expect(rule.getByTestId('memory-source')).toHaveText('Proposed by a routine, after reading session_search.');
		await expect(rule.getByTestId('memory-instruction')).toHaveCount(0);
		const long = cards.nth(1);
		await expect(long.getByTestId('memory-text')).toHaveText(LONG);
		await expect(long.getByTestId('memory-where')).toHaveText('For your profile');
		await expect(long.getByTestId('memory-instruction')).toBeVisible();
		await expect(long.getByTestId('memory-source')).toHaveText('Proposed by a routine or message (telegram), after reading web_extract, mail_read.');
		await expect(long.getByTestId('memory-instruction-note')).toContainText('reads it back as your own words');
		await expect(rule.getByTestId('memory-instruction-note')).toHaveCount(0);
		expect(await overflow(page)).toBeLessThanOrEqual(0);
		// For a person to read: a tall window so both cards are in it (the screen scrolls inside
		// the shell, which `fullPage` does not follow).
		await page.setViewportSize({ width: size.width, height: 1800 });
		await page.screenshot({ path: `test-results/v2-memory-proposals-${size.width}.png`, animations: 'disabled' });
		await page.setViewportSize(size);

		await rule.getByTestId('memory-drop').click();
		await expect(cards).toHaveCount(1);
		await expect(cards.getByTestId('memory-text')).toHaveText(LONG);
		expect(((await jmemory(page, 'proposals')).open as { id: string }[]).map((p) => p.id)).toEqual(['p2']);
	});
}

test('Keep asks: the gate’s card shows the fact under Waiting, and the proposal leaves on the yes', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/approvals');
	await propose(page, RULE, { read: ['session_search'] });
	await propose(page, 'He takes his coffee black');
	const cards = page.getByTestId('memory-card');
	await expect(cards).toHaveCount(2);
	await expect(page.getByTestId('approvals-card')).toHaveCount(0);

	await cards.nth(0).getByTestId('memory-keep').click();
	// Nothing is stored by the click: the card stays, says what was asked, and nothing more is asked meanwhile.
	await expect(cards.nth(0).getByTestId('memory-asked')).toBeVisible();
	await expect(cards.nth(0).getByTestId('memory-keep')).toBeDisabled();
	await expect(cards.nth(1).getByTestId('memory-keep')).toBeDisabled();
	const ask = page.getByTestId('approvals-card');
	await expect(ask).toHaveCount(1);
	await expect(ask).toContainText(`“${RULE}”`);
	await expect(ask).toContainText('after reading session_search');
	await expect(cards).toHaveCount(2);
	await page.setViewportSize({ width: 1440, height: 1800 });
	await page.screenshot({ path: 'test-results/v2-memory-proposals-keep-1440.png', animations: 'disabled' });
	await page.setViewportSize({ width: 1440, height: 900 });

	await ask.getByRole('button', { name: 'Approve', exact: true }).click();
	await expect(cards).toHaveCount(1);
	await expect(cards.getByTestId('memory-text')).toHaveText('He takes his coffee black');
	// The turn is over: the one that still waits can be asked about.
	await expect(cards.getByTestId('memory-keep')).toBeEnabled();
	expect((await jmemory(page, 'proposals')).open).toHaveLength(1);
});

test('a no on the card keeps nothing: the proposal still waits and can be asked again', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page, '/approvals');
	await propose(page, RULE);
	const card = page.getByTestId('memory-card');
	await card.getByTestId('memory-keep').click();
	const ask = page.getByTestId('approvals-card');
	await expect(ask).toHaveCount(1);
	await ask.getByRole('button', { name: 'Deny', exact: true }).click();
	await expect(ask).toHaveCount(0);
	await expect(card).toHaveCount(1);
	await expect(card.getByTestId('memory-asked')).toHaveCount(0);
	await expect(card.getByTestId('memory-keep')).toBeEnabled();
	expect((await jmemory(page, 'proposals')).open).toHaveLength(1);
});

test('a drop that is refused says why and the card stays; one dropped elsewhere goes without a reload', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/approvals');
	await propose(page, RULE);
	await rpc(page, 'test.memory_propose', { drop: false });
	const cards = page.getByTestId('memory-card');
	await cards.getByTestId('memory-drop').click();
	await expect(page.getByTestId('memory-failed')).toContainText('the kill switch is on');
	await expect(cards).toHaveCount(1);
	await rpc(page, 'test.memory_propose', { drop: true });
	await jmemory(page, 'drop all');
	await expect(cards).toHaveCount(0);
	await expect(page.getByTestId('approvals-memory-state')).toHaveAttribute('data-state', 'empty');
});

test('a routine’s proposal shows on an open screen when the gateway’s look announces it (T-1506)', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/approvals');
	await expect(page.getByTestId('approvals-memory-state')).toHaveAttribute('data-state', 'empty');
	// Saved in the routine's own process: nothing is said until the gateway looks.
	await propose(page, RULE, { quiet: true });
	await page.waitForTimeout(400);
	await expect(page.getByTestId('memory-card')).toHaveCount(0);
	await rpc(page, 'test.memory_announce', {});
	await expect(page.getByTestId('memory-card')).toHaveCount(1);
});

test('Keep all asks once: one card shows every fact, and they leave on the yes', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/approvals');
	await expect(page.getByTestId('memory-keep-all')).toHaveCount(0);
	await propose(page, RULE);
	await expect(page.getByTestId('memory-card')).toHaveCount(1);
	await expect(page.getByTestId('memory-keep-all')).toHaveCount(0); // one waits: its own Keep is the verb
	await propose(page, 'He takes his coffee black');
	await propose(page, 'The bins go out on Tuesday nights', { store: 'memory' });
	const cards = page.getByTestId('memory-card');
	await expect(cards).toHaveCount(3);
	await page.getByTestId('memory-keep-all').click();
	await expect(page.getByTestId('memory-asked')).toHaveCount(3);
	await expect(page.getByTestId('memory-keep-all')).toBeDisabled();
	const ask = page.getByTestId('approvals-card');
	await expect(ask).toHaveCount(1);
	await expect(ask).toContainText('3 facts');
	for (const fact of [RULE, 'He takes his coffee black', 'The bins go out on Tuesday nights']) await expect(ask).toContainText(`“${fact}”`);
	await page.setViewportSize({ width: 1440, height: 1800 });
	await page.screenshot({ path: 'test-results/v2-memory-proposals-keep-all-1440.png', animations: 'disabled' });
	await page.setViewportSize({ width: 1440, height: 900 });
	await ask.getByRole('button', { name: 'Approve', exact: true }).click();
	await expect(cards).toHaveCount(0);
	expect((await jmemory(page, 'proposals')).open).toHaveLength(0);
});

test('Drop all drops the cards shown and the list is empty', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await openShell(page, '/approvals');
	await propose(page, RULE);
	await propose(page, 'He takes his coffee black');
	await expect(page.getByTestId('memory-card')).toHaveCount(2);
	await page.screenshot({ path: 'test-results/v2-memory-proposals-all-390.png', animations: 'disabled', fullPage: true });
	expect(await overflow(page)).toBeLessThanOrEqual(0);
	await page.getByTestId('memory-drop-all').click();
	await expect(page.getByTestId('memory-card')).toHaveCount(0);
	await expect(page.getByTestId('approvals-memory-state')).toHaveAttribute('data-state', 'empty');
	expect((await jmemory(page, 'proposals')).open).toHaveLength(0);
});

test('how many wait to be remembered shows on All apps and on the launcher’s Approvals, and nothing at zero', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await openShell(page, '/');
	const apps = page.getByRole('button', { name: /^All apps/ });
	const count = page.getByTestId('apps-waiting');
	await expect(apps).toBeVisible();
	await expect(count).toHaveCount(0);
	await propose(page, RULE);
	await expect(count).toHaveText('1');
	await expect(apps).toHaveAccessibleName('All apps, 1 to remember');
	await propose(page, 'He takes his coffee black', { quiet: true });
	// Read on connect, not only heard: a reload shows both.
	await page.reload();
	await expect(page.getByTestId('shell')).toHaveAttribute('data-ready', 'true', { timeout: 15_000 });
	await expect(count).toHaveText('2');
	await apps.click();
	await expect(page.getByTestId('launcher-waiting')).toHaveText('2 to remember');
	await page.screenshot({ path: 'test-results/v2-memory-proposals-launcher-1440.png', animations: 'disabled' });
	await page.keyboard.press('Escape');
	await jmemory(page, 'drop all');
	await expect(count).toHaveCount(0);
	await apps.click();
	await expect(page.getByTestId('launcher-waiting')).toHaveCount(0);
});
