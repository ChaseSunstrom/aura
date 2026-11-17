// `make console-live` run against the e2e server's build, before a merge (T-0842).
//
// console-live.mjs waits on three things: the shell's `data-ready=true`, its `data-screen`, and
// `screen-<name>` for every app in `routes.ts`. It ran only in deploy:console, after the deploy,
// so the v2 shell swap (T-0611) renamed what it waits on and nobody knew until T-0838 failed
// every pipeline that reached it. This runs the script itself (no copy of its selectors to drift)
// against the build every other spec drives, so a renamed testid or a screen that never mounts
// fails here, in the merge request.
//
// Does not cover: the deployed stack's own data (the e2e server answers from mocks), the 390 px
// width, or the script's `errors`/`clipped` checks, which the mock's 404s and the e2e fonts would
// make noisy; `--check loaded` still walks every selector.
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

// The constant the e2e server's mock adapter answers to (allowJs types the .mjs).
import { MOCK_KEY } from '../../tests/web/mock-hermes.mjs';

const script = fileURLToPath(new URL('../../scripts/verify/console-live.mjs', import.meta.url));

test('console-live finds the shell and every launcher screen on the built console', async ({ baseURL }) => {
	// Every screen: a navigation, up to 20 s for data-ready, the 500 ms wait.
	test.setTimeout(180_000);
	const run = await new Promise<{ code: number; out: string }>((resolve) => {
		execFile(
			process.execPath,
			[script, `${baseURL}/`, '--width', '1440', '--check', 'loaded', '--wait', '500'],
			// The adapter key the e2e server's mock holds: the script signs in with it, as the phone does.
			{ env: { ...process.env, AURA_API_KEY: MOCK_KEY }, timeout: 150_000 },
			(err, stdout, stderr) => resolve({ code: err ? ((err as { code?: number }).code ?? 1) : 0, out: stdout + stderr })
		);
	});
	expect(run.code, run.out).toBe(0);
	expect(run.out).toMatch(/console-live 1440px loaded: (\d+) of \1 screens, ok/);
});
