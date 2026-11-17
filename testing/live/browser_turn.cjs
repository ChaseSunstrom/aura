// One turn through the real console, in a real browser.
//
// Run as: node browser_turn.cjs '<json job>'  ->  one JSON line on stdout.
//
// Why Node and not python-playwright: `aura-web/node_modules` already has
// Playwright and its Chromium. A second copy for Python would be 150 MB and a
// second version to keep in step, for the same browser doing the same job.
//
// Two modes:
//   voice — Chromium's fake capture device is pointed at a WAV, so the page's
//           OWN VAD hears speech, opens its OWN websocket and streams its OWN
//           audio. Nothing is stubbed: `?e2e=1` (which forces a turn to start
//           because the mock backend has no audio) is deliberately NOT used.
//   text  — the chat panel, typed into, which is that surface's real entry point.
// Resolved from jarvis-web's node_modules by path, not by cwd: this file lives
// outside that tree, so a bare require() finds nothing wherever it is run from.
const { chromium } = require(require.resolve('@playwright/test', {
	paths: [require('path').join(__dirname, '..', '..', 'aura-web')],
}));

const job = JSON.parse(process.argv[2] || '{}');
const timeout = job.timeoutMs || 180000;

const say = (payload) => process.stdout.write(JSON.stringify(payload) + '\n');

(async () => {
	const args = ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'];
	// A typed turn gets no microphone: Chromium's fake device beeps when no file
	// is given, the page's VAD heard it, and a "BEEP BEEP BEEP" voice turn
	// landed on the same session and swallowed the typed one's answer.
	const voice = job.mode === 'voice';
	if (voice) args.push('--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream');
	if (voice && job.wav) {
		// %noloop matters: looped audio makes the VAD hear a second utterance
		// while the first is still being answered, and the run never settles.
		args.push(`--use-file-for-fake-audio-capture=${job.wav}%noloop`);
	}

	const browser = await chromium.launch({ headless: job.headless !== false, args });
	const context = await browser.newContext({ permissions: voice ? ['microphone'] : [] });
	// A console with a password (the e2e server's) refuses a page that is not signed in.
	if (job.cookie) await context.addCookies([{ ...job.cookie, url: job.url }]);
	const page = await context.newPage();

	const marks = {};
	const mark = (name) => {
		if (marks[name] === undefined) marks[name] = Date.now() - t0;
	};
	let ttsUrl = '';
	page.on('response', (r) => {
		const url = r.url();
		if (url.includes('/api/tts_proxy/')) {
			ttsUrl = url;
			mark('tts');
		}
	});
	const logs = [];
	page.on('console', (m) => logs.push(`${m.type()}: ${m.text()}`.slice(0, 300)));
	page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`.slice(0, 300)));

	const t0 = Date.now();
	try {
		// The stored session the rig's last page was on rides in the URL; the HUD
		// resumes it before the turn is sent (T-0489).
		const params = new URLSearchParams();
		if (job.session) params.set('session', job.session);
		const query = params.toString();
		// A typed turn goes to /chat, the chat screen on the room's own Hermes
		// session: the room's dock folds its box and lists no replies, and the
		// old `?mode=chat` page and its `chat-input`/`chat-text` are gone, so
		// every text-ui turn timed out waiting on a box that never came.
		const path = job.mode === 'text' ? '/chat' : '/';
		await page.goto(`${job.url}${path}${query ? `?${query}` : ''}`, {
			waitUntil: 'domcontentloaded',
			timeout
		});

		if (job.mode === 'text') {
			const input = page.getByTestId('chatscreen-input');
			await input.waitFor({ state: 'visible', timeout });
			await page.locator('[data-testid="shell"][data-settled]').waitFor({ timeout });
			await input.fill(job.text);
			await page.getByTestId('chatscreen-send').click();
		} else {
			// The page opens the microphone on mount; the fake device starts
			// playing the file at the same moment, and the WAV's lead-in silence
			// is what keeps the first syllable from being lost to that race.
			await page.getByTestId('mic').waitFor({ state: 'visible', timeout });
		}

		// The core's conversation panel draws the newest line of each side: what the
		// microphone heard (then sent) as `conversation-you`, the answer as
		// `conversation-jarvis`. The chat screen draws a list.
		const transcriptBox = page.getByTestId('conversation-you');
		// This turn's answer is the first finished reply after the message it sent.
		// Counting replies before sending was not enough: `data-settled` is the
		// layout's hydration, the resumed history lands after it, and a count of 0
		// read turn 1's old answer back as turn 2's.
		const sent = page
			.locator('[data-testid="chatscreen-msg"][data-role="user"]', { hasText: job.text || '' })
			.last();
		const responseBox =
			job.mode === 'text'
				? sent
						.locator('xpath=following-sibling::li[@data-role="assistant"][not(@data-streaming)][1]')
						.locator('.words')
				: // Only an answer drawn after the heard line: a resumed session's older
					// answer is the newest JARVIS turn until this one's first word lands.
					transcriptBox
						.locator('xpath=ancestor::article[1]/following-sibling::article[@data-turn="jarvis"]')
						.getByTestId('conversation-jarvis');

		// Non-empty, not merely present: both boxes exist from the first paint.
		// Bounded: a box that is not there waited out Playwright's 30 s default
		// on every poll, and the turn overran the rig's kill instead of reporting.
		const settled = async (locator) => {
			const text = (await locator.textContent({ timeout: 1000 }).catch(() => '')) || '';
			return text.trim();
		};
		const deadline = Date.now() + timeout;
		let transcript = '';
		let response = '';
		while (Date.now() < deadline) {
			// In chat mode what was "heard" is what was typed: there is no STT in
			// that surface, and claiming a transcript would put a number in the
			// WER column for a path that never recognised anything.
			// Re-read in voice mode: a resumed session's older line is the newest "you"
			// until the heard one replaces it, and a latched first read kept the old words.
			transcript = job.mode === 'text' ? job.text : await settled(transcriptBox);
			if (transcript) mark('stt');
			response = await settled(responseBox);
			if (response) {
				mark('response');
				// Let a streaming answer finish: two identical reads 700 ms apart.
				await page.waitForTimeout(700);
				const again = await settled(responseBox);
				if (again === response) break;
				response = again;
			}
			await page.waitForTimeout(200);
		}
		// The answer can land in the same paint as the heard line, after this loop's read of it.
		if (job.mode !== 'text') transcript = (await settled(transcriptBox)) || transcript;
		if (transcript) mark('stt');

		// A tool the matrix asks about parks the turn on the owner's approval, and
		// a bare "no answer" sent the last one hunting the page for a rendering bug.
		const asked = response ? '' : await settled(page.getByTestId('approval-card').first());

		// What the page shows *after* the answer — the task dock filling, a
		// step count rising. Each probe waits for its text, bounded, and
		// reports what was actually there when it gave up, so a failure names
		// the real state of the page and not just "not found".
		const probes = [];
		for (const probe of job.probes || []) {
			const until = Date.now() + (probe.withinMs || 30000);
			let text = '';
			let ok = false;
			while (Date.now() < until) {
				text = await settled(page.getByTestId(probe.testid).first());
				if (text.toLowerCase().includes(String(probe.contains || '').toLowerCase())) {
					ok = true;
					break;
				}
				await page.waitForTimeout(500);
			}
			probes.push({ testid: probe.testid, contains: probe.contains, ok, text: text.slice(0, 200) });
		}
		// What this page remembered, so the next turn of the thread opens it.
		const session = await page
			.evaluate(() => localStorage.getItem('jarvis.hermes.session'))
			.catch(() => '');
		say({
			transcript,
			response,
			session: session || '',
			ttsUrl,
			latency: marks,
			probes,
			logs: logs.slice(-20),
			error: response
				? ''
				: asked
					? `the turn waited on an approval: ${asked.slice(0, 200)}`
					: 'no answer appeared before the timeout'
		});
	} catch (err) {
		say({ error: String((err && err.message) || err), logs: logs.slice(-20) });
	} finally {
		await browser.close();
	}
})();
