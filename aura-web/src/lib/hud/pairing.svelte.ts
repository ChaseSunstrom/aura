// The pairing flow behind `panel.pairing`: the console lock, the secret, the
// code.
//
// Ported from `components/Pairing.svelte` with the parts the panel draws —
// the lock, a code, its expiry — and without the parts that were console
// administration in a settings section (choosing the console password,
// adopting a pairing secret, revealing it, revoking tokens). Those are the
// operator's, not the phone's, and the body has a QR and a code (§9.1).
// The password is held in memory only, never in storage: the session cookie
// the server sets is httpOnly, so what survives a reload is something page
// JavaScript cannot read back.

import { describeError } from '$lib/connection';

export type Lock = 'unknown' | 'choose' | 'locked' | 'open';

export class PairingFlow {
	lock = $state<Lock>('unknown');
	/** The server holds a pairing secret; never the secret itself. */
	secretHeld = $state(false);
	code = $state('');
	/** Epoch seconds the code stops working; 0 for none. */
	expiresAt = $state(0);
	error = $state('');
	busy = $state(false);
	/** What the console told us about the lock, for the copy. */
	minChars = $state(10);
	passwordHint = $state('');

	private timer: ReturnType<typeof setInterval> | null = null;
	/** A tick, so the expiry sentence and the QR follow the clock. */
	now = $state(Date.now());

	/** The console's own origin, as the phone will reach it. */
	get url(): string {
		return typeof location === 'undefined' ? '' : location.origin;
	}

	get secondsLeft(): number {
		return this.expiresAt ? Math.max(0, Math.round(this.expiresAt - this.now / 1000)) : 0;
	}

	get live(): boolean {
		return Boolean(this.code) && this.secondsLeft > 0;
	}

	async start(): Promise<void> {
		// Once per activation. The host calls this from an effect that re-runs
		// on every layout snapshot, and each call re-read the lock: a read that
		// was in flight when the password was posted came back "locked" after
		// the unlock had opened the panel, and the secret prompt flipped back
		// to the password prompt (hud-house.spec, the pairing test, on the
		// first run of the full set). `dispose` clears the timer, so a panel
		// that closes and reopens starts again.
		if (this.timer) return;
		this.timer = setInterval(() => (this.now = Date.now()), 1000);
		await this.loadLock();
		await this.loadSecret();
		if (this.lock === 'open' && this.secretHeld && !this.live) await this.issue();
	}

	dispose(): void {
		if (this.timer) clearInterval(this.timer);
		this.timer = null;
	}

	private async failure(res: Response, fallback: string): Promise<string> {
		const detail = await res.json().catch(() => null);
		return detail?.message ?? `${fallback} (${res.status})`;
	}

	async loadLock(): Promise<void> {
		try {
			const res = await fetch('/api/console');
			if (!res.ok) throw new Error(await this.failure(res, 'the console could not be read'));
			const body = await res.json();
			this.minChars = body.minChars ?? this.minChars;
			this.passwordHint = body.problem ?? '';
			this.lock = body.authenticated ? 'open' : body.configured ? 'locked' : 'choose';
			this.error = '';
		} catch (e) {
			this.error = describeError(e);
		}
	}

	async loadSecret(): Promise<void> {
		try {
			const res = await fetch('/api/pair/secret');
			if (!res.ok) return;
			const body = await res.json();
			this.secretHeld = Boolean(body.held);
		} catch {
			this.secretHeld = false;
		}
	}

	/** Prove the password (or choose it, the first time), then issue a code. */
	async unlock(password: string): Promise<void> {
		if (!password.trim() || this.busy) return;
		this.busy = true;
		this.error = '';
		try {
			const res = await fetch('/api/console', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ password })
			});
			if (!res.ok) throw new Error(await this.failure(res, 'the console refused that password'));
			this.lock = 'open';
			await this.loadSecret();
		} catch (e) {
			this.error = describeError(e);
		} finally {
			this.busy = false;
		}
		if (this.lock === 'open' && this.secretHeld) await this.issue();
	}

	/**
	 * Hand the pairing secret to the server, once: a console whose environment
	 * has none cannot mint a code until the operator types it. Never kept here.
	 */
	async adoptSecret(secret: string): Promise<void> {
		if (!secret.trim() || this.busy) return;
		this.busy = true;
		this.error = '';
		try {
			const res = await fetch('/api/pair/secret', {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ secret })
			});
			if (!res.ok) throw new Error(await this.failure(res, 'the console would not take that secret'));
			await this.loadSecret();
		} catch (e) {
			this.error = describeError(e);
		} finally {
			this.busy = false;
		}
		if (this.secretHeld) await this.issue();
	}

	/** A new code. The old one, if any, stops working when the server says so. */
	async issue(): Promise<void> {
		if (this.busy) return;
		this.busy = true;
		this.error = '';
		try {
			const res = await fetch('/api/pair', { method: 'POST' });
			if (!res.ok) {
				if (res.status === 401) this.lock = 'locked';
				throw new Error(await this.failure(res, 'pairing failed'));
			}
			const body = await res.json();
			this.code = body.code;
			this.expiresAt = body.expires_at ?? Date.now() / 1000 + (body.ttl ?? 300);
		} catch (e) {
			this.error = describeError(e);
			this.code = '';
			await this.loadSecret();
		} finally {
			this.busy = false;
		}
	}
}
