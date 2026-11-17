/**
 * What the desktop app's assist popup (`/?popup=1`) is doing, said in its
 * document title so the app can keep it on screen while it matters (the
 * owner, 2026-09-25: "if it asks for approval ... I can't click it without
 * closing Jarvis ... could we have it like unfocus so I can move around my
 * screen and do stuff while it works ... things it can pull up for
 * notifications"). The app reads the title (`aura-desktop-app` shell.rs
 * `mood_of`): waiting on the person or a fresh notice pulls the popup up
 * without the focus, a running turn keeps it up, idle lets it go.
 *
 * The title is the channel because the popup is the console's own page on the
 * console's origin, which has no bridge into the app, and a title change is
 * what every webview reports to its window.
 */
import type { ReactorStateName } from '$lib/reactor';

export type PopupMood = 'idle' | 'working' | 'needs you' | 'notice';

/** The reactor's resting states; everything else is a turn in flight. */
const AT_REST: readonly ReactorStateName[] = ['idle', 'offline'];

export function popupMood(o: { waiting: number; notice: boolean; reactor: ReactorStateName }): PopupMood {
	if (o.waiting > 0) return 'needs you';
	if (o.notice) return 'notice';
	return AT_REST.includes(o.reactor) ? 'idle' : 'working';
}

/**
 * `Jarvis`, `Jarvis · working`; ` · pill` after it while the island is its pill, so the app
 * shrinks the window round it (shell.rs `popup_size`, T-0695).
 */
export const popupTitle = (name: string, mood: PopupMood, pill = false, away = false, dictated = ''): string =>
	// The name is the page's own to choose, so every middle dot is taken out of it (not just the
	// spaced separator: `a · · b` would leave one): it cannot open a forged `typed:` segment.
	[name.replaceAll('·', ' ').replace(/\s+/g, ' ').trim(), ...(mood === 'idle' ? [] : [mood]), ...(dictated ? [dictated] : []), ...(pill ? ['pill'] : []), ...(away ? ['away'] : [])].join(' · ');

/**
 * The desktop app's second global key (T-0696, shell.rs `summon_to_type`) raises this window and
 * dispatches this event on it; `detail.to` says why. The popup is the console's own page and has
 * no bridge into the app, so, like `jarvis:push-to-talk`, the app calls in rather than the page out.
 */
export const SUMMON_EVENT = 'jarvis:summon';

/** The summon asks for the typing line: focus it, and open the island if Escape had put it away. */
export const summonsToType = (e: Event): boolean => (e as CustomEvent<{ to?: string } | null>).detail?.to === 'type';

/**
 * The popup's ladder (BUILD_BRIEF §1 "Desktop popup"; the Build Brief's Popups board):
 * a compact always-on-top HUD at rest, the listening pill while the mic is
 * recording, and the full assistant once there is a turn, an answer or
 * something waiting.
 */
export type PopupRung = 'hud' | 'listening' | 'assistant';

export function popupRung(o: { recording: boolean; mood: PopupMood; exchange: boolean; expanded: boolean }): PopupRung {
	if (o.recording) return 'listening';
	if (o.expanded || o.exchange || o.mood !== 'idle') return 'assistant';
	return 'hud';
}

/** How long a notice that just arrived stays pulled up on the popup. */
export const NOTICE_MS = 8000;
