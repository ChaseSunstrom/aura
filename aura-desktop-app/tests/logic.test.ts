import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
	agentLine,
	askView,
	choicesFor,
	newestFirst,
	OVERLAY_HIDDEN,
	overlayFrame,
	policyArgs,
	revealPlan,
	sectionOf,
	updateLine,
	verdictFor
} from '../ui/logic';

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), 'utf8');

describe('the overlay', () => {
	const flat = { left: 0, top: 0, scale: 1 };

	it('draws the path, the highlight, the ripple, the caret, the scroll and the keys where the agent says', () => {
		let view = overlayFrame(OVERLAY_HIDDEN, { op: 'path', from: [10, 20], to: [300, 400], ms: 360 }, flat);
		expect(view.visible).toBe(true);
		expect(view.path).toEqual({ from: [10, 20], to: [300, 400] });
		view = overlayFrame(view, { op: 'highlight', box: [280, 380, 40, 40] }, flat);
		expect(view.box).toEqual([280, 380, 40, 40]);
		expect(view.path?.to).toEqual([300, 400]);
		view = overlayFrame(view, { op: 'ripple', at: [300, 400] }, flat);
		expect(view.ripple).toEqual([300, 400]);
		view = overlayFrame(view, { op: 'caret', at: [300, 400], chars: 12 }, flat);
		expect(view.caret).toEqual([300, 400]);
		view = overlayFrame(view, { op: 'scroll', at: null, direction: 'down' }, flat);
		expect(view.scroll).toEqual({ at: null, direction: 'down' });
		view = overlayFrame(view, { op: 'key', keys: 'ctrl+s' }, flat);
		expect(view.keys).toBe('ctrl+s');
		expect(overlayFrame(view, { op: 'key', keys: 'x'.repeat(200) }, flat).keys).toHaveLength(32);
	});

	it('maps screen pixels to the page on a HiDPI screen and on a monitor left of the primary', () => {
		expect(overlayFrame(OVERLAY_HIDDEN, { op: 'ripple', at: [600, 400] }, { left: 0, top: 0, scale: 2 }).ripple).toEqual([300, 200]);
		const spanning = { left: -1920, top: 0, scale: 1 };
		expect(overlayFrame(OVERLAY_HIDDEN, { op: 'ripple', at: [100, 50] }, spanning).ripple).toEqual([2020, 50]);
		expect(overlayFrame(OVERLAY_HIDDEN, { op: 'highlight', box: [200, 100, 80, 40] }, { left: 0, top: 0, scale: 2 }).box).toEqual([100, 50, 40, 20]);
	});

	it('hides on hide and ignores a frame it cannot draw', () => {
		const shown = overlayFrame(OVERLAY_HIDDEN, { op: 'ripple', at: [1, 2] }, flat);
		expect(overlayFrame(shown, { op: 'hide' }, flat)).toEqual(OVERLAY_HIDDEN);
		for (const bad of [
			null,
			'ripple',
			[],
			{ op: 'nope' },
			{ op: 'ripple', at: ['1', 2] },
			{ op: 'ripple', at: [Infinity, 0] },
			{ op: 'ripple' },
			{ op: 'path', from: [0, 0] },
			{ op: 'highlight', box: [1, 2, -3, 4] },
			{ op: 'highlight', box: [1, 2, 3] },
			{ op: 'scroll', at: null, direction: 'sideways' },
			{ op: 'key', keys: 5 }
		]) {
			expect(overlayFrame(shown, bad, flat), JSON.stringify(bad)).toBe(shown);
		}
	});

	it('is a click-through window that stays on top, takes no focus and is hidden until the agent works', () => {
		const shell = source('../src-tauri/src/shell.rs');
		const start = shell.indexOf('fn build_overlay');
		expect(start).toBeGreaterThan(-1);
		const builder = shell.slice(start, shell.indexOf('\n}\n', start));
		for (const flag of [
			'.transparent(true)',
			'.always_on_top(true)',
			'.skip_taskbar(true)',
			'.focused(false)',
			'.focusable(false)',
			'.decorations(false)',
			'.shadow(false)',
			'.visible(false)',
			'.set_ignore_cursor_events(true)'
		]) {
			expect(builder, flag).toContain(flag);
		}
		// A window that could not be made click-through would sit over the owner's screen
		// eating their clicks: it is destroyed instead.
		expect(builder).toMatch(/set_ignore_cursor_events\(true\)[\s\S]*?destroy\(\)/);
		// Hidden on the agent's `hide`, and when the agent's link drops mid-action.
		const overlay = shell.slice(shell.indexOf('fn overlay('), shell.indexOf('fn hide_overlay('));
		expect(overlay).toMatch(/"hide"[\s\S]*?hide_overlay\(/);
		const link = shell.slice(shell.indexOf('async fn run_link'), shell.indexOf('fn ask('));
		expect(link).toMatch(/Frame::Overlay\(frame\)\) => overlay\(app, frame\)/);
		expect(link.slice(link.indexOf('writer.abort()'))).toContain('hide_overlay(app)');
		// It hears the agent's steps and nothing else: a capability of its own, no commands.
		const capability = JSON.parse(source('../src-tauri/capabilities/overlay.json'));
		expect(capability.windows).toEqual(['overlay']);
		expect(capability.permissions).toEqual(['core:event:allow-listen', 'core:event:allow-unlisten']);
		expect(JSON.parse(source('../src-tauri/capabilities/local.json')).windows).not.toContain('overlay');
	});

	it('draws with the design tokens only, and no inline style the CSP would refuse', () => {
		const page = source('../ui/overlay.html');
		expect(page).not.toMatch(/<style|style="/);
		expect(page).toContain('overlay.css');
		const css = source('../ui/overlay.css');
		expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|\d+(px|ms)\b/i);
		// The ripple grows on its ::before: `scale` on the mark itself scales the position the
		// script gives it as well, and the ring swept off towards the far corner (seen in a screenshot).
		expect(css).toMatch(/#ripple\.play::before\s*\{\s*animation: ripple/);
		expect(css).not.toMatch(/#ripple\.play\s*\{/);
	});
});

describe('the permissions page', () => {
	it('never offers to remember an answer for a Tier 3 action', () => {
		expect(choicesFor('CONFIRM')).toEqual(['ask', 'never']);
		expect(choicesFor('NOTIFY')).toEqual(['ask', 'allow_always', 'never']);
		expect(policyArgs({ id: 'run_command', tier_name: 'CONFIRM' }, 'allow_always')).toBeNull();
		expect(policyArgs({ id: 'run_command', tier_name: 'CONFIRM' }, 'never')).toEqual(['policy', 'set', 'run_command', 'never']);
		expect(policyArgs({ id: 'write_file', tier_name: 'NOTIFY' }, 'allow_always')).toEqual(['policy', 'set', 'write_file', 'allow_always']);
	});
});

describe('the window', () => {
	it('opens the section a hash names, and the status page for anything else', () => {
		expect(sectionOf('#pair')).toBe('pair');
		expect(sectionOf('#nowhere')).toBe('status');
		expect(sectionOf('')).toBe('status');
	});

	it('does nothing for an empty hash or a bare #, so a plain open keeps the page where it is', () => {
		expect(revealPlan('')).toBeNull();
		expect(revealPlan('#')).toBeNull();
	});

	it('reveals the named section and re-reads the data of the three that load it', () => {
		expect(revealPlan('#pair')).toEqual({ section: 'pair', load: null });
		expect(revealPlan('#permissions')).toEqual({ section: 'permissions', load: 'permissions' });
		expect(revealPlan('#activity')).toEqual({ section: 'activity', load: 'activity' });
		expect(revealPlan('#checks')).toEqual({ section: 'checks', load: 'doctor' });
		expect(revealPlan('updates')).toEqual({ section: 'updates', load: null });
	});

	it('falls back to the status page for a hash that names no section, never to an element id', () => {
		expect(revealPlan('#nowhere')).toEqual({ section: 'status', load: null });
		expect(revealPlan('#log')).toEqual({ section: 'status', load: null });
		expect(revealPlan('#__proto__')).toEqual({ section: 'status', load: null });
	});

	it('says what an update is doing, and that it waits for a quiet moment', () => {
		expect(updateLine({ status: 'ready', version: '1.0.400' })).toContain('next time the app is idle');
		expect(updateLine({ status: 'current', version: '' })).toBe('This is the newest build.');
		expect(updateLine({ status: 'could not check: offline', version: '' })).toBe('could not check: offline');
	});

	it('reads the agent as unpaired, starting or its state word', () => {
		const base = { agent_running: true, connected: true, waiting_for_pairing: false, label: 'Ready' };
		expect(agentLine(base)).toBe('Ready');
		expect(agentLine({ ...base, connected: false })).toBe('Starting…');
		expect(agentLine({ ...base, agent_running: false, waiting_for_pairing: true })).toContain('Not paired yet');
	});

	it('lists activity newest first', () => {
		const e = (time: string) => ({ time, action_id: 'x', decision: 'allow', status: 'ok' });
		expect(newestFirst([e('2026-09-24T10:00:00Z'), e('2026-09-24T12:00:00Z')]).map((x) => x.time)).toEqual([
			'2026-09-24T12:00:00Z',
			'2026-09-24T10:00:00Z'
		]);
	});
});

describe('the approval prompt', () => {
	it('answers approved for Approve, approved_always for Always allow and denied for Deny', () => {
		expect(verdictFor('approve')).toBe('approved');
		expect(verdictFor('always')).toBe('approved_always');
		expect(verdictFor('deny')).toBe('denied');
	});

	it('answers denied for anything that is not one of the two allowing buttons', () => {
		for (const other of ['', 'close', 'escape', 'APPROVE', 'approved', 'toString', '__proto__']) {
			expect(verdictFor(other)).toBe('denied');
		}
	});

	it('only Approve and Always allow can allow; closing the window denies', () => {
		const allowing = ['approve', 'always', 'deny', 'close', 'x'].filter((b) => verdictFor(b) !== 'denied');
		expect(allowing).toEqual(['approve', 'always']);
		// closing is the app's decision, not the page's: the Rust side answers every held ask "denied".
		const shell = source('../src-tauri/src/shell.rs');
		expect(shell).toMatch(/fn deny_all[\s\S]*?answer\(app, &id, "denied"\)/);
		expect(shell).toMatch(/CloseRequested \{ \.\. \} = event \{\s*deny_all\(&app\)/);
	});

	it('shows the action, the reason, each parameter as text, the tier in words, and Always only when offered', () => {
		const view = askView({
			id: 'a1',
			description: 'Open a file',
			reason: 'You asked for it',
			params: { path: '/tmp/x', n: 3, nested: { a: 1 } },
			tier: 3,
			allow_always: true
		});
		expect(view.what).toBe('Open a file');
		expect(view.why).toBe('You asked for it');
		expect(view.params).toEqual([
			['path', '/tmp/x'],
			['n', '3'],
			['nested', '{"a":1}']
		]);
		expect(view.tier).toBe('Asks every time');
		expect(view.showAlways).toBe(true);
	});

	it('names an action with no description, and offers no Always without allow_always or a known tier', () => {
		const view = askView({ id: 'a2' });
		expect(view.what).toBe('An action on this computer');
		expect(view.why).toBe('');
		expect(view.params).toEqual([]);
		expect(view.tier).toBe('');
		expect(view.showAlways).toBe(false);
		expect(askView({ id: 'a3', tier: 9, allow_always: false }).tier).toBe('');
	});
});
