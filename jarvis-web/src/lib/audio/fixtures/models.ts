// The wake chain's models for the tests (wake.test.ts, e2e/wake.spec.ts), never the console's:
// the house's mirror on this box, else the pinned release from the house's own catalogue (copied
// into wake.json), checked against its sha256 and cached under node_modules.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';

// Read, not imported: Playwright's loader takes a JSON import only with an import attribute.
const fixture: { head: string; headSha256: string; models: Record<WakeModel, { url: string; sha256: string }> } = JSON.parse(
	readFileSync(new URL('./wake.json', import.meta.url), 'utf8')
);
export type WakeModel = 'melspectrogram.onnx' | 'embedding_model.onnx';

const ROOT = new URL('../../../../../', import.meta.url).pathname;
const CACHE = new URL('../../../../node_modules/.cache/jarvis-wake/', import.meta.url).pathname;

export const sha = (b: Uint8Array) => createHash('sha256').update(b).digest('hex');

/** The head the fixture's scores were made with; fails when it changed under them. */
export function head(): Uint8Array {
	const b = readFileSync(ROOT + fixture.head);
	if (sha(b) !== fixture.headSha256) throw new Error('the head changed: rerun jarvis-web/scripts/wake-fixture.py');
	return b;
}

export async function model(name: WakeModel): Promise<Uint8Array> {
	const { url, sha256 } = fixture.models[name];
	for (const dir of [process.env.WAKE_MODELS_DIR, `${ROOT}aura-core/config/models/`, CACHE]) {
		const p = dir && `${dir.replace(/\/?$/, '/')}${name}`;
		if (p && existsSync(p)) {
			const b = readFileSync(p);
			if (sha(b) === sha256) return b;
		}
	}
	const b = new Uint8Array(await (await fetch(url)).arrayBuffer());
	if (sha(b) !== sha256) throw new Error(`${name} from ${url} is not the pinned file`);
	mkdirSync(CACHE, { recursive: true });
	writeFileSync(CACHE + name, b);
	return b;
}
