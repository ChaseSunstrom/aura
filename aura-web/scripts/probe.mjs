// Read values out of a running screen — the numbers half of checking a UI.
//
// A screenshot answers "does this look right"; this answers "what is it, exactly"
// — a rect, a computed colour, how many of a thing there are — without pulling a
// whole accessibility tree back. Pair it with `shot.mjs`: look at the picture,
// measure the numbers (the v3 plan §1.4).
//
// Usage: node scripts/probe.mjs <url> '<expression>' [width] [height] [waitMs]
//   The expression is evaluated in the page and its result printed as JSON.
import { chromium } from '@playwright/test';

const [url, expression, w = '1440', h = '900', wait = '1500'] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: +w, height: +h } });
await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForTimeout(+wait);
console.log(JSON.stringify(await page.evaluate(`(() => (${expression}))()`), null, 1));
await browser.close();
