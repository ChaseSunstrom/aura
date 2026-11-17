// The Hermes link, as Svelte sees it.
//
// `HermesLink` is plain TypeScript so it runs under Vitest; this is the one
// rune wrapper over it: a `$state.raw` snapshot that every component reads
// and the link replaces wholesale on each event. Raw, not deep — the snapshot
// is rebuilt by the reducer and never mutated in place, so proxying its
// arrays on every token would be work for nothing.
//
// The layout constructs the pair once and hands it down through context; a
// component reads it with `useHermes()`, which is null on the server or in a
// test that rendered the component alone — so a component draws nothing
// rather than throwing.

import { getContext, setContext } from 'svelte';
import { HermesLink, type HermesSnapshot } from './link';

export const HERMES_CONTEXT = 'hermes-link';

export class HermesView {
	snap = $state.raw<HermesSnapshot>(undefined as unknown as HermesSnapshot);
	private unsub: () => void;

	constructor(readonly link: HermesLink) {
		this.snap = link.current;
		this.unsub = link.subscribe((s) => (this.snap = s));
	}

	dispose(): void {
		this.unsub();
	}
}

export interface HermesContext {
	link: HermesLink;
	view: HermesView;
}

export function provideHermes(link: HermesLink): HermesContext {
	const ctx: HermesContext = { link, view: new HermesView(link) };
	setContext(HERMES_CONTEXT, ctx);
	return ctx;
}

export function useHermes(): HermesContext | null {
	try {
		return getContext<HermesContext | undefined>(HERMES_CONTEXT) ?? null;
	} catch {
		return null;
	}
}
