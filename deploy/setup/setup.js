// The setup page (T-0864): six questions, a review, then the install's own progress.
//
// The server beside this file (server.py) is the authority: it checks every answer again and is
// the only thing that writes them. This script collects, shows what the server said, and draws.
// The token arrives once in the address (?t=…), is taken out of the address bar at once, and is
// sent as a header from then on. The password and the model key are never put in storage.
//
// The core behind the questions is drawn here from the theme's own token colours (read from the
// computed style, never typed): a spiral galaxy under AURA, a ringed reactor under JARVIS. It
// stops while the tab is hidden and draws a single still frame for reduced motion.
//
// Not covered: a browser without fetch or CSS custom properties (the page says to use
// --no-gui), and anything about the install itself, which the terminal reports in full.
(() => {
	'use strict';

	const $ = (sel, root = document) => root.querySelector(sel);
	const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

	const STEPS = ['server', 'models', 'voice', 'wake', 'devices', 'theme', 'review'];
	const RAIL = { server: 'Server', models: 'Models', voice: 'Voice', wake: 'Wake', devices: 'Devices', theme: 'Theme', review: 'Install' };
	const NAMES = { aura: 'AURA', jarvis: 'JARVIS' };
	const PHASES = ['preflight', 'setup', 'secrets', 'n8n', 'images', 'start', 'verify', 'pair'];
	const PHASE_WORDS = {
		preflight: 'Checked this machine',
		setup: 'Took your answers',
		secrets: 'Made the keys',
		n8n: 'Prepared automation',
		images: 'Fetching and building the parts',
		start: 'Starting the services',
		verify: 'Checking each one answers',
		pair: 'Ready to pair'
	};
	const STT_HINTS = {
		tiny: 'Tiny: the fastest, and the one that mishears most.',
		base: 'Base: quick, fine for short commands in a quiet room.',
		small: 'Small: the balance most machines should pick.',
		medium: 'Medium: better with accents and noise; wants a graphics card.',
		'large-v3': 'Large: the most accurate; slow without a graphics card.'
	};
	const TTS_WORDS = { kokoro: 'A natural voice', piper: 'A light voice' };
	const DEVICE_WORDS = { phone: 'Phone', desktop: 'Computer', car: 'Car' };
	const NEXT_STEPS = {
		phone: ['Phone', 'install the Android app, then open Settings and Pairing in the console and scan the code it shows.'],
		desktop: ['Computer', 'install the desktop app; in the console, Pairing offers to open it and pairs it in one step.'],
		car: ['Car', 'pair the phone first; the car screen appears in Android Auto once the phone app is signed in.']
	};
	const LOOPBACK = '127.0.0.1';
	const STORE = 'aura-setup';
	const POLL_MS = 1500;

	let token = '';
	let facts = {};
	let choices = {};
	let current = 0;
	let furthest = 0;
	const answers = {
		server: { bind: '', public_url: '', timezone: '', password: '' },
		models: { mode: '', url: '', model: '', key: '' },
		voice: { tts: '', stt: '' },
		wake: { enabled: true },
		devices: [],
		theme: 'aura'
	};

	// ---- small things -------------------------------------------------------------------

	const store = {
		get(key) {
			try { return sessionStorage.getItem(key) || ''; } catch { return ''; }
		},
		set(key, value) {
			try { sessionStorage.setItem(key, value); } catch { /* private mode: the page works without it */ }
		}
	};

	function say(text) {
		$('#status').textContent = text;
	}

	function el(tag, attrs = {}, text = '') {
		const node = document.createElement(tag);
		for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
		if (text) node.textContent = text;
		return node;
	}

	async function api(path, body) {
		const init = { method: body === undefined ? 'GET' : 'POST', cache: 'no-store', headers: { Authorization: 'Bearer ' + token } };
		if (body !== undefined) {
			init.headers['Content-Type'] = 'application/json';
			init.body = JSON.stringify(body);
		}
		const res = await fetch(path, init);
		let data = {};
		try { data = await res.json(); } catch { /* an empty body is an empty object */ }
		return { status: res.status, data };
	}

	// ---- the token and the first load ---------------------------------------------------

	function takeToken() {
		const params = new URLSearchParams(location.search);
		const given = params.get('t') || '';
		if (given) store.set(STORE + '-token', given);
		// Out of the address bar at once: an address is copied, bookmarked and shown on screen shares.
		if (params.has('t')) history.replaceState(null, '', location.pathname);
		return given || store.get(STORE + '-token');
	}

	function gate(text) {
		const panel = $('#panel');
		panel.dataset.view = 'gate';
		$('#steps').hidden = true;
		$('.rail').hidden = true;
		const note = $('#gate');
		note.textContent = text;
		note.hidden = false;
		core.mood('attention');
	}

	async function start() {
		token = takeToken();
		if (!token) return gate('Open the address the installer printed in the terminal: it carries a one-time token.');
		let state;
		try {
			state = await api('/api/state');
		} catch {
			return gate('The installer is not answering. If it has stopped, run it again.');
		}
		if (state.status === 429) return gate('Too many wrong tokens were tried. Stop the installer and run it again.');
		if (state.status !== 200) return gate('This address is missing its token. Open the one the installer printed in the terminal.');
		facts = state.data.facts || {};
		choices = state.data.choices || {};
		build();
		restore();
		wear(answers.theme);
		if (state.data.answered) return install();
		$('#panel').dataset.view = 'steps';
		show(0);
	}

	// ---- building the parts that depend on the machine ---------------------------------

	function segment(name, value, text) {
		const label = el('label', { class: 'segment' });
		label.append(el('input', { type: 'radio', name, value }), el('span', {}, text));
		return label;
	}

	function build() {
		const binds = $('#bind-choices');
		const addresses = Array.isArray(facts.addresses) ? facts.addresses : [];
		for (const address of addresses) {
			const label = el('label', { class: 'choice' });
			label.append(
				el('input', { type: 'radio', name: 'bind', value: address }),
				el('span', { class: 'choice-title' }, 'On your network: ' + address),
				el('span', { class: 'choice-note' }, 'Phones and other computers here can reach it.')
			);
			binds.append(label);
		}
		const own = el('label', { class: 'choice' });
		own.append(
			el('input', { type: 'radio', name: 'bind', value: LOOPBACK }),
			el('span', { class: 'choice-title' }, 'This computer only'),
			el('span', { class: 'choice-note' }, 'Nothing else on the network can open it.')
		);
		binds.append(own);

		for (const size of choices.stt || []) $('#stt-choices').append(segment('stt', size, size));
		for (const input of $$('input[name="tts"]')) {
			if (!(choices.tts || []).includes(input.value)) input.closest('.choice').hidden = true;
		}

		if (!facts.gpu) {
			for (const choice of $$('[data-needs-gpu]')) {
				$('input', choice).disabled = true;
				$('[data-gpu-note]', choice).textContent = 'Needs an NVIDIA card with the container toolkit. None was found on this machine.';
			}
		}

		let zones = [];
		try { zones = Intl.supportedValuesOf('timeZone'); } catch { /* an older browser: the field is still a text field */ }
		const list = $('#zones');
		for (const zone of zones) list.append(el('option', { value: zone }));

		// Defaults: what most people on this machine would pick, all of it changeable.
		let zone = '';
		try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch { /* keep the machine's */ }
		answers.server.bind = addresses[0] || LOOPBACK;
		answers.server.timezone = zone || facts.timezone || 'Etc/UTC';
		answers.models.mode = facts.gpu ? 'local' : 'endpoint';
		answers.voice.tts = facts.gpu ? 'kokoro' : 'piper';
		answers.voice.stt = (choices.stt || []).includes('small') ? 'small' : (choices.stt || [''])[0];
		$('#password').minLength = choices.min_password || 10;

		const rail = $('#rail');
		STEPS.forEach((name, index) => {
			const item = el('li');
			const button = el('button', { type: 'button', 'data-rail': name });
			button.append(el('span', { class: 'rail-num' }, String(index + 1).padStart(2, '0')), el('span', { class: 'rail-word' }, ' ' + RAIL[name]));
			button.setAttribute('aria-label', `Step ${index + 1}: ${RAIL[name]}`);
			button.addEventListener('click', () => { if (index <= furthest) { collect(); show(index); } });
			item.append(button);
			rail.append(item);
		});

		const phases = $('#phases');
		for (const phase of PHASES) {
			const row = el('li', { 'data-phase': phase, 'data-state': 'waiting' });
			row.append(el('span', { class: 'mark', 'aria-hidden': 'true' }), el('span', {}, PHASE_WORDS[phase]), el('span', { class: 'word' }, ''));
			phases.append(row);
		}
		swatches();
	}

	// A theme's own ground and accent, read from its rule in themes.css: the swatch types no colour.
	function swatches() {
		// A pack names only what it changes; the rest is the base :root rule's. A browser
		// writes the selector back with its own quotes, so the theme is matched by its name.
		const rules = [];
		for (const sheet of Array.from(document.styleSheets)) {
			try { rules.push(...Array.from(sheet.cssRules)); } catch { /* a sheet that cannot be read names nothing */ }
		}
		const named = (test) => rules.filter((r) => r.style && r.selectorText && test(r.selectorText));
		const base = named((selector) => selector.split(',').some((part) => part.trim() === ':root'));
		for (const look of $$('.look')) {
			const id = look.dataset.look;
			const pack = named((selector) => new RegExp(`\\[data-theme=["']?${id}["']?\\]`).test(selector));
			const value = (name) => {
				for (const rule of [...pack].reverse().concat([...base].reverse())) {
					const found = rule.style.getPropertyValue(name).trim();
					if (found) return found;
				}
				return '';
			};
			if (!value('--jv-accent') || !value('--jv-bg')) continue;
			const swatch = $('.swatch', look);
			swatch.style.background = `radial-gradient(circle at 50% 50%, ${value('--jv-accent')} 0, ${value('--jv-accent')} 5%, ${value('--jv-core-glow')} 6%, ${value('--jv-bg')} 72%)`;
			swatch.style.borderColor = value('--jv-line');
		}
	}

	// ---- answers in and out of the page -------------------------------------------------

	function checked(name) {
		const input = $(`input[name="${name}"]:checked`);
		return input ? input.value : '';
	}

	function check(name, value) {
		for (const input of $$(`input[name="${name}"]`)) input.checked = input.value === value && !input.disabled;
	}

	function collect() {
		answers.server.bind = checked('bind');
		answers.server.password = $('#password').value;
		answers.server.timezone = $('#timezone').value.trim();
		answers.server.public_url = $('#public-url').value.trim();
		answers.models.mode = checked('model-mode');
		answers.models.url = $('#llm-url').value.trim();
		answers.models.key = $('#llm-key').value.trim();
		answers.models.model = $('#llm-model').value.trim();
		answers.voice.tts = checked('tts');
		answers.voice.stt = checked('stt');
		answers.wake.enabled = $('#wake-on').checked;
		answers.devices = $$('input[name="device"]:checked').map((input) => input.value);
		answers.theme = checked('theme') || answers.theme;
		// Kept for a reload, without the two secrets.
		const kept = JSON.parse(JSON.stringify(answers));
		kept.server.password = '';
		kept.models.key = '';
		store.set(STORE, JSON.stringify({ answers: kept, current, furthest }));
	}

	function restore() {
		try {
			const saved = JSON.parse(store.get(STORE) || 'null');
			if (saved && saved.answers) {
				for (const part of ['server', 'models', 'voice', 'wake']) Object.assign(answers[part], saved.answers[part] || {});
				if (Array.isArray(saved.answers.devices)) answers.devices = saved.answers.devices;
				if (NAMES[saved.answers.theme]) answers.theme = saved.answers.theme;
				furthest = Math.min(Number(saved.furthest) || 0, STEPS.length - 1);
			}
		} catch { /* a damaged row is no row */ }
		answers.server.password = '';
		answers.models.key = '';
		check('bind', answers.server.bind);
		if (!checked('bind')) check('bind', LOOPBACK);
		$('#timezone').value = answers.server.timezone;
		$('#public-url').value = answers.server.public_url;
		check('model-mode', answers.models.mode);
		if (!checked('model-mode')) check('model-mode', 'endpoint');
		$('#llm-url').value = answers.models.url;
		$('#llm-model').value = answers.models.model;
		check('tts', answers.voice.tts);
		if (!checked('tts')) check('tts', 'piper');
		check('stt', answers.voice.stt);
		$('#wake-on').checked = answers.wake.enabled !== false;
		for (const input of $$('input[name="device"]')) input.checked = answers.devices.includes(input.value);
		check('theme', answers.theme);
		reflect();
	}

	// What follows from what is chosen: shown and hidden parts, the hint lines, the warning.
	function reflect() {
		$('#endpoint').hidden = checked('model-mode') !== 'endpoint';
		$('#stt-hint').textContent = STT_HINTS[checked('stt')] || '';
		$('.said').hidden = !$('#wake-on').checked;
		const devices = $$('input[name="device"]:checked').map((input) => input.value);
		const away = devices.includes('phone') || devices.includes('car');
		$('#reach-warning').hidden = !(away && checked('bind') === LOOPBACK && !$('#public-url').value.trim());
	}

	function wear(theme) {
		const id = NAMES[theme] ? theme : 'aura';
		document.documentElement.dataset.theme = id;
		for (const node of $$('[data-name]')) node.textContent = NAMES[id];
		document.title = NAMES[id] + ' setup';
		core.theme(id);
	}

	// ---- errors -------------------------------------------------------------------

	const INPUT_FOR = {
		'server.password': ['#password', '#password2'], 'server.timezone': ['#timezone'], 'server.public_url': ['#public-url'],
		'models.url': ['#llm-url'], 'models.model': ['#llm-model'], 'models.key': ['#llm-key']
	};

	function showErrors(errors) {
		for (const node of $$('[data-error]')) node.textContent = errors[node.dataset.error] || '';
		for (const [field, selectors] of Object.entries(INPUT_FOR)) {
			for (const selector of selectors) {
				if (errors[field]) $(selector).setAttribute('aria-invalid', 'true');
				else $(selector).removeAttribute('aria-invalid');
			}
		}
		const fields = Object.keys(errors);
		if (!fields.length) return -1;
		const step = fields[0].includes('.') ? fields[0].split('.')[0] : (STEPS.includes(fields[0]) ? fields[0] : 'review');
		return STEPS.indexOf(step);
	}

	// The checks worth making before the round trip; the server makes all of them again.
	function localErrors(step) {
		const errors = {};
		if (step === 'server') {
			const min = choices.min_password || 10;
			if ($('#password').value.trim().length < min) errors['server.password'] = `At least ${min} characters.`;
			else if ($('#password').value !== $('#password2').value) errors['server.password'] = 'The two do not match.';
			if (!answers.server.timezone) errors['server.timezone'] = 'An IANA name such as Europe/Berlin.';
		}
		if (step === 'models' && answers.models.mode === 'endpoint') {
			if (!answers.models.url) errors['models.url'] = 'The endpoint needs an address.';
			if (!answers.models.model) errors['models.model'] = 'Name the model; Test it lists what the endpoint has.';
		}
		if (step === 'voice' && !answers.voice.stt) errors['voice.stt'] = 'Choose a listening model.';
		return errors;
	}

	// ---- moving between steps -------------------------------------------------------------------

	function show(index) {
		current = Math.max(0, Math.min(index, STEPS.length - 1));
		furthest = Math.max(furthest, current);
		const name = STEPS[current];
		for (const step of $$('.step')) step.hidden = step.dataset.step !== name;
		for (const button of $$('#rail button')) {
			const at = STEPS.indexOf(button.dataset.rail);
			button.disabled = at > furthest;
			button.dataset.state = at < current ? 'done' : 'open';
			if (at === current) button.setAttribute('aria-current', 'step');
			else button.removeAttribute('aria-current');
		}
		$('#back').hidden = current === 0;
		$('#next').textContent = name === 'review' ? 'Install' : 'Continue';
		if (name === 'review') summarise();
		reflect();
		core.mood('idle');
		const title = $('h1', $(`.step[data-step="${name}"]`));
		title.focus({ preventScroll: false });
		say(`Step ${current + 1} of ${STEPS.length}: ${RAIL[name]}`);
	}

	function summarise() {
		const list = $('#summary');
		list.textContent = '';
		const row = (term, text) => list.append(el('dt', {}, term), el('dd', {}, text));
		const local = answers.server.bind === LOOPBACK;
		row('Address', local ? 'This computer only' : answers.server.bind);
		if (answers.server.public_url) row('Away from home', answers.server.public_url);
		row('Password', 'Chosen (only its hash is kept)');
		row('Time zone', answers.server.timezone);
		row('Model', answers.models.mode === 'local' ? 'On this machine' : `${answers.models.model} at ${answers.models.url}`);
		if (answers.models.mode === 'endpoint') row('Key', answers.models.key ? 'Given' : 'None');
		row('Speaking', TTS_WORDS[answers.voice.tts] || '');
		row('Listening', answers.voice.stt);
		row('Wake word', answers.wake.enabled ? 'On: Hey Jarvis' : 'Off: push-to-talk only');
		row('Devices', answers.devices.length ? answers.devices.map((d) => DEVICE_WORDS[d]).join(', ') : 'The browser only');
		row('Theme', NAMES[answers.theme]);
	}

	async function forward() {
		collect();
		const name = STEPS[current];
		const local = localErrors(name);
		if (showErrors(local) >= 0) {
			core.mood('attention');
			say(Object.values(local)[0]);
			return;
		}
		if (name !== 'review') return show(current + 1);

		const next = $('#next');
		next.setAttribute('aria-busy', 'true');
		next.disabled = true;
		core.mood('thinking');
		let reply;
		try {
			reply = await api('/api/answers', answers);
		} catch {
			reply = { status: 0, data: {} };
		}
		next.removeAttribute('aria-busy');
		next.disabled = false;
		if (reply.status === 200 || reply.status === 409) {
			$('#password').value = '';
			$('#password2').value = '';
			$('#llm-key').value = '';
			return install();
		}
		const errors = reply.status === 422 && reply.data.errors ? reply.data.errors
			: { answers: reply.status === 0 ? 'The installer is not answering. If it has stopped, run it again.' : 'The installer refused the answers. Look at the terminal.' };
		const at = showErrors(errors);
		core.mood('attention');
		say(Object.values(errors)[0]);
		if (at >= 0 && at !== current) show(at), showErrors(errors);
	}

	// ---- the endpoint test -------------------------------------------------------------------

	async function testModel() {
		collect();
		const button = $('#test-model');
		const result = $('#test-result');
		button.disabled = true;
		button.setAttribute('aria-busy', 'true');
		result.removeAttribute('data-ok');
		result.textContent = 'Asking the endpoint…';
		core.mood('thinking');
		let reply = { ok: false, error: 'The installer is not answering.' };
		try {
			const res = await api('/api/test-model', { url: answers.models.url, key: answers.models.key, model: answers.models.model });
			if (res.status === 200) reply = res.data;
		} catch { /* the message above stands */ }
		button.disabled = false;
		button.removeAttribute('aria-busy');
		result.dataset.ok = String(Boolean(reply.ok));
		if (reply.ok) {
			const list = $('#models-found');
			list.textContent = '';
			for (const name of reply.models) list.append(el('option', { value: name }));
			if (!answers.models.model && reply.models.length) $('#llm-model').value = reply.models[0];
			const count = reply.models.length;
			const named = $('#llm-model').value.trim();
			result.textContent = `It answered: ${count} model${count === 1 ? '' : 's'}.`
				+ (named ? (reply.models.includes(named) ? ` ${named} is there.` : ` ${named} is not in its list.`) : '');
			core.mood('idle');
		} else {
			result.textContent = reply.error ? reply.error[0].toUpperCase() + reply.error.slice(1) + '.' : 'No answer.';
			core.mood('attention');
		}
	}

	// ---- the install -------------------------------------------------------------------

	let misses = 0;

	function install() {
		$('#panel').dataset.view = 'install';
		$('#steps').hidden = true;
		$('.rail').hidden = true;
		$('#install').hidden = false;
		$('#t-install').focus();
		core.mood('working');
		say('The install has started.');
		poll();
	}

	async function poll() {
		let data = null;
		try {
			const res = await api('/api/progress');
			if (res.status === 200) data = res.data;
		} catch { /* counted below */ }
		if (!data) {
			misses += 1;
			// The installer stops this server when it ends; after a success the page already says so.
			if (misses >= 6 && $('#done').hidden) {
				$('#install-error').textContent = 'The installer has stopped answering. The terminal it runs in says why.';
				core.mood('attention');
				return;
			}
			return setTimeout(poll, POLL_MS);
		}
		misses = 0;
		const finished = draw(data);
		if (!finished) setTimeout(poll, POLL_MS);
	}

	function draw(data) {
		const done = Array.isArray(data.done) ? data.done : [];
		const allDone = data.phase === 'done';
		const failed = data.state === 'failed' || data.phase === 'failed';
		let count = 0;
		for (const row of $$('#phases li')) {
			const phase = row.dataset.phase;
			let state = 'waiting';
			if (allDone || done.includes(phase)) state = 'done';
			else if (phase === data.phase) state = failed ? 'failed' : 'running';
			row.dataset.state = state;
			$('.word', row).textContent = state === 'waiting' ? '' : state;
			if (state === 'done') count += 1;
		}
		$('#install-count').textContent = `${count}/${PHASES.length}`;
		$('#phase-detail').textContent = allDone ? '' : (data.detail || '');
		if (failed) {
			$('#install-error').textContent = 'The install stopped' + (PHASE_WORDS[data.phase] ? ` at "${PHASE_WORDS[data.phase]}"` : '')
				+ '. The terminal has the full reason; fix it and run the same line again: nothing made so far is lost.';
			$('#install-word').textContent = 'Stopped';
			core.mood('attention');
			say('The install stopped.');
			return true;
		}
		if (allDone) {
			$('#install-word').textContent = 'Installed';
			$('#t-install').textContent = `${NAMES[document.documentElement.dataset.theme] || 'AURA'} is up.`;
			const link = $('#open-console');
			if (data.console) link.href = data.console;
			else link.hidden = true;
			const steps = $('#next-steps');
			steps.textContent = '';
			const chosen = answers.devices.length ? answers.devices : [];
			for (const device of chosen) {
				const item = el('li');
				item.append(el('strong', {}, NEXT_STEPS[device][0] + ': '), document.createTextNode(NEXT_STEPS[device][1]));
				steps.append(item);
			}
			const first = el('li');
			first.append(el('strong', {}, 'First: '), document.createTextNode('sign in with the password you chose; the console then walks you through naming yourself and your voice.'));
			steps.prepend(first);
			$('#done').hidden = false;
			core.mood('done');
			say('The install is finished.');
			return true;
		}
		return false;
	}

	// ---- the core -------------------------------------------------------------------

	const core = (() => {
		const canvas = $('#core');
		const ctx = canvas.getContext('2d');
		const still = window.matchMedia('(prefers-reduced-motion: reduce)');
		let width = 0, height = 0, cx = 0, cy = 0, radius = 0, frame = 0;
		let kind = 'aura', feeling = 'idle', energy = 1, clock = 0, last = 0, burst = 0;
		let palette = {};
		let particles = [], stars = [];

		// A fixed seed: the same galaxy on every load, and in every screenshot.
		function random(seed) {
			let a = seed >>> 0;
			return () => {
				a = (a + 0x6d2b79f5) >>> 0;
				let t = a;
				t = Math.imul(t ^ (t >>> 15), t | 1);
				t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
				return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
			};
		}

		function seed() {
			const next = random(8640);
			const count = window.innerWidth <= 900 ? 420 : 900;
			particles = [];
			for (let i = 0; i < count; i += 1) {
				const r = Math.pow(next(), 0.62);
				const arm = (i % 3) * ((Math.PI * 2) / 3);
				const spread = (next() + next() + next() - 1.5) * 0.55 * (0.35 + r);
				particles.push({ r, theta: arm + r * 5.2 + spread, size: 0.6 + next() * 1.5, tint: next(), phase: next() * Math.PI * 2 });
			}
			stars = [];
			for (let i = 0; i < 150; i += 1) stars.push({ x: next(), y: next(), size: 0.4 + next() * 1.1, phase: next() * Math.PI * 2 });
		}

		function readPalette() {
			const style = getComputedStyle(document.documentElement);
			const value = (name) => style.getPropertyValue(name).trim();
			palette = {
				accent: value('--jv-accent'), hot: value('--jv-core-hot'), glow: value('--jv-core-glow'), second: value('--jv-violet'),
				lift: value('--jv-accent-lift') || value('--jv-core-halo'), tick: value('--jv-tick'), warn: value('--jv-warn'), ok: value('--jv-ok'),
				text: value('--jv-text')
			};
		}

		// A token colour at an alpha: the tokens are hex, and anything else is used as it is.
		function fade(colour, alpha) {
			const hex = /^#([0-9a-f]{6})$/i.exec(colour);
			if (!hex) return colour;
			const n = parseInt(hex[1], 16);
			return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
		}

		function place() {
			const ratio = Math.min(window.devicePixelRatio || 1, 2);
			width = window.innerWidth;
			height = window.innerHeight;
			canvas.width = Math.round(width * ratio);
			canvas.height = Math.round(height * ratio);
			ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
			const panel = $('#panel').getBoundingClientRect();
			if (width <= 900) {
				// The band between the masthead and the questions.
				const top = $('.mast').getBoundingClientRect().bottom;
				const room = Math.max(panel.top - top, height * 0.2);
				cx = width / 2;
				cy = top + room / 2;
				radius = Math.min(width, room) * 0.42;
			} else {
				cx = panel.left / 2 + width * 0.02;
				cy = height / 2;
				radius = Math.min(panel.left, height) * 0.36;
			}
		}

		function glow(scale, colour, alpha) {
			const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * scale);
			g.addColorStop(0, fade(colour, alpha));
			g.addColorStop(1, fade(colour, 0));
			ctx.fillStyle = g;
			ctx.beginPath();
			ctx.arc(cx, cy, radius * scale, 0, Math.PI * 2);
			ctx.fill();
		}

		function galaxy(t) {
			ctx.globalCompositeOperation = 'lighter';
			for (const star of stars) {
				ctx.globalAlpha = 0.25 + 0.3 * Math.sin(t * 0.7 + star.phase);
				ctx.fillStyle = palette.text;
				ctx.fillRect(star.x * width, star.y * height, star.size, star.size);
			}
			const tilt = -0.42;
			const cosT = Math.cos(tilt), sinT = Math.sin(tilt);
			const swell = 1 + burst * 0.5;
			for (const p of particles) {
				const angle = p.theta + t * 0.11 * (1.35 - p.r * 0.8);
				const x = Math.cos(angle) * p.r * radius * swell;
				const y = Math.sin(angle) * p.r * radius * 0.56 * swell;
				ctx.globalAlpha = (0.3 + 0.7 * (0.5 + 0.5 * Math.sin(t * 1.6 + p.phase))) * (1 - p.r * 0.55);
				ctx.fillStyle = p.tint < 0.62 ? palette.accent : p.tint < 0.86 ? palette.lift : palette.second;
				ctx.beginPath();
				ctx.arc(cx + x * cosT - y * sinT, cy + x * sinT + y * cosT, p.size, 0, Math.PI * 2);
				ctx.fill();
			}
			ctx.globalAlpha = 1;
			const pulse = 1 + 0.06 * Math.sin(t * 1.3) + burst * 0.6;
			glow(0.75 * pulse, palette.accent, 0.16);
			glow(0.3 * pulse, palette.lift, 0.5);
			glow(0.12 * pulse, palette.hot, 0.95);
		}

		function reactor(t) {
			ctx.globalCompositeOperation = 'lighter';
			const pulse = 1 + 0.05 * Math.sin(t * 1.5) + burst * 0.5;
			glow(0.95 * pulse, palette.accent, 0.12);
			const rings = [
				{ r: 0.92, parts: 3, gap: 0.5, speed: 0.08, w: 1 },
				{ r: 0.78, parts: 5, gap: 0.3, speed: -0.13, w: 1.5 },
				{ r: 0.62, parts: 2, gap: 0.8, speed: 0.21, w: 1 },
				{ r: 0.46, parts: 7, gap: 0.2, speed: -0.3, w: 2 },
				{ r: 0.3, parts: 4, gap: 0.4, speed: 0.42, w: 1 }
			];
			ctx.lineCap = 'round';
			for (const ring of rings) {
				const span = (Math.PI * 2) / ring.parts;
				ctx.strokeStyle = palette.accent;
				ctx.lineWidth = ring.w;
				ctx.globalAlpha = 0.35 + 0.4 * ring.r;
				for (let i = 0; i < ring.parts; i += 1) {
					const from = t * ring.speed + i * span;
					ctx.beginPath();
					ctx.arc(cx, cy, radius * ring.r * (1 + burst * 0.2), from, from + span * (1 - ring.gap));
					ctx.stroke();
				}
			}
			ctx.globalAlpha = 0.5;
			ctx.strokeStyle = palette.tick;
			ctx.lineWidth = 1;
			for (let i = 0; i < 72; i += 1) {
				const a = (i / 72) * Math.PI * 2 + t * 0.02;
				const inner = radius * (i % 6 === 0 ? 0.98 : 1.0), outer = radius * 1.04;
				ctx.beginPath();
				ctx.moveTo(cx + Math.cos(a) * inner, cy + Math.sin(a) * inner);
				ctx.lineTo(cx + Math.cos(a) * outer, cy + Math.sin(a) * outer);
				ctx.stroke();
			}
			ctx.globalAlpha = 1;
			glow(0.24 * pulse, palette.accent, 0.55);
			glow(0.11 * pulse, palette.hot, 0.95);
		}

		// The line from the core to the bracket: what the projection is about. Amber while it waits on a person.
		function leader(t) {
			if (width <= 900) return;
			const panel = $('#panel').getBoundingClientRect();
			const ex = panel.left, ey = panel.top;
			const colour = feeling === 'attention' ? palette.warn : feeling === 'done' ? palette.ok : palette.accent;
			const g = ctx.createLinearGradient(cx, cy, ex, ey);
			g.addColorStop(0, fade(colour, 0));
			g.addColorStop(0.35, fade(colour, 0.3));
			g.addColorStop(1, fade(colour, 0.75));
			ctx.globalCompositeOperation = 'lighter';
			ctx.strokeStyle = g;
			ctx.lineWidth = 1;
			ctx.beginPath();
			ctx.moveTo(cx, cy);
			ctx.lineTo(ex, ey);
			ctx.stroke();
			const along = (t * 0.22 * energy) % 1;
			ctx.fillStyle = fade(colour, 0.9);
			ctx.beginPath();
			ctx.arc(cx + (ex - cx) * along, cy + (ey - cy) * along, 1.6, 0, Math.PI * 2);
			ctx.fill();
		}

		function paint() {
			ctx.globalCompositeOperation = 'source-over';
			ctx.globalAlpha = 1;
			ctx.clearRect(0, 0, width, height);
			if (kind === 'jarvis') reactor(clock);
			else galaxy(clock);
			leader(clock);
		}

		function tick(now) {
			// Never below zero: a frame's timestamp is when the frame began, which can be earlier
			// than the performance.now() run() took when the panel last changed size. A negative
			// step made the burst grow instead of fade, and after enough steps the core's glow
			// covered the page (seen when the page was filmed a frame at a time, T-0865).
			const dt = Math.max(0, Math.min((now - last) / 1000, 0.05));
			last = now;
			const target = feeling === 'thinking' ? 3.2 : feeling === 'working' ? 2.1 : 1;
			energy += (target - energy) * Math.min(1, dt * 2.5);
			burst = Math.max(0, burst - dt * 0.9);
			clock += dt * energy;
			paint();
			frame = requestAnimationFrame(tick);
		}

		function run() {
			cancelAnimationFrame(frame);
			if (document.hidden) return;
			if (still.matches) {
				clock = 40;       // one settled frame, the same every time
				burst = 0;
				paint();
				return;
			}
			last = performance.now();
			frame = requestAnimationFrame(tick);
		}

		function fit() {
			place();
			run();
		}

		seed();
		window.addEventListener('resize', () => { seed(); fit(); });
		document.addEventListener('visibilitychange', run);
		still.addEventListener('change', run);
		if (window.ResizeObserver) new ResizeObserver(fit).observe($('#panel'));

		return {
			theme(id) {
				kind = id;
				// The new pack's colours are computed only after the attribute lands.
				requestAnimationFrame(() => { readPalette(); fit(); });
			},
			mood(name) {
				if (name === 'done' && feeling !== 'done') burst = 1;
				feeling = name;
				if (still.matches) run();
			}
		};
	})();

	// ---- wiring -------------------------------------------------------------------

	$('#steps').addEventListener('submit', (event) => {
		event.preventDefault();
		forward();
	});
	$('#back').addEventListener('click', () => {
		collect();
		showErrors({});
		show(current - 1);
	});
	$('#test-model').addEventListener('click', testModel);
	$('#steps').addEventListener('change', (event) => {
		const input = event.target;
		if (input.name === 'theme') wear(input.value);
		if (input.name === 'device') {
			// The car is reached through the phone: one follows the other.
			const phone = $('input[name="device"][value="phone"]'), car = $('input[name="device"][value="car"]');
			if (input.value === 'car' && input.checked) phone.checked = true;
			if (input.value === 'phone' && !input.checked) car.checked = false;
		}
		reflect();
	});
	$('#steps').addEventListener('input', reflect);
	for (const chip of $$('[data-preset]')) {
		chip.addEventListener('click', () => {
			$('#llm-url').value = chip.dataset.preset;
			$('#llm-url').focus();
		});
	}
	for (const link of $$('[data-goto]')) {
		link.addEventListener('click', () => { collect(); show(STEPS.indexOf(link.dataset.goto)); });
	}

	start();
})();
