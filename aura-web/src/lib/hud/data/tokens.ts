// The Integrations screen's House keys rows (T-1386; the section was headed Devices until T-1411): the house's `config/token/list`, one row per stored token.
//
// The house sends no token and no digest (`TokenInfo.as_dict`); this copies only the fields it
// shows, so one that ever did would still not reach a row.
//
// Does not cover: the house's environment token (the house does not list it, so a console signed in
// with it has no row marked and every listed key can be revoked), or telling a token
// stored before pairing was recorded (T-1262) from one minted by hand or at first run: the house
// keeps no mark that would, so all three read "Older token".

import { agoOf } from './time';

export interface TokenRow {
	id: string;
	name: string;
	state: 'paired' | 'older';
	/** The state in words; colour is only a second signal. */
	word: string;
	/** When it was last used, and whether a socket holds it open now. */
	detail: string;
	/** What the person should know before acting; empty for a paired device. */
	note: string;
	/** May answer held requests. Null when not paired: the house reads the flag only for a paired token. */
	approver: boolean | null;
	/** False for the row that is this console's own way in: the house's `current`. */
	revocable: boolean;
	/** True for that row (the house marks it; an older house is guessed at, see `tokensView`). */
	current: boolean;
}

/** Null when the house did not answer with a list: an error, never "no devices" over live credentials. */
export function tokensView(reply: unknown, now = Date.now()): TokenRow[] | null {
	if (!Array.isArray(reply)) return null;
	const out: TokenRow[] = [];
	const seen = new Set<string>();
	for (const t of reply as unknown[]) {
		if (!t || typeof t !== 'object') continue;
		const raw = t as Record<string, unknown>;
		const id = raw.id;
		if (typeof id !== 'string' || !id || id.length > 64 || seen.has(id)) continue;
		seen.add(id);
		const paired = raw.paired === true;
		const connected = raw.connected === true;
		const used = raw.last_used_at;
		const last = typeof used === 'number' && Number.isFinite(used) ? `Last used ${agoOf(used * 1000, now)}` : 'Never used';
		// The house says which row is the caller's own (`current`, T-1407): revoking it would sign this
		// console out of the house. Only a real `true` counts. A house with no such field (older than
		// T-1407) cannot say, so the old guess stands for it alone: an unpaired key with a socket open
		// is treated as the caller's, the safe side.
		const own = 'current' in raw ? raw.current === true : !paired && connected;
		out.push({
			id,
			name: (typeof raw.name === 'string' && raw.name.trim().slice(0, 60)) || 'Unnamed token',
			state: paired ? 'paired' : 'older',
			word: own ? 'This console' : paired ? 'Paired' : 'Older token',
			detail: connected ? `${last} · connected now` : last,
			note: own
				? 'The key this console signed in with. Revoking it would sign you out of the house, so it is not offered here. Revoke it from another device if you must.'
				: paired
					? ''
					: 'Stored before pairing was recorded, or made by hand. Pair that device again to give it a key of its own, then revoke this one.',
			// A missing field is an approver, as in the house (`TokenInfo.approver` defaults to true).
			approver: paired ? raw.approver !== false : null,
			revocable: !own,
			current: own
		});
	}
	return out;
}
