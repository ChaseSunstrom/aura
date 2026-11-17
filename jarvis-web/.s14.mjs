import { chromium } from 'playwright';
const b = await chromium.launch({ args: ['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--js-flags=--expose-gc'] });
const results = [];
for (const n of [240, 5000, 50000]) {
  const p = await b.newPage({ viewport: { width: 1100, height: 800 } });
  const t0 = Date.now();
  await p.goto(`http://127.0.0.1:5227/gallery/orb?view=memory&nodes=${n}&size=560`, { waitUntil: 'domcontentloaded' });
  // Wait for the worker layout to land.
  await p.waitForFunction((n) => (document.querySelector('[data-testid="memory-figure"]')?.getAttribute('data-memory-source') ?? '').startsWith(`${n} entries`), n, { timeout: 240_000 });
  const layoutMs = Date.now() - t0;
  const source = await p.getAttribute('[data-testid="memory-figure"]', 'data-memory-source');
  await p.waitForTimeout(1500);
  const fps = await p.evaluate(() => new Promise((res) => {
    let frames = 0; const t = performance.now();
    const tick = () => { frames++; if (performance.now() - t < 4000) requestAnimationFrame(tick); else res((frames / 4).toFixed(1)); };
    requestAnimationFrame(tick);
  }));
  const tier = await p.textContent('[data-testid="memory-tier"]');
  const heap = await p.evaluate(() => (performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null));
  results.push({ n, layoutMs, source, fps, tier: tier?.trim(), heapMB: heap });
  console.log(JSON.stringify(results.at(-1)));
  await p.close();
}
await b.close();
