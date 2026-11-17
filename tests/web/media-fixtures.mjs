// The media artifacts the mock adapter serves and the specs summon: one
// video, one recording, one PDF, each shaped like the contract's example
// (`tests/contracts/hud-panels.json` examples.video/audio/document) with the
// id the real store would give it — the sha256 of its bytes.
//
// Served by the mock (`mock-hermes.mjs` under `/api/artifacts/<id>` with byte
// ranges); the specs that summon them import the same table, so a fixture
// renamed on one side fails on the other at import time rather than as a 404
// mid-test.
//
// The clip is WebM (VP9 + Opus), not H.264 in MP4: Playwright's Chromium ships
// without the proprietary codecs, so an H.264 fixture never reaches
// `readyState >= 2` there and the spec would fail on the fixture, not the HUD.
// The contract example stays `video/mp4` because that is what the real
// artifacts are (fetch-video.sh); the console plays either.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const fixtures = new URL('../../aura-web/e2e/fixtures/', import.meta.url);

function artifact(name, mime, title, provenance) {
	const file = fileURLToPath(new URL(name, fixtures));
	const id = createHash('sha256').update(readFileSync(file)).digest('hex');
	return {
		id,
		file,
		mime,
		/** The console-relative url a panel carries: the relay adds the key. */
		url: `/hermes/api/artifacts/${id}`,
		/** The `hud.panel.show` payload, as the contract shapes it. */
		payload: { url: `/hermes/api/artifacts/${id}`, mime, title, provenance }
	};
}

export const MEDIA = {
	video: artifact('e2e-clip.webm', 'video/webm', 'A test card, two seconds', { tool: 'fetch_video', source_url: 'https://example.org/clips/test-card' }),
	audio: artifact('e2e-tone.mp3', 'audio/mpeg', 'A 440 Hz tone, two seconds', { tool: 'fetch_audio', source_url: 'https://example.org/tones/a4.mp3' }),
	document: artifact('e2e-note.pdf', 'application/pdf', 'The study runs cold', { tool: 'extract_page', source_url: 'https://example.org/notes/study.pdf' }),
	// The contract's image example names a file no mock serves; a spec
	// drawing it recorded a 404 as the image body's defect.
	image: artifact('e2e-image.png', 'image/png', 'The Jarvis mark', { tool: 'image_generate', source_url: '' })
};

/** By artifact id, for the mock's route. */
export const MEDIA_BY_ID = Object.fromEntries(Object.values(MEDIA).map((m) => [m.id, m]));
