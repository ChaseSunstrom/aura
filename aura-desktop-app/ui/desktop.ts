/**
 * The "This computer" window (ADR-0051): everything the agent's CLI did,
 * without a terminal. Status and the agent's log, pairing, permissions (the
 * agent's own `tiers --json` / `policy set`), activity (`audit --json`),
 * checks (`doctor`) and updates. The decisions are in `logic.ts`; this draws.
 */
import {
	CHOICE_WORDS,
	TIER_WORDS,
	agentLine,
	choicesFor,
	newestFirst,
	policyArgs,
	revealPlan,
	updateLine,
	type AuditEntry,
	type Policy,
	type Tiers
} from './logic.js';

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

interface ShellState {
	state: string;
	detail: string;
	label: string;
	console_url: string;
	console_chosen: boolean;
	agent_running: boolean;
	connected: boolean;
	waiting_for_pairing: boolean;
	last_pair: { ok: boolean; message: string } | null;
	version: string;
	update: { status: string; version: string };
	autostart: boolean;
}
interface CliOutput {
	code: number | null;
	stdout: string;
	stderr: string;
}

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

async function cli(args: string[]): Promise<CliOutput> {
	return invoke<CliOutput>('agent_cli', { args });
}

/**
 * `#section` (how the tray opens the window) brings that section into view and
 * moves focus to its heading; the section's data is read again, as the old tab's
 * switch did. The hash is then dropped, so asking for the same section a second
 * time (after scrolling away) is a hash change again and not a no-op.
 */
function reveal(hash: string): void {
	const plan = revealPlan(hash);
	if (!plan) return;
	const { section, load } = plan;
	if (load === 'permissions') void loadPermissions();
	if (load === 'activity') void loadActivity();
	if (load === 'doctor') void loadDoctor();
	$(section).scrollIntoView({ block: 'start' });
	$(`${section}-h`).focus({ preventScroll: true });
	history.replaceState(null, '', location.pathname + location.search);
}

async function refresh(): Promise<void> {
	const s = await invoke<ShellState>('shell_state');
	$('version').textContent = `Version ${s.version}`;
	$('update-version').textContent = s.version;
	$('agent-line').textContent = agentLine(s);
	$('console-url').textContent = s.console_chosen ? s.console_url : 'None chosen yet — pair this computer.';
	$('autostart').setAttribute('aria-pressed', String(s.autostart));
	$('update-line').textContent = updateLine(s.update);
	if (s.last_pair) {
		const said = $('pair-said');
		said.textContent = s.last_pair.message;
		said.classList.toggle('error', !s.last_pair.ok);
	}
	const lines = await invoke<string[]>('agent_log');
	$('log').textContent = lines.length ? lines.join('\n') : 'Nothing yet.';
}

/** One Field-style row: name and hint on the left, a control (or a value) on the right. */
function fieldRow(label: string, hint: string, control?: HTMLElement): HTMLElement {
	const row = document.createElement('div');
	row.className = 'field-row';
	const text = document.createElement('span');
	text.className = 'text';
	const name = document.createElement('span');
	name.className = 'label';
	name.textContent = label;
	const what = document.createElement('span');
	what.className = 'hint';
	what.textContent = hint;
	text.append(name, what);
	row.append(text);
	if (control) row.append(control);
	return row;
}

function groupHead(text: string): HTMLElement {
	const head = document.createElement('div');
	head.className = 'group-head';
	head.textContent = text;
	return head;
}

async function loadPermissions(): Promise<void> {
	const said = $('permissions-said');
	const list = $('actions');
	let tiers: Tiers;
	try {
		const out = await cli(['tiers', '--json']);
		if (out.code !== 0) throw new Error(out.stderr.trim() || `the agent answered ${out.code}`);
		tiers = JSON.parse(out.stdout) as Tiers;
	} catch (e) {
		said.textContent = `Couldn't read the permissions. ${String(e instanceof Error ? e.message : e)}`;
		said.classList.add('error');
		list.replaceChildren();
		return;
	}
	said.classList.remove('error');
	said.textContent = tiers.panic ? 'Refusing everything: nothing runs until this is turned off.' : '';
	$('panic').setAttribute('aria-pressed', String(tiers.panic));
	$('automation').setAttribute('aria-pressed', String(tiers.automation_enabled));
	$('automation').textContent = tiers.automation_enabled ? 'Automation on' : 'Automation off';
	list.replaceChildren();
	let tier = '';
	for (const action of tiers.actions) {
		if (action.tier_name !== tier) {
			tier = action.tier_name;
			list.append(groupHead(TIER_WORDS[action.tier_name] ?? action.tier_name));
		}
		const pick = document.createElement('select');
		pick.className = 'field';
		pick.setAttribute('aria-label', `Your answer for ${action.id}`);
		for (const choice of choicesFor(action.tier_name)) pick.add(new Option(CHOICE_WORDS[choice], choice, false, choice === action.policy));
		pick.disabled = action.tier_name === 'AUTO' || !action.available;
		pick.addEventListener('change', async () => {
			const args = policyArgs(action, pick.value as Policy);
			if (!args) return;
			const out = await cli(args);
			if (out.code !== 0) said.textContent = out.stderr.trim();
			await loadPermissions();
		});
		const hint = action.available ? action.description : `${action.description} Not on this computer: ${action.unsupported_reason ?? 'unsupported'}.`;
		list.append(fieldRow(action.id, hint, pick));
	}
	if (!tiers.actions.length) said.textContent = 'The agent reported no actions.';
}

async function loadActivity(): Promise<void> {
	const said = $('activity-said');
	const list = $('audit');
	try {
		const out = await cli(['audit', '--json', '--limit', '100']);
		if (out.code !== 0) throw new Error(out.stderr.trim() || `the agent answered ${out.code}`);
		const entries = newestFirst(JSON.parse(out.stdout) as AuditEntry[]);
		list.replaceChildren();
		for (const entry of entries) {
			const when = document.createElement('span');
			when.className = 'mono';
			when.textContent = entry.time.replace('T', ' ').replace(/\..*$/, '');
			const outcome = entry.note ? `${entry.status} — ${entry.note}` : entry.status;
			list.append(fieldRow(entry.action_id, `${entry.decision}, ${outcome}`, when));
		}
		said.classList.remove('error');
		said.textContent = entries.length ? '' : 'Nothing has run on this computer yet. What the assistant does here is listed as it happens.';
	} catch (e) {
		list.replaceChildren();
		said.classList.add('error');
		said.textContent = `Couldn't read the activity. ${String(e instanceof Error ? e.message : e)}`;
	}
}

async function loadDoctor(): Promise<void> {
	const pre = $('doctor');
	pre.textContent = 'Checking…';
	try {
		const out = await cli(['doctor']);
		pre.textContent = (out.stdout + (out.stderr ? `\n${out.stderr}` : '')).trim() || 'The agent said nothing.';
	} catch (e) {
		pre.textContent = `Couldn't run the checks. ${String(e instanceof Error ? e.message : e)}`;
	}
}

window.addEventListener('hashchange', () => reveal(location.hash));

$('pair-form').addEventListener('submit', async (event) => {
	event.preventDefault();
	const said = $('pair-said');
	said.classList.remove('error');
	said.textContent = 'Pairing…';
	const result = await invoke<{ ok: boolean; message: string }>('pair', { text: $<HTMLInputElement>('pair-text').value });
	said.textContent = result.message;
	said.classList.toggle('error', !result.ok);
});
$('open-console').addEventListener('click', () => void invoke('open_console'));
$('restart-agent').addEventListener('click', () => void invoke('restart_agent'));
$('autostart').addEventListener('click', async () => {
	const on = $('autostart').getAttribute('aria-pressed') !== 'true';
	try {
		await invoke<boolean>('set_autostart', { on });
	} finally {
		await refresh();
	}
});
$('panic').addEventListener('click', async () => {
	const on = $('panic').getAttribute('aria-pressed') !== 'true';
	await cli(['policy', 'panic', on ? 'on' : 'off']);
	await loadPermissions();
});
$('automation').addEventListener('click', async () => {
	const on = $('automation').getAttribute('aria-pressed') !== 'true';
	await cli(['policy', on ? 'enable' : 'disable']);
	await loadPermissions();
});
$('doctor-run').addEventListener('click', () => void loadDoctor());
$('update-check').addEventListener('click', () => void invoke('check_update'));

void listen('shell-changed', () => void refresh());
// One page: every section is read on open (the old tabs read theirs when shown).
void loadPermissions();
void loadActivity();
void loadDoctor();
void refresh();
reveal(location.hash);
