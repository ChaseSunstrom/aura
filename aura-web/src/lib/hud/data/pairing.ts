// `panel.pairing` from the console's pairing endpoints.
//
// The QR carries `jarvis://pair?v=1&u=<url>&c=<code>` — what the phone's
// `PairingPayload.kt` parses — and the code under it is the same code in the
// groups it is read in. The SVG is inlined as a data URL because the body takes
// an `<img>` source: the encoder is the console's own and fetches nothing.

import { qrSvg } from '$lib/qr';
import { wornName } from '$lib/ds/theme.svelte';

export interface PairingSources {
	/** The console's own origin, as the phone will reach it. */
	url: string;
	code: string;
	/** Epoch seconds the code stops working, or 0 for none. */
	expiresAt: number;
	now?: number;
}

export interface PairingView {
	qr: string;
	/** The same payload as a link: "Open in the desktop app", which pairs the installed app (ADR-0051). */
	link: string;
	code: string;
	expires: string;
	/** Seconds the code has left; 0 when it has none or there is no code. */
	secondsLeft: number;
}

/** `jarvis://pair?v=1&u=<url>&c=<code>`; both values percent-encoded, one rule for both. */
export function pairingPayload(url: string, code: string): string {
	return `jarvis://pair?v=1&u=${encodeURIComponent(url)}&c=${encodeURIComponent(code)}`;
}

/** "4812 9917" — groups of four, so the digits are read in fours and not as a number. */
export function groupCode(code: string): string {
	const clean = code.replace(/\s+/g, '');
	return clean.replace(/(.{4})(?=.)/g, '$1 ');
}

export function pairingView(sources: PairingSources): PairingView {
	const now = sources.now ?? Date.now();
	const secondsLeft = sources.expiresAt ? Math.max(0, Math.round(sources.expiresAt - now / 1000)) : 0;
	const live = Boolean(sources.code) && secondsLeft > 0;
	if (!live) return { qr: '', link: '', code: '', expires: '', secondsLeft: 0 };
	const link = pairingPayload(sources.url, sources.code);
	const svg = qrSvg(link, { title: `${wornName()} pairing code` });
	const minutes = Math.floor(secondsLeft / 60);
	const seconds = secondsLeft % 60;
	return {
		qr: `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`,
		link,
		code: groupCode(sources.code),
		expires:
			minutes > 0
				? `Good for ${minutes} minute${minutes === 1 ? '' : 's'}${seconds ? ` and ${seconds} seconds` : ''}.`
				: `Good for ${seconds} second${seconds === 1 ? '' : 's'}.`,
		secondsLeft
	};
}
