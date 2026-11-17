// The installer's setup page on a stage of its own: deploy/setup/server.py started on a free
// port with a scratch state folder and a one-time token, as scripts/install.sh starts it. The
// setup spec and the README's film both stand here, so there is one way to start it.
//
// Not covered: the installer itself. Whoever stands here plays its side by writing progress.
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { type AddressInfo, createServer as createNetServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect } from '@playwright/test';

const REPO = fileURLToPath(new URL('../../', import.meta.url));

export interface SetupStage {
	/** Where the page is served. */
	origin: string;
	/** The one-time token the installer would have printed in the address. */
	token: string;
	/** The scratch state folder: answers.env lands here, progress.json is read from here. */
	state: string;
	/** The installer's side: what it reports is what the page shows. */
	progress(doc: Record<string, unknown>): void;
	stop(): void;
}

function freePort(): Promise<number> {
	return new Promise((resolve, reject) => {
		const probe = createNetServer();
		probe.once('error', reject);
		probe.listen(0, '127.0.0.1', () => {
			const { port } = probe.address() as AddressInfo;
			probe.close(() => resolve(port));
		});
	});
}

/** Start the setup server with these facts about the machine, and wait until it answers its token. */
export async function startSetupServer(facts: Record<string, unknown>): Promise<SetupStage> {
	const state = mkdtempSync(join(tmpdir(), 'aura-setup-e2e-'));
	writeFileSync(join(state, 'facts.json'), JSON.stringify(facts));
	const token = randomBytes(24).toString('hex');
	const port = await freePort();
	const origin = `http://127.0.0.1:${port}`;
	const server = spawn(
		'python3',
		[join(REPO, 'deploy/setup/server.py'), '--state', state, '--src', REPO, '--bind', '127.0.0.1', '--port', String(port)],
		{ env: { ...process.env, AURA_SETUP_TOKEN: token }, stdio: 'ignore' }
	);
	await expect
		.poll(
			async () => {
				try {
					return (await fetch(`${origin}/api/state`, { headers: { Authorization: `Bearer ${token}` } })).status;
				} catch {
					return 0;
				}
			},
			{ timeout: 15_000 }
		)
		.toBe(200);
	return {
		origin,
		token,
		state,
		progress: (doc) => writeFileSync(join(state, 'progress.json'), JSON.stringify(doc)),
		stop: () => {
			server.kill();
			rmSync(state, { recursive: true, force: true });
		}
	};
}

export interface ModelStub {
	/** The endpoint's address, as the setup page is given it. */
	url: string;
	/** The Authorization header of the last request, so a spec can say the key arrived as a bearer. */
	auth(): string;
	close(): Promise<void>;
}

/** A model endpoint that lists these models and nothing else: no real provider is ever asked. */
export async function startModelStub(models: string[]): Promise<ModelStub> {
	let auth = '';
	const endpoint = createServer((req, res) => {
		auth = String(req.headers.authorization ?? '');
		if (req.url === '/v1/models') {
			res.writeHead(200, { 'Content-Type': 'application/json' });
			res.end(JSON.stringify({ data: models.map((id) => ({ id })) }));
		} else {
			res.writeHead(404);
			res.end();
		}
	});
	await new Promise<void>((resolve) => endpoint.listen(0, '127.0.0.1', resolve));
	return {
		url: `http://127.0.0.1:${(endpoint.address() as AddressInfo).port}/v1`,
		auth: () => auth,
		close: () => new Promise<void>((resolve) => endpoint.close(() => resolve()))
	};
}
