// What a phone that redeemed a pairing code at the console gets besides the
// house's token (`routes/api/pair/claim`).
import { resolveAdapter } from './hermes';

/** What a claim may say it is; `tests/contracts/pairing-claim.json` is the table all four ends read. */
export const CLAIM_KINDS = ['phone', 'desktop', 'other'] as const;
export const UNNAMED_KIND = 'other';
/** The desktop agent holds a house token alone; it never talks to the adapter. */
export const NO_ADAPTER_KEY = ['desktop'] as const;

/**
 * A spent code also carries an adapter key of the device's own (T-0569): the adapter's
 * pairing payload (the phone-reachable url, the model) with a key minted for this device by
 * the adapter's `POST /api/pair`, named and kinded by the claim and holding the house's
 * token id, so the console's Devices screen can later revoke both at once. Without it a
 * phone paired from this panel had the house alone, whose voice pipeline has no agent of
 * its own — "Sorry, no conversation agent is configured yet" (the owner, 2026-09-25).
 *
 * The master key (AURA_ADAPTER_KEY) is sent only to the adapter, server side, and never
 * appears in the answer: before T-0569 every phone held it, so locking out one meant
 * rotating it on all of them. A device paired before keeps the master key until it is
 * paired again.
 *
 * Null — and the house's answer unchanged — for a desktop claim, or when the adapter is not
 * configured, unreachable, or answers anything but a minted key.
 */
export async function withAdapter(
	claim: string,
	asked: string,
	env: Record<string, string | undefined>,
	fetcher: typeof fetch = fetch
): Promise<Record<string, unknown> | null> {
	const adapter = resolveAdapter(env);
	if (!adapter.configured) return null;
	let body: Record<string, unknown>;
	try {
		body = JSON.parse(claim);
	} catch {
		return null;
	}
	let request: Record<string, unknown> = {};
	try {
		request = JSON.parse(asked) ?? {};
	} catch {
		/* the house took it; only its kind and name are read here */
	}
	const kind = (CLAIM_KINDS as readonly unknown[]).includes(request.kind) ? String(request.kind) : UNNAMED_KIND;
	if ((NO_ADAPTER_KEY as readonly string[]).includes(kind)) return null;
	const name = typeof body.name === 'string' ? body.name : typeof request.name === 'string' ? request.name : '';
	const answer = await fetcher(`${adapter.url}/api/pair`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${adapter.key}`, 'Content-Type': 'application/json' },
		body: JSON.stringify({ name, kind, house_token: typeof body.token_id === 'string' ? body.token_id : '' }),
		redirect: 'error'
	}).catch(() => null);
	if (!answer?.ok) return null;
	const pair = (await answer.json().catch(() => null)) as Record<string, unknown> | null;
	if (
		pair?.kind !== 'hermes-adapter' ||
		typeof pair.url !== 'string' ||
		typeof pair.key !== 'string' ||
		pair.key === adapter.key
	) {
		return null;
	}
	return { ...body, adapter: { url: pair.url, key: pair.key, model: pair.model ?? '', device: pair.device ?? null } };
}
