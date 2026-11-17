/**
 * The approval prompt (ADR-0051): one held action, answered here, with the
 * console closed or open. The agent decided the action needs a person; this
 * shows it and sends back one of the three answers. Closing the window is a
 * denial (the app sends it), never a default-allow.
 */
import { STATE_GLYPH } from './tokens.js';
import { askView, verdictFor, type AskFrame as Ask } from './logic.js';

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const $ = (id: string): HTMLElement => document.getElementById(id) as HTMLElement;
let current: Ask | null = null;

async function load(): Promise<void> {
	current = await invoke<Ask | null>('pending_ask');
	if (!current) return;
	$('glyph').textContent = STATE_GLYPH.attention;
	const view = askView(current);
	$('what').textContent = view.what;
	$('why').textContent = view.why;
	const params = $('params');
	params.replaceChildren();
	for (const [key, value] of view.params) {
		const term = document.createElement('dt');
		term.className = 'meta';
		term.textContent = key;
		const detail = document.createElement('dd');
		detail.className = 'mono';
		detail.textContent = value;
		params.append(term, detail);
	}
	$('tier').textContent = view.tier;
	$('always').hidden = !view.showAlways;
	$('approve').focus();
}

async function answer(button: 'approve' | 'always' | 'deny'): Promise<void> {
	if (!current) return;
	await invoke('answer', { id: current.id, verdict: verdictFor(button) });
	await load();
}

$('approve').addEventListener('click', () => void answer('approve'));
$('always').addEventListener('click', () => void answer('always'));
$('deny').addEventListener('click', () => void answer('deny'));
window.addEventListener('jarvis-ask', () => void load());
void listen('shell-changed', () => {
	if (!current) void load();
});
void load();
