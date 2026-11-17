// The orb's half of the geometry contract.
//
// `tests/contracts/reactor_geometry.json` is the one table; `Reactor.svelte`
// types those numbers as constants and `reactor.test.ts` holds it to the file.
// The orb types them too, in `geometry.ts`, and this is what holds *it* — read
// at test time with `new URL`, which vitest resolves and the bundler never sees.
//
// It exists because the obvious alternative broke the image. `geometry.ts`
// imported the JSON directly, and that is a bundled import reaching outside
// `aura-web/`; `build:web` uses `CONTEXT: aura-web`, so rollup failed with
// "Could not resolve ../../../../tests/contracts/reactor_geometry.json" and the
// web image could not be built. A test may read across that boundary. Shipped
// code may not.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { GEOMETRY, type ReactorGeometry } from './geometry';

const contract = JSON.parse(
	readFileSync(new URL('../../../../tests/contracts/reactor_geometry.json', import.meta.url), 'utf8')
) as Record<string, unknown>;

describe('the orb reads the same geometry as the SVG', () => {
	it.each(Object.keys(GEOMETRY) as (keyof ReactorGeometry)[])(
		'%s matches the contract',
		(key) => {
			expect(contract[key], `${key} is missing from reactor_geometry.json`).toBeDefined();
			expect(GEOMETRY[key]).toEqual(contract[key]);
		}
	);

	it('does not reach outside aura-web from shipped code', () => {
		// The defect this file is named for, pinned: a bundled import of the
		// contract puts a path in the image build that does not exist there.
		const source = readFileSync(new URL('./geometry.ts', import.meta.url), 'utf8');
		expect(source).not.toMatch(/^\s*import .*tests\/contracts/m);
	});
});
