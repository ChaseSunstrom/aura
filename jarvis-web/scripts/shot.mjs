// Screenshot a URL so a screen can be looked at rather than inferred from its
// source. The browser MCP servers on this host resolve Chrome at
// /opt/google/chrome/chrome, a path root owns and this account cannot create,
// so the evidence for "I looked at it" comes from the same Chromium Playwright
// already drives in e2e.
//
// Usage: node scripts/shot.mjs <url> <out.png> [width] [height] [waitMs] [reduced]
//   reduced — pass "reduced" to emulate prefers-reduced-motion, which is the
//   flat non-overlapping projection the map degrades to.
import { chromium } from '@playwright/test';

const [url, out, w = '1440', h = '900', wait = '1200', reduced] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({
	viewport: { width: +w, height: +h },
	reducedMotion: reduced === 'reduced' ? 'reduce' : 'no-preference'
});
await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(+wait);
await page.screenshot({ path: out });
await browser.close();
console.log(out);
