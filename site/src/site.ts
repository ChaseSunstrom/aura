// The Pages site's behaviour (T-0866): the live core and its states, the films, the copy buttons.
//
// Everything here adds to a page that already reads without it: the films carry the browser's
// own controls until this takes them over, the core has a still picture under it, and the
// install lines are text. Nothing is fetched and nothing is stored.
//
// Reduced motion: the core stands still (one frame per state), the tour of states does not run,
// no film starts by itself and the films keep the browser's controls.

import { tokenMs } from '$lib/tokens';

import { CORE_STATES, mountCore, type CoreState } from './core';

/** How long the tour rests on each state: long enough to see the core settle into it. */
const REST = tokenMs('--jv-dur-undo');
/** How long a button says what it just did. */
const SAID = tokenMs('--jv-dur-blink');

const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const said = document.getElementById('said');

function say(text: string) {
	if (said) said.textContent = text;
}

// ---- the core ---------------------------------------------------------------------------

async function core() {
	const host = document.getElementById('stage-core');
	const panel = document.getElementById('states');
	const chips = panel?.querySelector<HTMLElement>('.chips');
	if (!host || !panel || !chips) return;
	const live = await mountCore(host, 'idle');
	if (!live.drawn) return; // the still picture stays, and states that would change nothing stay hidden
	host.dataset.core = 'live';

	let pinned = false;
	let timer = 0;
	const buttons = new Map<CoreState, HTMLButtonElement>();

	function show(state: CoreState) {
		live.set(state);
		host!.dataset.state = state;
		for (const [name, button] of buttons) button.setAttribute('aria-pressed', String(name === state));
	}

	function tour() {
		clearInterval(timer);
		if (pinned || reduce.matches) return;
		timer = window.setInterval(() => {
			if (document.hidden) return;
			const at = CORE_STATES.indexOf((host!.dataset.state as CoreState) ?? 'idle');
			show(CORE_STATES[(at + 1) % CORE_STATES.length]);
		}, REST);
	}

	for (const state of CORE_STATES) {
		const button = document.createElement('button');
		button.type = 'button';
		button.className = 'chip';
		button.textContent = state;
		button.addEventListener('click', () => {
			pinned = true; // a state someone chose stays until they choose another
			clearInterval(timer);
			show(state);
			say(`The core is ${state}`);
		});
		buttons.set(state, button);
		chips.append(button);
	}
	panel.hidden = false;
	show('idle');
	tour();
	reduce.addEventListener('change', tour);
}

// ---- the films --------------------------------------------------------------------------

function films() {
	const all = [...document.querySelectorAll<HTMLVideoElement>('.film video')];
	if (!('IntersectionObserver' in window)) return;

	for (const video of all) {
		const figure = video.closest('.film')!;
		const toggle = document.createElement('button');
		toggle.type = 'button';
		toggle.className = 'button toggle';
		// Stopped by hand, a film stays stopped when it scrolls back into view.
		const label = () => {
			toggle.textContent = video.paused ? 'Play' : 'Pause';
		};
		toggle.addEventListener('click', () => {
			if (video.paused) {
				delete video.dataset.held;
				void video.play();
			} else {
				video.dataset.held = '';
				video.pause();
			}
		});
		video.addEventListener('play', label);
		video.addEventListener('pause', label);
		label();
		figure.append(toggle);
	}

	const watch = new IntersectionObserver(
		(entries) => {
			for (const entry of entries) {
				const video = entry.target as HTMLVideoElement;
				if (entry.isIntersecting && !reduce.matches && !('held' in video.dataset)) void video.play().catch(() => {});
				else if (!entry.isIntersecting) video.pause();
			}
		},
		{ threshold: 0.45 }
	);

	function wear() {
		for (const video of all) {
			// The page's own button is the control while the films play themselves; a reader who
			// asked for less motion keeps the browser's, and starts each film by hand.
			video.controls = reduce.matches;
			video.closest('.film')!.toggleAttribute('data-manual', reduce.matches);
			if (reduce.matches) video.pause();
		}
	}
	wear();
	reduce.addEventListener('change', wear);
	for (const video of all) watch.observe(video);
}

// ---- arriving ---------------------------------------------------------------------------

/** Each feature is projected as it comes into view; without this script it is simply there. */
function arrivals() {
	if (!('IntersectionObserver' in window) || reduce.matches) return;
	const parts = document.querySelectorAll<HTMLElement>('.feature, .proven');
	const watch = new IntersectionObserver(
		(entries) => {
			for (const entry of entries) {
				if (!entry.isIntersecting) continue;
				entry.target.setAttribute('data-seen', '');
				watch.unobserve(entry.target);
			}
		},
		{ threshold: 0.12 }
	);
	document.documentElement.dataset.arrivals = '';
	for (const part of parts) watch.observe(part);
}

// ---- the copy buttons ------------------------------------------------------------------

function copies() {
	for (const button of document.querySelectorAll<HTMLButtonElement>('button[data-copy]')) {
		if (!navigator.clipboard) {
			button.hidden = true; // the line is still text to select
			continue;
		}
		const was = button.textContent ?? '';
		button.addEventListener('click', async () => {
			const line = document.getElementById(button.dataset.copy ?? '')?.textContent ?? '';
			try {
				await navigator.clipboard.writeText(line);
				button.textContent = 'Copied';
				say('The install line is copied');
			} catch {
				button.textContent = 'Select it';
				say('The line could not be copied: select it instead');
			}
			window.setTimeout(() => (button.textContent = was), SAID);
		});
	}
}

copies();
films();
arrivals();
void core();
