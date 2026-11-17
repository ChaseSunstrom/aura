// JARVIS's reply draws its Markdown under the core (T-0727). The owner's NUFI reply showed
// literal ** and inline "- " bullets; the mock Hermes streams that reply a few characters at a
// time when a prompt names NUFI (tests/contracts/markdown_text.json holds its text), and the
// subtitle must read as a list with bold labels at a desktop and a phone width.
//
// MARKDOWN_SCREENS=1 writes docs/screens/v2/markdown/stage-<width>.png, the record; the core is
// animated all the time, so a pixel golden would be flaky.
import { expect, test } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';

import { openShell, say } from './v2';

const ROOT = new URL('../../', import.meta.url).pathname;

for (const [w, h] of [
	[1440, 900],
	[390, 844]
] as const) {
	test(`the NUFI reply reads as a list with bold labels at ${w}px`, async ({ page }) => {
		await page.setViewportSize({ width: w, height: h });
		await openShell(page);
		await say(page, 'What is on the NUFI page?');
		const said = page.getByTestId('conversation-jarvis');
		await expect(said).toContainText('which of these should I connect?', { timeout: 15_000 });
		await expect(said.locator('li')).toHaveCount(3);
		await expect(said.locator('strong')).toHaveText(['Wallet extension', 'Google', 'Facebook']);
		const text = await said.innerText();
		expect(text).not.toContain('**');
		expect(text).not.toMatch(/(^|\s)- /m);
		if (process.env.MARKDOWN_SCREENS) {
			const out = `${ROOT}docs/screens/v2/markdown`;
			mkdirSync(out, { recursive: true });
			writeFileSync(`${out}/stage-${w}.png`, await page.screenshot());
		}
	});
}
