import { fileURLToPath } from 'node:url';

import { defineConfig } from '@playwright/test';

// The port is a knob because 8199 is also where a running install's HUD
// listens (docker-compose.yml, host networking). On the box that runs Jarvis,
// `reuseExistingServer: false` below would refuse to start — or, flipped on,
// would silently test the live HUD instead of the mock-backed build. So a
// verify run sets E2E_PORT to something free; the default stays 8199 so CI
// and the README are unchanged. serve-e2e.mjs reads PORT, which is passed
// through here.
const port = process.env.E2E_PORT ?? '8199';
/**
 * The console session serve-e2e.mjs seeds: every spec's browser is signed in
 * (T-0387). A context made by hand adds `SIGNED_IN` itself.
 */
export const E2E_SESSION = 'e2e-signed-in';
export const SIGNED_IN = {
	name: 'jarvis_console',
	value: E2E_SESSION,
	domain: '127.0.0.1',
	path: '/',
	expires: -1,
	httpOnly: true,
	secure: false,
	sameSite: 'Strict' as const
};

export default defineConfig({
	testDir: 'e2e',
	// Specs load in Node, never through Vite, so the SvelteKit aliases ($lib) do not exist
	// for anything a spec imports. Locally they resolved anyway, through the paths in the
	// generated .svelte-kit/tsconfig.json; CI's a11y and frame jobs get only build/, so a
	// '$lib/qr' import under hud/data/apps.ts passed here and killed hud-a11y there. This
	// tsconfig has no paths, so a local run fails the same way.
	tsconfig: './e2e/playwright.tsconfig.json',
	// Every *.spec.ts in e2e/, not one named file. It WAS one named file, which
	// meant a new spec added beside it was collected by nothing and reported as
	// passing by being absent.
	// The specs that rewrite committed pictures (`v2-sweep`, `ds-core`'s screenshots) skip
	// themselves unless their variable is set, so a bare run never touches docs/.
	testMatch: '*.spec.ts',
	timeout: 60_000,
	fullyParallel: false,
	// One more try on CI, none here. Pipelines 929 and 931 did not deploy because hud-behaviour
	// failed on runner 2 on tests that pass on this box: in 931 the headless browser itself
	// died under three of them ("page.evaluate: Target crashed"). A test that passes on its
	// second try is listed as flaky by the list reporter at the end of the job's log and in the
	// html report, so a retry is seen, not hidden; a test that fails twice still fails the job.
	// Locally a failure is never retried: it is read.
	retries: process.env.CI ? 1 : 0,
	workers: 1,
	// On CI the `junit` report is what the merge request lists failing tests
	// from (GitLab renders `artifacts:reports:junit`; there are no per-line
	// annotations as there were on GitHub), and the html report is the artifact
	// the job keeps (with `list` alone it found nothing, twice). Locally, the
	// list is enough and an html report would open a browser tab.
	reporter: process.env.CI
		? [
				['list'],
				['junit', { outputFile: 'playwright-junit.xml' }],
				['html', { open: 'never', outputFolder: 'playwright-report' }]
			]
		: [['list']],
	use: {
		baseURL: `http://127.0.0.1:${port}`,
		browserName: 'chromium',
		headless: true,
		// The fake microphone (launchOptions below) says a sentence every few
		// seconds for ever, and the console ships with the mic open (the owner,
		// 2026-09-18). Every spec would otherwise hear it, transcribe it through
		// the mock and start turns nobody asked for -- the reactor never idle,
		// the chat filling by itself, every screenshot mid-turn. So every
		// context starts muted, the way a person who muted once does; the two
		// specs about the mic forget it (`micAsShipped` in v2.ts). A
		// context made by hand (`browser.newContext`) gets none of this unless
		// it spreads `contextOptions`.
		storageState: {
			cookies: [SIGNED_IN],
			origins: [
				{
					origin: `http://127.0.0.1:${port}`,
					localStorage: [{ name: 'jarvis:open-mic', value: '0' }]
				}
			]
		},
		launchOptions: {
			// No `executablePath`. Pinning one to a developer container's layout
			// is why this suite launched nothing on CI: `/opt/pw-browsers/chromium`
			// exists in that container and nowhere else, so every test failed with
			// "executable doesn't exist" the moment the specs became discoverable.
			//
			// Playwright finds its own browser: on CI from the default cache that
			// `npx playwright install --with-deps chromium` populates, and in a
			// container from PLAYWRIGHT_BROWSERS_PATH, which is the supported way
			// to point it at a preinstalled one.
			args: [
				'--no-sandbox',
				// A Docker executor gives the job a 64 MB /dev/shm, and Chromium keeps its
				// render buffers there by default: on the shared runners hud-a11y — the
				// heaviest walk in the pipeline, every route plus the gallery — died mid-run
				// ("Less than 64MB of free space in temporary directory for shared memory
				// files"), taking the web server with it, so every later route failed with
				// ERR_CONNECTION_REFUSED while the same commit passed 26/26 on this box.
				// /tmp instead of /dev/shm is the documented fix and costs nothing elsewhere.
				'--disable-dev-shm-usage',
				'--use-fake-device-for-media-stream',
				// What the fake microphone hears: a second and a half of speech-like
				// noise, then quiet, looped -- "a short sentence, a pause" for ever.
				// The open-mic spec needs an utterance that ends on its own; the
				// default fake device beeps without pause. Every stream opened on
				// it starts at the top of the file.
				`--use-file-for-fake-audio-capture=${fileURLToPath(new URL('./e2e/fixtures/open-mic.wav', import.meta.url))}`,
				'--use-fake-ui-for-media-stream',
				'--autoplay-policy=no-user-gesture-required'
			]
		}
	},
	webServer: {
		command: 'node ../tests/web/serve-e2e.mjs',
		url: `http://127.0.0.1:${port}/healthz`,
		// Passkeys need a named site (T-0946): an address is no relying-party id, localhost is.
		env: { PORT: port, E2E_SESSION, AURA_CONSOLE_ORIGIN: `http://localhost:${port}` },
		reuseExistingServer: false,
		stdout: 'pipe',
		stderr: 'pipe',
		timeout: 60_000
	}
});
