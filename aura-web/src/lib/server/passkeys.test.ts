import { describe, it, expect, beforeEach } from 'vitest';
import * as nodeCrypto from 'node:crypto';
import * as nodeFs from 'node:fs';
import * as nodeOs from 'node:os';
import * as nodePath from 'node:path';
import { isoCBOR } from '@simplewebauthn/server/helpers';
import {
	CHALLENGE_TTL_MS,
	MAX_CHALLENGES,
	listPasskeys,
	register,
	registrationOptions,
	relyingParty,
	removePasskey,
	resetPasskeys,
	signInOptions,
	verifySignIn
} from './passkeys';

// A software authenticator, so the real verification runs: an ES256 key, the
// authenticator data and the attestation object built as a platform authenticator
// builds them. What it cannot show is a real browser and a real fingerprint reader
// (GAPS); what it does show is every refusal this module promises.

const { mkdtempSync } = nodeFs as { mkdtempSync(prefix: string): string };
const { tmpdir } = nodeOs as { tmpdir(): string };
const { join } = nodePath as { join(...parts: string[]): string };
type Key = { export(o: { format: 'jwk' }): { x: string; y: string } };
const { createHash, generateKeyPairSync, randomBytes, sign } = nodeCrypto as unknown as {
	createHash(a: string): { update(d: Uint8Array | string): { digest(): Uint8Array } };
	generateKeyPairSync(t: 'ec', o: { namedCurve: string }): { publicKey: Key; privateKey: object };
	randomBytes(n: number): Uint8Array;
	sign(a: string, d: Uint8Array, k: object): Uint8Array;
};
declare const Buffer: {
	from(input: string | Uint8Array, encoding?: string): Uint8Array & { toString(encoding: string): string };
	concat(parts: Uint8Array[]): Uint8Array & { toString(encoding: string): string };
};

const ORIGIN = 'https://console.example';
const b64 = (bytes: Uint8Array) => Buffer.from(bytes).toString('base64url');
const sha256 = (data: Uint8Array | string) => createHash('sha256').update(data).digest();

class Authenticator {
	private keys = generateKeyPairSync('ec', { namedCurve: 'P-256' });
	readonly id = randomBytes(16);
	counter = 0;
	/** Flags: present 0x01, verified 0x04. */
	verified = true;

	private authData(rpID: string, attested: boolean): Uint8Array {
		const flags = 0x01 | (this.verified ? 0x04 : 0) | (attested ? 0x40 : 0);
		const count = new Uint8Array(4);
		new DataView(count.buffer).setUint32(0, this.counter);
		const head = [sha256(rpID), new Uint8Array([flags]), count];
		if (!attested) return Buffer.concat(head);
		const jwk = this.keys.publicKey.export({ format: 'jwk' });
		const cose = isoCBOR.encode(
			new Map<number, number | Uint8Array>([
				[1, 2],
				[3, -7],
				[-1, 1],
				[-2, Buffer.from(jwk.x, 'base64url')],
				[-3, Buffer.from(jwk.y, 'base64url')]
			])
		);
		return Buffer.concat([...head, new Uint8Array(16), new Uint8Array([0, this.id.length]), this.id, cose]);
	}

	private clientData(type: string, challenge: string, origin: string): Uint8Array {
		return Buffer.from(JSON.stringify({ type, challenge, origin, crossOrigin: false }));
	}

	create(challenge: string, origin = ORIGIN, rpID = 'console.example') {
		const attestationObject = isoCBOR.encode(
			new Map<string, unknown>([
				['fmt', 'none'],
				['attStmt', new Map()],
				['authData', this.authData(rpID, true)]
			]) as Parameters<typeof isoCBOR.encode>[0]
		);
		return {
			id: b64(this.id),
			rawId: b64(this.id),
			type: 'public-key',
			clientExtensionResults: {},
			response: {
				clientDataJSON: b64(this.clientData('webauthn.create', challenge, origin)),
				attestationObject: b64(attestationObject),
				transports: ['internal']
			}
		};
	}

	get(challenge: string, origin = ORIGIN, rpID = 'console.example') {
		this.counter += 1;
		const authData = this.authData(rpID, false);
		const clientDataJSON = this.clientData('webauthn.get', challenge, origin);
		const signature = sign('sha256', Buffer.concat([authData, sha256(clientDataJSON)]), this.keys.privateKey);
		return {
			id: b64(this.id),
			rawId: b64(this.id),
			type: 'public-key',
			clientExtensionResults: {},
			response: { clientDataJSON: b64(clientDataJSON), authenticatorData: b64(authData), signature: b64(signature) }
		};
	}
}

let env: Record<string, string>;
beforeEach(() => {
	resetPasskeys();
	env = {
		AURA_CONSOLE_ORIGIN: ORIGIN,
		AURA_CONSOLE_PASSWORD_FILE: join(mkdtempSync(join(tmpdir(), 'jarvis-passkeys-')), 'console-password')
	};
});

async function enrolled(): Promise<Authenticator> {
	const device = new Authenticator();
	await register(env, device.create((await registrationOptions(env)).challenge), 'Laptop');
	return device;
}

describe('where passkeys can exist', () => {
	it('needs the https address from the setting, and says why without it', () => {
		expect(relyingParty(env)).toEqual({ origin: ORIGIN, rpID: 'console.example' });
		expect(relyingParty({})).toHaveProperty('problem');
		expect(relyingParty({ AURA_CONSOLE_ORIGIN: 'https://console.example/hermes' })).toEqual({ origin: ORIGIN, rpID: 'console.example' });
		expect(relyingParty({ AURA_CONSOLE_ORIGIN: 'http://192.0.2.7:8199' })).toHaveProperty('problem');
		expect(relyingParty({ AURA_CONSOLE_ORIGIN: 'http://localhost:8199' })).toEqual({
			origin: 'http://localhost:8199',
			rpID: 'localhost'
		});
	});
});

describe('adding a passkey', () => {
	it('keeps the key, its name and nothing secret', async () => {
		const device = await enrolled();
		const kept = listPasskeys(env);
		expect(kept).toHaveLength(1);
		expect(kept[0]).toMatchObject({ id: b64(device.id), name: 'Laptop', counter: 0 });
	});

	it('asks the authenticator to verify the person and not to re-add one it holds', async () => {
		const device = await enrolled();
		const options = await registrationOptions(env);
		expect(options.authenticatorSelection?.userVerification).toBe('required');
		expect(options.excludeCredentials?.map((c) => c.id)).toEqual([b64(device.id)]);
	});

	it('refuses an answer to a challenge it did not issue, and one used twice', async () => {
		const device = new Authenticator();
		await expect(register(env, device.create(b64(randomBytes(32))), 'x')).rejects.toThrow();
		const { challenge } = await registrationOptions(env);
		await register(env, device.create(challenge), 'x');
		await expect(register(env, new Authenticator().create(challenge), 'y')).rejects.toThrow();
		expect(listPasskeys(env)).toHaveLength(1);
	});

	it('refuses an answer made for another site', async () => {
		const { challenge } = await registrationOptions(env);
		await expect(register(env, new Authenticator().create(challenge, 'https://evil.example'), 'x')).rejects.toThrow();
		const again = await registrationOptions(env);
		await expect(
			register(env, new Authenticator().create(again.challenge, ORIGIN, 'evil.example'), 'x')
		).rejects.toThrow();
		expect(listPasskeys(env)).toHaveLength(0);
	});

	it('refuses an authenticator that was only touched', async () => {
		const device = new Authenticator();
		device.verified = false;
		await expect(register(env, device.create((await registrationOptions(env)).challenge), 'x')).rejects.toThrow();
	});
});

describe('signing in with a passkey', () => {
	it('opens for a registered passkey answering our challenge, and counts its use', async () => {
		const device = await enrolled();
		const options = await signInOptions(env);
		expect(options.userVerification).toBe('required');
		expect(await verifySignIn(env, device.get(options.challenge))).toBe(b64(device.id));
		expect(listPasskeys(env)[0].counter).toBe(1);
	});

	it('refuses the same answer sent twice', async () => {
		const device = await enrolled();
		const answer = device.get((await signInOptions(env)).challenge);
		expect(await verifySignIn(env, answer)).not.toBeNull();
		expect(await verifySignIn(env, answer)).toBeNull();
	});

	it('refuses a challenge past its two minutes', async () => {
		const device = await enrolled();
		const { challenge } = await signInOptions(env, 1_000);
		expect(await verifySignIn(env, device.get(challenge), 1_000 + CHALLENGE_TTL_MS + 1)).toBeNull();
	});

	it('refuses a registration challenge used to sign in', async () => {
		const device = await enrolled();
		const { challenge } = await registrationOptions(env);
		expect(await verifySignIn(env, device.get(challenge))).toBeNull();
	});

	it('refuses a key this console never registered, even with the right id', async () => {
		const device = await enrolled();
		const stranger = new Authenticator();
		Object.defineProperty(stranger, 'id', { value: device.id });
		expect(await verifySignIn(env, stranger.get((await signInOptions(env)).challenge))).toBeNull();
		expect(await verifySignIn(env, new Authenticator().get((await signInOptions(env)).challenge))).toBeNull();
	});

	it('refuses another site, a touch without verification, and a counter that went back', async () => {
		const device = await enrolled();
		const next = async () => (await signInOptions(env)).challenge;
		expect(await verifySignIn(env, device.get(await next(), 'https://evil.example'))).toBeNull();
		expect(await verifySignIn(env, device.get(await next(), ORIGIN, 'evil.example'))).toBeNull();
		device.verified = false;
		expect(await verifySignIn(env, device.get(await next()))).toBeNull();
		device.verified = true;
		expect(await verifySignIn(env, device.get(await next()))).not.toBeNull();
		device.counter = 0; // a clone replaying from an older state
		expect(await verifySignIn(env, device.get(await next()))).toBeNull();
	});

	it('a flood of sign-in asks does not push out a registration in progress', async () => {
		const { challenge } = await registrationOptions(env);
		for (let i = 0; i < MAX_CHALLENGES + 5; i += 1) await signInOptions(env);
		await register(env, new Authenticator().create(challenge), 'Laptop');
		expect(listPasskeys(env)).toHaveLength(1);
	});

	it('is gone once removed, and nothing signs in without the setting', async () => {
		const device = await enrolled();
		expect(await verifySignIn({ ...env, AURA_CONSOLE_ORIGIN: '' }, device.get((await signInOptions(env)).challenge))).toBeNull();
		expect(removePasskey(env, b64(device.id))).toBe(true);
		expect(removePasskey(env, b64(device.id))).toBe(false);
		expect(await verifySignIn(env, device.get((await signInOptions(env)).challenge))).toBeNull();
	});
});
