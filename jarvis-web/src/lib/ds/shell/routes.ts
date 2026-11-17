// What a path means in the v2 shell: the core (the stage, with whatever the
// moment projects), or one of the few full screens the launcher lists.
//
// The legacy console had a layout per path (`hud/activities.ts`); the v2 shell
// has no permanent panels, so most of those paths are the core. They keep
// resolving rather than 404ing because the phone's strip, bookmarks and notes
// still carry them. The screens are the ones the boards draw: Settings,
// Integrations, Approvals, Texts, Devices, Chat, Ops, the Board, Models and
// the car.
//
// Does not cover: what a screen shows (lib/ds/screens), or a path the server
// refuses (`+page.server.ts` owns 404s).

// Relative, not `$lib`: the Playwright specs import this table in Node, where the
// SvelteKit aliases do not exist (playwright.config.ts), to walk every path.
import { normalisePath, resolveActivity } from '../../hud/activities';
import { DEFAULT_THEME, THEMES } from '../../themes';

export const SCREENS = ['core', 'chat', 'approvals', 'texts', 'devices', 'integrations', 'settings', 'ops', 'board', 'models', 'car', 'setup'] as const;
export type Screen = (typeof SCREENS)[number];

const EXACT: Readonly<Record<string, Screen>> = {
	'/chat': 'chat',
	'/approvals': 'approvals',
	'/messages': 'texts',
	'/texts': 'texts',
	'/devices': 'devices',
	'/desktop': 'devices',
	'/settings/console': 'devices',
	'/settings/desktop': 'devices',
	'/integrations': 'integrations',
	'/tools': 'integrations',
	'/settings/tools': 'integrations',
	'/ops': 'ops',
	'/board': 'board',
	'/models': 'models',
	'/settings/models': 'models',
	'/car': 'car',
	// The first-run walkthrough (T-0629): reached from Settings, never listed in the launcher.
	'/setup': 'setup'
};

/** The screen a path opens. Anything not a screen is the core. */
export function screenOf(path: string): Screen {
	const p = normalisePath(path);
	if (Object.hasOwn(EXACT, p)) return EXACT[p];
	return p === '/settings' || p.startsWith('/settings/') ? 'settings' : 'core';
}

/**
 * Whether the console has anything at a path: a screen, or a legacy layout's
 * path (now the core). Anything else is a 404 with a real status, so a typo'd
 * `/settings/nowhere` is not a 200 that a link checker counts as fine.
 */
export function knownPath(path: string): boolean {
	const p = normalisePath(path);
	return Object.hasOwn(EXACT, p) || resolveActivity(p) !== null;
}

export interface App {
	screen: Exclude<Screen, 'core' | 'car' | 'setup'>;
	path: string;
	label: string;
	/** One line on what it is for, in the launcher. */
	hint: string;
}

/** The launcher's apps, in the order a person reaches for them. The car's display is not one: it is its own device. */
export const APPS: readonly App[] = [
	{ screen: 'chat', path: '/chat', label: 'Chat', hint: 'Every conversation, full screen' },
	{ screen: 'approvals', path: '/approvals', label: 'Approvals', hint: 'What is waiting for your OK' },
	{ screen: 'texts', path: '/messages', label: 'Texts', hint: 'Messages from your phone' },
	{ screen: 'devices', path: '/devices', label: 'Devices', hint: 'Your phone, desktop and watch' },
	{ screen: 'integrations', path: '/integrations', label: 'Integrations', hint: 'Connect services and tools' },
	{ screen: 'settings', path: '/settings', label: 'Settings', hint: 'You, voices, privacy' },
	{ screen: 'ops', path: '/ops', label: 'Ops', hint: 'The house’s machines at a glance' },
	{ screen: 'board', path: '/board', label: 'Board', hint: 'What was done today' },
	{ screen: 'models', path: '/models', label: 'Models', hint: 'Load, add and download the house’s models' }
];

/** The document title for a screen: the core's and the car's is the worn persona's name (T-0928). */
export function titleOf(screen: Screen, name = THEMES[DEFAULT_THEME].persona.name): string {
	if (screen === 'core' || screen === 'car') return name;
	if (screen === 'setup') return 'Setup';
	return APPS.find((a) => a.screen === screen)?.label ?? name;
}
