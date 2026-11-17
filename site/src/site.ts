// The Pages site's behaviour (T-0866, T-1516): the live core and its states, the two faces, the
// docked core, the turn's path, the figures, the films, the hold, the copy buttons.
//
// Everything here adds to a page that already reads without it: the films carry the browser's
// own controls until this takes them over, the core has a still picture under it, every figure
// is already written, the steps are a list and the install lines are text. Nothing is fetched
// and nothing is stored: the face the page wears travels only in its address (`?theme=jarvis`).
//
// Reduced motion: the cores stand still (one frame per state), the tour of states does not run,
// no film starts by itself, figures do not count, the ticker is a list and nothing arrives.

import { THEME_IDS, THEMES, type ThemeId } from '$lib/themes';
import { tokenMs } from '$lib/tokens';
import { rendererFor } from '$lib/ds/core/renderer';

import { CORE_STATES, mountCore, type CoreState, type LiveCore } from './core';

/** How long the tour rests on each state: long enough to see the core settle into it. */
const REST = tokenMs('--jv-dur-undo');
/** How long a button says what it just did. */
const SAID = tokenMs('--jv-dur-blink');
/** How long a figure takes to count up to itself. */
const COUNT = tokenMs('--jv-dur-sweep');
/** How long a press must be held to confirm, as on the console. */
const HOLD = tokenMs('--jv-dur-hold');
/** How long colours ease between the two faces. */
const SWITCH = tokenMs('--jv-dur-appear');

const root = document.documentElement;
const reduce = matchMedia('(prefers-reduced-motion: reduce)');
const said = document.getElementById('said');
const seeing = 'IntersectionObserver' in window;

function say(text: string) {
	if (said) said.textContent = text;
}

/** Calls `see` once for each element the first time it comes into view. */
function onceSeen(parts: Iterable<Element>, see: (part: Element) => void, threshold = 0.12) {
	const watch = new IntersectionObserver(
		(entries) => {
			for (const entry of entries) {
				if (!entry.isIntersecting) continue;
				watch.unobserve(entry.target);
				see(entry.target);
			}
		},
		{ threshold }
	);
	for (const part of parts) watch.observe(part);
}

// ---- the face the page wears ----------------------------------------------------------------

type Strings = (typeof THEMES)[ThemeId]['persona']['strings'];

/** The persona's own line for each state the core can be in; a state with none says nothing. */
const LINES: Partial<Record<CoreState, keyof Strings>> = {
	idle: 'ready',
	listening: 'listening',
	thinking: 'thinking',
	acting: 'working',
	working: 'working',
	alert: 'approvalAsk'
};

const isTheme = (id: string | null | undefined): id is ThemeId => THEME_IDS.includes(id as ThemeId);

let theme: ThemeId = isTheme(new URLSearchParams(location.search).get('theme'))
	? (new URLSearchParams(location.search).get('theme') as ThemeId)
	: 'aura';
const cores: LiveCore[] = [];
const onWear: Array<() => void> = [];

function line(state: CoreState): string {
	const key = LINES[state];
	return key ? THEMES[theme].persona.strings[key] : '';
}

function dress() {
	const name = THEMES[theme].persona.name;
	root.dataset.theme = theme;
	document.title = name;
	for (const part of document.querySelectorAll('[data-persona-name]')) part.textContent = name;
	for (const button of document.querySelectorAll<HTMLButtonElement>('.switch [data-theme-to]'))
		button.setAttribute('aria-pressed', String(button.dataset.themeTo === theme));
	for (const face of document.querySelectorAll<HTMLElement>('.face[data-face]'))
		face.toggleAttribute('data-worn', face.dataset.face === theme);
	for (const run of onWear) run();
}

function wear(next: ThemeId) {
	if (next === theme) return;
	theme = next;
	const url = new URL(location.href);
	if (next === 'aura') url.searchParams.delete('theme');
	else url.searchParams.set('theme', next);
	history.replaceState(null, '', url);
	if (!reduce.matches) {
		root.dataset.switching = '';
		window.setTimeout(() => delete root.dataset.switching, SWITCH);
	}
	dress();
	for (const core of cores) void core.wear(rendererFor(next));
	say(`The page wears ${THEMES[next].persona.name}`);
}

function faces() {
	for (const button of document.querySelectorAll<HTMLButtonElement>('[data-theme-to]')) {
		button.hidden = false;
		button.addEventListener('click', () => {
			const to = button.dataset.themeTo;
			if (isTheme(to)) wear(to);
		});
	}
	const sw = document.getElementById('switch');
	if (sw) sw.hidden = false;
	dress();
}

// ---- the core ---------------------------------------------------------------------------

const subtitle = document.getElementById('subtitle');

function caption(text: string) {
	if (!subtitle || subtitle.textContent === text) return;
	if (reduce.matches) {
		subtitle.textContent = text;
		return;
	}
	subtitle.dataset.fading = '';
	window.setTimeout(() => {
		subtitle.textContent = text;
		delete subtitle.dataset.fading;
	}, tokenMs('--jv-dur-fade'));
}

async function core() {
	const host = document.getElementById('stage-core');
	const panel = document.getElementById('states');
	const chips = panel?.querySelector<HTMLElement>('.chips');
	if (!host || !panel || !chips) return;
	const live = await mountCore(host, rendererFor(theme));
	if (!live.drawn) return; // the still picture stays, and states that would change nothing stay hidden
	cores.push(live);
	host.dataset.core = 'live';

	let pinned = false;
	let timer = 0;
	let now: CoreState = 'idle';
	const buttons = new Map<CoreState, HTMLButtonElement>();

	function show(state: CoreState) {
		now = state;
		live.set(state);
		host!.dataset.state = state;
		caption(line(state));
		for (const [name, button] of buttons) button.setAttribute('aria-pressed', String(name === state));
	}

	function tour() {
		clearInterval(timer);
		if (pinned || reduce.matches) return;
		timer = window.setInterval(() => {
			if (document.hidden) return;
			show(CORE_STATES[(CORE_STATES.indexOf(now) + 1) % CORE_STATES.length]);
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
	onWear.push(() => caption(line(now)));
	panel.hidden = false;
	show('idle');
	tour();
	reduce.addEventListener('change', tour);
}

// ---- the docked core ---------------------------------------------------------------------

/** What the docked core is doing beside each part of the page. */
const MOODS: Record<string, CoreState> = {
	numbers: 'thinking',
	core: 'idle',
	voice: 'listening',
	answer: 'speaking',
	approval: 'alert',
	themes: 'music',
	car: 'speaking',
	phone: 'listening',
	ops: 'working',
	flow: 'thinking',
	approvals: 'alert',
	reach: 'acting',
	faces: 'idle',
	install: 'working'
};

async function dock() {
	const button = document.getElementById('dock');
	const host = document.getElementById('dock-core');
	const label = document.getElementById('dock-state');
	const hero = document.querySelector('.hero');
	if (!button || !host || !label || !hero || !seeing) return;
	button.hidden = false;
	const live = await mountCore(host, rendererFor(theme), { docked: true });
	if (!live.drawn) {
		button.hidden = true;
		return;
	}
	cores.push(live);
	let mood: CoreState = 'idle';
	const tell = () => {
		live.set(mood);
		label.textContent = line(mood) || mood;
	};
	onWear.push(tell);
	button.addEventListener('click', () => {
		document.getElementById('top')?.scrollIntoView({ behavior: reduce.matches ? 'auto' : 'smooth' });
		document.querySelector<HTMLElement>('.wordmark')?.focus({ preventScroll: true });
	});

	// Docked once the first screen's core is mostly gone, so there is never a core in two places.
	new IntersectionObserver(
		(entries) => {
			for (const entry of entries) root.toggleAttribute('data-docked', !entry.isIntersecting);
		},
		{ threshold: 0.25 }
	).observe(hero);

	// The part of the page crossing the middle of the screen sets the mood.
	const middle = new IntersectionObserver(
		(entries) => {
			for (const entry of entries) {
				if (!entry.isIntersecting) continue;
				const next = MOODS[entry.target.id];
				if (next && next !== mood) {
					mood = next;
					tell();
				}
			}
		},
		{ rootMargin: '-45% 0px -45% 0px' }
	);
	for (const id of Object.keys(MOODS)) {
		const part = document.getElementById(id);
		if (part) middle.observe(part);
	}
	tell();
}

/** Each face card draws its own core, small; it stays its own kind whatever the page wears. */
async function faceCores() {
	for (const host of document.querySelectorAll<HTMLElement>('.face-core[data-kind]')) {
		const kind = host.dataset.kind;
		if (kind !== 'galaxy' && kind !== 'reactor') continue;
		await mountCore(host, kind, { docked: true });
	}
}

// ---- the masthead ----------------------------------------------------------------------

/** The section in view lights its link; the hairline under the masthead is how far down the page is. */
function masthead() {
	const progress = document.getElementById('progress');
	let queued = false;
	const measure = () => {
		queued = false;
		const room = root.scrollHeight - innerHeight;
		progress?.style.setProperty('--progress', String(room > 0 ? Math.min(1, scrollY / room) : 0));
		root.toggleAttribute('data-scrolled', scrollY > 0);
	};
	addEventListener(
		'scroll',
		() => {
			if (!queued) requestAnimationFrame(measure);
			queued = true;
		},
		{ passive: true }
	);
	addEventListener('resize', measure, { passive: true });
	measure();

	if (!seeing) return;
	const links = new Map<string, HTMLAnchorElement>();
	for (const link of document.querySelectorAll<HTMLAnchorElement>('.mast nav a[href^="#"]'))
		links.set(link.hash.slice(1), link);
	const light = (id: string | null) => {
		for (const [name, link] of links) {
			if (name === id) link.setAttribute('aria-current', 'true');
			else link.removeAttribute('aria-current');
		}
	};
	const watch = new IntersectionObserver(
		(entries) => {
			for (const entry of entries) if (entry.isIntersecting) light(entry.target.id);
		},
		{ rootMargin: '-40% 0px -55% 0px' }
	);
	for (const id of links.keys()) {
		const part = document.getElementById(id);
		if (part) watch.observe(part);
	}
	// Above the first section nothing is current.
	const hero = document.querySelector('.hero');
	if (hero)
		new IntersectionObserver((entries) => {
			if (entries.some((entry) => entry.isIntersecting && entry.intersectionRatio > 0.5)) light(null);
		}, { threshold: [0.5] }).observe(hero);
}

// ---- the turn's path -------------------------------------------------------------------

/** The step crossing the middle of the screen lights its part of the drawing. */
function flow() {
	const figure = document.getElementById('diagram');
	const steps = [...document.querySelectorAll<HTMLElement>('#steps .step')];
	if (!figure || !steps.length || !seeing) return;
	const parts = [...figure.querySelectorAll<SVGElement>('[data-on]')];

	function light(step: string) {
		figure!.dataset.step = step;
		for (const part of parts) {
			const on = (part.dataset.on ?? '').split(' ').includes(step);
			part.toggleAttribute('data-lit', on);
			part.toggleAttribute('data-reverse', on && (part.dataset.back ?? '').split(' ').includes(step));
		}
		for (const item of steps) {
			if (item.dataset.step === step) item.setAttribute('aria-current', 'step');
			else item.removeAttribute('aria-current');
		}
		pan();
	}

	// Where the drawing is wider than its box (a phone), bring the lit nodes to the middle.
	function pan() {
		const room = figure!.scrollWidth - figure!.clientWidth;
		if (room <= 0) return;
		const lit = [...figure!.querySelectorAll<SVGGraphicsElement>('.node[data-lit]')];
		if (!lit.length) return;
		const box = figure!.getBoundingClientRect();
		const centres = lit.map((node) => {
			const at = node.getBoundingClientRect();
			return at.left + at.width / 2 - box.left + figure!.scrollLeft;
		});
		const middle = (Math.min(...centres) + Math.max(...centres)) / 2;
		figure!.scrollTo({ left: middle - figure!.clientWidth / 2, behavior: reduce.matches ? 'auto' : 'smooth' });
	}

	const watch = new IntersectionObserver(
		(entries) => {
			for (const entry of entries) if (entry.isIntersecting) light((entry.target as HTMLElement).dataset.step ?? '0');
		},
		{ rootMargin: '-48% 0px -48% 0px' }
	);
	for (const step of steps) watch.observe(step);
	light('1');
}

// ---- the figures -----------------------------------------------------------------------

/** Each figure counts up to itself the first time it is seen; it is already written without this. */
function figures() {
	if (!seeing || reduce.matches) return;
	const all = document.querySelectorAll<HTMLElement>('.figure[data-count]');
	onceSeen(
		all,
		(part) => {
			const el = part as HTMLElement;
			const to = Number(el.dataset.count);
			if (!Number.isFinite(to) || to <= 0) return;
			const start = performance.now();
			const step = (at: number) => {
				const t = Math.min(1, (at - start) / COUNT);
				// Ease out: the count slows as it lands, like the console's figures.
				el.textContent = String(Math.round(to * (1 - (1 - t) ** 3)));
				if (t < 1) requestAnimationFrame(step);
			};
			el.textContent = '0';
			requestAnimationFrame(step);
		},
		0.5
	);
}

/** Bench bars take their share; drawn from nothing the first time the list is seen. */
function bench() {
	const list = document.getElementById('bench');
	if (!list) return;
	for (const bar of list.querySelectorAll<HTMLElement>('.bar[data-share]')) bar.style.setProperty('--share', bar.dataset.share ?? '0');
	if (seeing && !reduce.matches) onceSeen([list], (part) => part.setAttribute('data-seen', ''), 0.3);
}

// ---- the matrix ------------------------------------------------------------------------

/** Each verdict's reasons run by as a ticker: the list twice over, so the loop has no seam. */
function tickers() {
	if (reduce.matches) return;
	for (const box of document.querySelectorAll<HTMLElement>('.reasons')) {
		const list = box.querySelector('ul');
		if (!list || list.children.length < 3) continue;
		const rows = list.children.length;
		for (const item of [...list.children]) {
			const copy = item.cloneNode(true) as HTMLElement;
			copy.setAttribute('aria-hidden', 'true');
			list.append(copy);
		}
		box.style.setProperty('--rows', String(rows));
		box.dataset.ticker = '';
	}
}

/** The hold-to-confirm: a press held for the console's whole hold confirms; let go early and it drains. */
function hold() {
	const demo = document.getElementById('hold-demo');
	const button = document.getElementById('hold');
	const label = button?.querySelector('.hold-label');
	const note = document.getElementById('hold-said');
	if (!demo || !button || !label || !note) return;
	const was = { label: label.textContent ?? '', note: note.textContent ?? '' };
	let timer = 0;
	let reset = 0;

	const start = (event: Event) => {
		event.preventDefault();
		if ('done' in button.dataset) return;
		clearTimeout(reset);
		button.dataset.holding = '';
		timer = window.setTimeout(() => {
			delete button.dataset.holding;
			button.dataset.done = '';
			label.textContent = 'Confirmed';
			note.textContent = 'Held to the end: on the console, this is where the action would run.';
			say('Confirmed by holding');
			reset = window.setTimeout(() => {
				delete button.dataset.done;
				label.textContent = was.label;
				note.textContent = was.note;
			}, REST);
		}, HOLD);
	};
	const stop = () => {
		if (!('holding' in button.dataset)) return;
		clearTimeout(timer);
		delete button.dataset.holding;
		note.textContent = 'Let go too soon: nothing happened. Hold until it fills.';
	};

	button.addEventListener('pointerdown', start);
	for (const kind of ['pointerup', 'pointerleave', 'pointercancel']) button.addEventListener(kind, stop);
	button.addEventListener('keydown', (event) => {
		if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) start(event);
	});
	button.addEventListener('keyup', (event) => {
		if (event.key === ' ' || event.key === 'Enter') stop();
	});
	button.addEventListener('contextmenu', (event) => event.preventDefault());
	demo.hidden = false;
}

// ---- the films --------------------------------------------------------------------------

function films() {
	const all = [...document.querySelectorAll<HTMLVideoElement>('.film video')];
	if (!seeing) return;

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

	function controls() {
		for (const video of all) {
			// The page's own button is the control while the films play themselves; a reader who
			// asked for less motion keeps the browser's, and starts each film by hand.
			video.controls = reduce.matches;
			video.closest('.film')!.toggleAttribute('data-manual', reduce.matches);
			if (reduce.matches) video.pause();
		}
	}
	controls();
	reduce.addEventListener('change', controls);
	for (const video of all) watch.observe(video);
}

// ---- arriving ---------------------------------------------------------------------------

/** Each part is projected as it comes into view; without this script it is simply there. */
function arrivals() {
	if (!seeing || reduce.matches) return;
	const parts = document.querySelectorAll<HTMLElement>(
		'.feature, .proven, .numbers, .flow-head, .matrix, .reach-head, .faces, .plugin'
	);
	document.querySelectorAll<HTMLElement>('.plugin').forEach((plugin, i) => plugin.style.setProperty('--i', String(i % 4)));
	root.dataset.arrivals = '';
	onceSeen(parts, (part) => part.setAttribute('data-seen', ''));
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
			const text = document.getElementById(button.dataset.copy ?? '')?.textContent ?? '';
			try {
				await navigator.clipboard.writeText(text);
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

faces();
masthead();
copies();
films();
flow();
figures();
bench();
tickers();
hold();
arrivals();
void core();
void dock();
void faceCores();
