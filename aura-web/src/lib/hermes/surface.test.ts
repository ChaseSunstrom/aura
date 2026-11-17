// The console's surface report, held to `tests/contracts/surface-capabilities.json`:
// the same table the artifacts plugin's registry carries for `web`, sent as
// `params.surface` on every `prompt.submit` (a typed line, a spoken one, and
// the `send` a slash command asks for). Held by a shared table rather than a
// comment saying "keep in step", because a comment has never once kept anything
// in step; the plugin, the phone and the TUI read the same file.
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { HermesClient, SURFACE, type SocketLike } from './client';

const contract = JSON.parse(
	readFileSync(new URL('../../../../tests/contracts/surface-capabilities.json', import.meta.url), 'utf8')
) as { param: string; fields: string[]; surfaces: Record<string, Record<string, unknown>> };

class FakeSocket implements SocketLike {
	readyState = 1;
	sent: any[] = [];
	onopen: ((ev: unknown) => void) | null = null;
	onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
	onerror: ((ev: unknown) => void) | null = null;
	onmessage: ((ev: { data: unknown }) => void) | null = null;
	send(data: string): void {
		const frame = JSON.parse(data);
		this.sent.push(frame);
		// A `send` result: the client must follow with prompt.submit of the message.
		const result = frame.method === 'command.dispatch' ? { type: 'send', message: 'Continue', notice: 'ok' } : { status: 'streaming' };
		queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ jsonrpc: '2.0', id: frame.id, result }) }));
	}
	close(): void {}
}

describe('the surface report', () => {
	it('is the contract row for web, field for field', () => {
		expect(SURFACE).toEqual(contract.surfaces.web);
		expect(Object.keys(SURFACE)).toEqual(contract.fields);
	});

	it('rides the prompt a slash command asks the client to submit', async () => {
		const socket = new FakeSocket();
		const client = new HermesClient({
			url: 'ws://t/hermes/ws',
			openSocket: () => socket,
			setTimeoutFn: (() => 0) as any,
			clearTimeoutFn: (() => {}) as any,
			random: () => 0
		});
		client.start();
		socket.onopen?.(null);
		const outcome = await client.dispatchCommand('s1', 'goal', 'tidy');
		expect(outcome.kind).toBe('sent');
		const prompt = socket.sent.find((f) => f.method === 'prompt.submit');
		expect(prompt.params[contract.param]).toEqual(contract.surfaces.web);
	});
});
