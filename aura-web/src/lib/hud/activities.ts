// The console's activities: every path the console ever had. The v2 shell has
// no layouts; `$lib/ds/shell/routes.ts` reads this table only to know a path
// is the console's (the core, or a screen) rather than a 404. Every path here
// keeps resolving because the phone's strip, a bookmark and a note all still
// carry them; the `MOVED` aliases of the old `screens.ts` are rows here too.
//
// The `layout`, `focus` and `opens` columns are what the legacy window manager
// opened; they stay because the phone's tab order is held to this table
// (`android-app/tools/console_parity_test.py`) and the e2e specs walk it.
// Does not cover: which v2 screen a path opens (`screenOf` in routes.ts).

/** A panel a legacy layout opened: its id and type. */
interface PanelSpec {
	id: string;
	type: string;
}

export interface Activity {
	/** The shipped layout it opens, and the settings key its arrangement is saved under. */
	layout: 'Working' | 'House' | 'Knowledge' | 'Settings' | 'Dashboards' | 'Car';
	/** The panel the route is about: focused on arrival, opened if the layout lacks it. */
	focus?: string;
	/** A panel the shipped layout does not carry, placed beside `focus`'s neighbour. */
	opens?: { spec: PanelSpec; beside: string; edge?: 'top' | 'bottom' | 'left' | 'right' };
	/** The document title. */
	title: string;
	/** A full screen over the room rather than a layout's panel: the chat (2026-09-27), or the Ops board (BUILD_BRIEF §1). */
	screen?: 'chat' | 'ops';
}

const KANBAN: Activity['opens'] = { spec: { id: 'kanban', type: 'kanban' }, beside: 'chat', edge: 'bottom' };

/**
 * Path → activity. The lookup is exact; the order is the phone's tab order
 * (`ConsoleTab.kt`, held to this table by `android-app/tools/console_parity_test.py`).
 */
export const ACTIVITIES: Readonly<Record<string, Activity>> = {
	'/': { layout: 'Working', title: 'Core' },

	// The chat, expanded: every conversation and the open one, full screen (2026-09-27).
	'/chat': { layout: 'Working', focus: 'chat', screen: 'chat', title: 'Chat' },

	// The car display (2026-09-25): the reactor, and the car's panels from the phone.
	'/car': { layout: 'Car', title: 'Car' },

	'/dashboards': { layout: 'Dashboards', focus: 'telemetry', title: 'Dashboards' },
	// The rail's sections that had no path of their own (BUILD_BRIEF §1, `sections.ts`):
	// the Dashboards layout already holds their panels, each focused on arrival.
	'/notifications': { layout: 'Dashboards', focus: 'notifications', title: 'Notifications' },
	'/jobs': { layout: 'Dashboards', focus: 'jobs', title: 'Jobs' },
	'/telemetry': { layout: 'Dashboards', focus: 'telemetry', title: 'Telemetry' },
	'/ops': { layout: 'Dashboards', focus: 'telemetry', screen: 'ops', title: 'Ops' },

	'/house': { layout: 'House', focus: 'house', title: 'House' },
	'/house/devices': { layout: 'House', focus: 'house', title: 'Devices' },
	'/house/areas': { layout: 'House', focus: 'house', title: 'Areas' },
	'/house/automations': { layout: 'House', focus: 'automations', title: 'Automations' },

	'/work': { layout: 'Working', focus: 'kanban', opens: KANBAN, title: 'Work' },
	'/work/tasks': { layout: 'Working', focus: 'kanban', opens: KANBAN, title: 'Tasks' },
	'/work/goals': { layout: 'Working', focus: 'goals', title: 'Goals' },
	'/work/code': { layout: 'Working', focus: 'kanban', opens: KANBAN, title: 'Code' },

	'/knowledge': { layout: 'Knowledge', focus: 'memory', title: 'Knowledge' },
	'/knowledge/memory': { layout: 'Knowledge', focus: 'memory', title: 'Memory' },
	'/knowledge/notes': { layout: 'Knowledge', focus: 'document', title: 'Notes' },

	'/settings': { layout: 'Settings', focus: 'settings', title: 'Settings' },
	'/settings/assistant': { layout: 'Settings', focus: 'settings', title: 'Assistant' },
	'/settings/house': { layout: 'Settings', focus: 'settings', title: 'House settings' },
	'/settings/system': { layout: 'Settings', focus: 'settings', title: 'System' },
	'/settings/voice': {
		layout: 'Settings',
		focus: 'voice',
		opens: { spec: { id: 'voice', type: 'voice' }, beside: 'model', edge: 'bottom' },
		title: 'Voice settings'
	},
	'/settings/console': {
		layout: 'Settings',
		focus: 'pairing',
		opens: { spec: { id: 'pairing', type: 'pairing' }, beside: 'model', edge: 'bottom' },
		title: 'Console'
	},
	'/settings/models': {
		layout: 'Settings',
		focus: 'models',
		opens: { spec: { id: 'models', type: 'models' }, beside: 'settings', edge: 'right' },
		title: 'Models'
	},
	'/settings/tools': {
		layout: 'Settings',
		focus: 'integrations',
		opens: { spec: { id: 'integrations', type: 'integrations' }, beside: 'settings', edge: 'right' },
		title: 'Tools'
	},
};

/**
 * Where a path from before the consolidation lives now — `screens.ts`'s
 * `MOVED`, carried over so nothing that used to resolve stops resolving.
 */
export const ALIASES: Readonly<Record<string, string>> = {
	'/models': '/settings/models',
	'/routines': '/house/automations',
	'/integrations': '/settings/tools',
	'/devices': '/house/devices',
	'/areas': '/house/areas',
	'/house/dashboards': '/dashboards',
	'/automations': '/house/automations',
	'/tasks': '/work/tasks',
	'/code': '/work/code',
	'/notes': '/knowledge/notes',
	'/memory': '/knowledge/memory',
	'/tools': '/settings/tools',
	'/desktop': '/settings/console',
	'/settings/desktop': '/settings/console'
};

/** A path with its trailing slash gone, so `/house/` and `/house` are one row. */
export function normalisePath(path: string): string {
	const trimmed = path.replace(/\/+$/, '');
	return trimmed === '' ? '/' : trimmed;
}

export interface Resolved {
	path: string;
	activity: Activity;
	/** `/work/tasks/<id>`: the task the route names, opened in its own panel. */
	taskId?: string;
}

/** The activity a path resolves to, following an alias, or null for a path the console has no layout for. */
export function resolveActivity(rawPath: string): Resolved | null {
	const path = normalisePath(ALIASES[normalisePath(rawPath)] ?? rawPath);
	const task = /^\/(?:work\/)?tasks\/([^/]+)$/.exec(path);
	if (task) {
		return {
			path: `/work/tasks/${task[1]}`,
			activity: {
				layout: 'Working',
				focus: `task:${task[1]}`,
				opens: { spec: { id: `task:${task[1]}`, type: 'task' }, beside: 'chat', edge: 'bottom' },
				title: 'Task'
			},
			taskId: task[1]
		};
	}
	const activity = ACTIVITIES[path];
	return activity ? { path, activity } : null;
}
