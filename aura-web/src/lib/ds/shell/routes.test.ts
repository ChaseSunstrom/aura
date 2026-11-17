import { describe, expect, it } from 'vitest';

import { ACTIVITIES, ALIASES } from '$lib/hud/activities';
import { APPS, SCREENS, knownPath, screenOf, titleOf } from './routes';

describe('screenOf', () => {
	it('the root and the legacy layouts are the core', () => {
		for (const p of ['/', '/house', '/work/tasks', '/knowledge/memory', '/dashboards', '/nowhere']) expect(screenOf(p)).toBe('core');
	});

	it('every settings path is the one short settings screen, but tools are integrations', () => {
		expect(screenOf('/settings')).toBe('settings');
		expect(screenOf('/settings/voice/')).toBe('settings');
		expect(screenOf('/settings/tools')).toBe('integrations');
		expect(screenOf('/integrations')).toBe('integrations');
	});

	it('the models have their own screen, at the launcher\'s path and the legacy one (T-1254)', () => {
		expect(screenOf('/models')).toBe('models');
		expect(screenOf('/settings/models')).toBe('models');
		expect(titleOf('models')).toBe('Models');
	});

	it('every path the legacy console resolved still resolves to a screen', () => {
		for (const p of [...Object.keys(ACTIVITIES), ...Object.keys(ALIASES)]) expect(SCREENS).toContain(screenOf(p));
	});

	it('an inherited key is not a path', () => {
		expect(screenOf('/constructor')).toBe('core');
	});
});

describe('knownPath', () => {
	it('knows every app, every legacy path and the root; anything else is a 404', () => {
		for (const p of ['/', ...APPS.map((a) => a.path), '/texts', '/car', ...Object.keys(ACTIVITIES), ...Object.keys(ALIASES)]) expect(knownPath(p), p).toBe(true);
		for (const p of ['/nowhere', '/settings/nowhere', '/constructor']) expect(knownPath(p), p).toBe(false);
	});
});

describe('APPS', () => {
	it('each app opens its own screen', () => {
		for (const a of APPS) expect(screenOf(a.path)).toBe(a.screen);
		expect(new Set(APPS.map((a) => a.screen)).size).toBe(APPS.length);
	});

	it('titles name the screen, the core the worn persona (T-0928)', () => {
		expect(titleOf('core')).toBe('AURA');
		expect(titleOf('core', 'JARVIS')).toBe('JARVIS');
		expect(titleOf('car', 'JARVIS')).toBe('JARVIS');
		expect(titleOf('approvals', 'JARVIS')).toBe('Approvals');
	});
});
