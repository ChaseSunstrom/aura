/**
 * The console's uncaught errors reach the adapter's log (T-0475): on the phone the page runs in
 * a WebView, where a broken console says nothing anyone can read. Nothing is sent while a
 * private chat is on (T-0462). Not covered: errors a component catches itself, a WebGL context
 * loss (a canvas event, not an error), and an error's message quoting what the page held (cut to
 * 300 characters by the adapter, not scrubbed).
 */
export const PER_PAGE = 10;
const ROUTE = '/hermes/api/client-error';

/** Listen on [target] (the window); returns the function that stops listening. */
export function reportErrors(
	privately: () => boolean,
	fetcher: typeof fetch = (input, init) => fetch(input, init),
	target: EventTarget = window
): () => void {
	const seen = new Set<string>();
	const send = (report: Record<string, unknown>) => {
		if (privately() || seen.size >= PER_PAGE) return;
		// A repeat (a ResizeObserver loop fires all session) is sent once, so it cannot spend
		// the cap a later error needs.
		const key = JSON.stringify(report);
		if (seen.has(key)) return;
		seen.add(key);
		const init = { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(report), keepalive: true };
		// A report that fails is dropped: reporting it would loop.
		fetcher(ROUTE, init).catch(() => {});
	};
	const onError = (e: Event) => {
		const { message, filename, lineno } = e as ErrorEvent;
		send({ kind: 'error', message: String(message ?? ''), source: filename ?? '', line: lineno ?? 0 });
	};
	const onRejection = (e: Event) => {
		const reason = (e as PromiseRejectionEvent).reason;
		send({ kind: 'rejection', message: reason instanceof Error ? reason.message : String(reason) });
	};
	target.addEventListener('error', onError);
	target.addEventListener('unhandledrejection', onRejection);
	return () => {
		target.removeEventListener('error', onError);
		target.removeEventListener('unhandledrejection', onRejection);
	};
}
