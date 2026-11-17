// The house socket, as Svelte sees it — the `ConsoleLink` counterpart of
// `hermes/store.svelte.ts`.
//
// The layout owns the one `ConsoleLink` (it reconnects; the panels' rows ride
// on it) and hands it down through context; the HUD host reads its snapshot
// as a rune and asks it for the live connection. Null on the server and in a
// test that rendered a component alone, so a component draws nothing rather
// than throwing.

import { getContext, setContext } from 'svelte';
import type { ConsoleLink, LinkSnapshot } from '$lib/consoleLink';

export const CONSOLE_CONTEXT = 'console-link';

export class ConsoleView {
	snap = $state.raw<LinkSnapshot>(undefined as unknown as LinkSnapshot);
	private readonly unsub: () => void;

	constructor(readonly link: ConsoleLink) {
		this.snap = link.current;
		this.unsub = link.subscribe((s) => (this.snap = s));
	}

	dispose(): void {
		this.unsub();
	}
}

export function provideConsole(link: ConsoleLink): ConsoleView {
	const view = new ConsoleView(link);
	setContext(CONSOLE_CONTEXT, view);
	return view;
}

export function useConsole(): ConsoleView | null {
	try {
		return getContext<ConsoleView | undefined>(CONSOLE_CONTEXT) ?? null;
	} catch {
		return null;
	}
}
