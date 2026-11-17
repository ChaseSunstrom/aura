// Playwright webServer command: start the mock HA and the mock Hermes adapter,
// then the built aura-web app (node build) pointed at both. Dies with its
// children.
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { startMockHA, MOCK_TOKEN } from './mock-ha.mjs';
import { startMockHermes } from './mock-hermes.mjs';

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'aura-web');

// The e2e console is one that has been set up. First run (no password ever
// chosen, no "later") puts the setup sheet over the room, and every spec
// written for the room would meet it on a fresh runner; the sheet itself is
// exercised by `hud-setup.spec.ts` through `?setup=1`. The mark is what the
// console route reads (`$lib/server/consoleAuth` setupFile), beside the
// password file it writes under the server's cwd.
const setupMark = join(webRoot, '.storage', 'console-setup.json');
if (!existsSync(setupMark)) {
	mkdirSync(dirname(setupMark), { recursive: true });
	writeFileSync(setupMark, JSON.stringify({ done: new Date().toISOString(), name: null }) + '\n', { mode: 0o600 });
}

// T-0629: a walkthrough row left half-done by a stopped run would send every spec's core
// to /setup; the run starts with it closed, and the walkthrough's own spec opens it.
writeFileSync(join(webRoot, '.storage', 'console-walkthrough.json'), JSON.stringify({ schema: 1, marks: {}, closed: true }) + '\n', { mode: 0o600 });

// T-0387: the console's relays and API refuse a browser that has not signed in
// once a password exists, and the lock specs need one. So the run has a password
// (the env one, which wins over any file a past run chose) and one live session,
// the cookie every spec's browser carries (`E2E_SESSION` in playwright.config.ts).
const E2E_PASSWORD = 'e2e-console-password';
// Only this one: the sign-ins of past runs piled up to the console's cap of 64, and
// the cap evicts the session that expires first, which was this one, so every spec
// after the lock specs ran signed out.
const sessionsFile = join(webRoot, '.storage', 'console-sessions.json');
writeFileSync(
	sessionsFile,
	JSON.stringify({ [process.env.E2E_SESSION ?? 'e2e-signed-in']: Date.now() + 24 * 60 * 60 * 1000 }),
	{ mode: 0o600 }
);

const mock = await startMockHA({ log: console.log });
// Handed the house so `POST /api/test/drop` can drop both sockets at once.
const hermes = await startMockHermes({ log: console.log, house: mock });

const child = spawn(process.execPath, ['build'], {
	cwd: webRoot,
	stdio: 'inherit',
	env: {
		...process.env,
		PORT: process.env.PORT ?? '8199',
		HOST: '127.0.0.1',
		// Exercise the jarvis-core path: AURA_* must win over HA_*, so the
		// deliberately-wrong HA vars below double as a precedence regression test
		// — if resolveBackend picked them, every e2e test would fail to connect.
		AURA_BACKEND: 'core',
		AURA_URL: mock.url,
		AURA_TOKEN: MOCK_TOKEN,
		HA_URL: 'http://127.0.0.1:1',
		HA_TOKEN: 'wrong-token',
		AURA_PIPELINE: 'Jarvis',
		AURA_TTS_VOICE: 'en_GB-alan-medium',
		// The Hermes surfaces: the console relays /hermes/* to the mock adapter
		// with this key, which the browser must never see.
		AURA_ADAPTER_URL: hermes.url,
		AURA_ADAPTER_KEY: hermes.key,
		AURA_CONSOLE_PASSWORD: E2E_PASSWORD
	}
});

const shutdown = async () => {
	child.kill('SIGTERM');
	await mock.close();
	await hermes.close();
	process.exit(0);
};
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, shutdown);
child.on('exit', (code) => {
	mock.close();
	hermes.close();
	process.exit(code ?? 0);
});
