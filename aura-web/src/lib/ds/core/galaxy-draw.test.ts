import { describe, expect, it } from 'vitest';
import { createDrawList, galaxyAt, galaxyInto } from './galaxy';
import { createGalaxy2d, createGalaxyGl } from './galaxy-draw';

type Call = [string, ...unknown[]];

/** A context that answers everything and writes down what it was asked. */
function recorder(calls: Call[], answers: Record<string, unknown> = {}) {
	return new Proxy({} as Record<string, unknown>, {
		get(target, name: string) {
			if (name in answers) return answers[name];
			if (name in target) return target[name];
			// GL's enums: their own name stands in for the number.
			if (/^[A-Z_0-9]+$/.test(name)) return name;
			return (...args: unknown[]) => {
				calls.push([name, ...args]);
				return { addColorStop: () => {} };
			};
		},
		set(target, name: string, value) {
			calls.push([`=${name}`, value]);
			target[name] = value;
			return true;
		}
	});
}

function fakeCanvas(kind: 'webgl2' | '2d', calls: Call[], answers: Record<string, unknown> = {}) {
	const listeners: Record<string, () => void> = {};
	const ctx = recorder(calls, answers);
	const canvas = {
		width: 0,
		height: 0,
		getContext: (k: string) => (k === kind ? ctx : null),
		addEventListener: (name: string, fn: () => void) => (listeners[name] = fn),
		removeEventListener: (name: string) => delete listeners[name]
	};
	return { canvas: canvas as unknown as HTMLCanvasElement, listeners, raw: canvas };
}

const glAnswers = (calls: Call[]) => ({
	getShaderParameter: () => true,
	getProgramParameter: () => true,
	getExtension: () => ({ loseContext: () => calls.push(['loseContext']) })
});

function frame(state: Parameters<typeof galaxyAt>[0], w = 400, h = 300) {
	const list = createDrawList();
	galaxyInto(galaxyAt(state, 'idle', 1500), list, w, h);
	return list;
}

describe('the galaxy on WebGL2', () => {
	it('is null where there is no WebGL2', () => {
		const { canvas } = fakeCanvas('2d', []);
		expect(createGalaxyGl(canvas, () => {})).toBeNull();
	});

	it('draws each batch instanced, adding light and laying dust over', () => {
		const calls: Call[] = [];
		const { canvas, raw } = fakeCanvas('webgl2', calls, glAnswers(calls));
		const gl = createGalaxyGl(canvas, () => {})!;
		expect(gl.kind).toBe('webgl');
		gl.resize(200, 150, 2);
		expect([raw.width, raw.height]).toEqual([400, 300]);
		const list = frame('working');
		calls.length = 0;
		gl.draw(list);
		const draws = calls.filter((c) => c[0] === 'drawArraysInstanced');
		expect(draws.map((c) => c[4])).toEqual(list.batches.map((b) => b.count));
		const blends = calls.filter((c) => c[0] === 'blendFunc').map((c) => c[2]);
		expect(blends).toEqual(['ONE', 'ONE_MINUS_SRC_ALPHA', 'ONE']);
		expect(calls.some((c) => c[0] === 'clear')).toBe(true);
	});

	it('grows its buffer for a bigger list, and frees everything on dispose', () => {
		const calls: Call[] = [];
		const { canvas, listeners } = fakeCanvas('webgl2', calls, glAnswers(calls));
		let lost = 0;
		const gl = createGalaxyGl(canvas, () => (lost += 1))!;
		gl.resize(400, 300, 1);
		const small = createDrawList(1);
		galaxyInto(galaxyAt('idle', 'idle', 0, 'mini'), small, 400, 300);
		gl.draw(small);
		const big = createDrawList(1);
		galaxyInto(galaxyAt('thinking'), big, 4000, 3000);
		calls.length = 0;
		gl.draw(big);
		expect(calls.filter((c) => c[0] === 'bufferData')).toHaveLength(1);
		listeners.webglcontextlost();
		expect(lost).toBe(1);
		gl.dispose();
		const names = calls.map((c) => c[0]);
		for (const n of ['deleteBuffer', 'deleteProgram', 'deleteVertexArray', 'loseContext'])
			expect(names).toContain(n);
		expect(listeners.webglcontextlost).toBeUndefined();
	});
});

describe('the galaxy in Canvas2D', () => {
	it('draws the same list: squares as rects, blobs as gradients, lines as strokes', () => {
		const calls: Call[] = [];
		const { canvas } = fakeCanvas('2d', calls);
		const ctx = createGalaxy2d(canvas)!;
		expect(ctx.kind).toBe('canvas2d');
		ctx.resize(400, 300, 1);
		const list = frame('working');
		calls.length = 0;
		ctx.draw(list);
		const count = (n: string) => calls.filter((c) => c[0] === n).length;
		expect(count('fillRect')).toBeGreaterThan(2000);
		expect(count('createRadialGradient')).toBeGreaterThan(10);
		expect(count('stroke')).toBeGreaterThan(300);
		const modes = calls.filter((c) => c[0] === '=globalCompositeOperation').map((c) => c[1]);
		expect(modes).toEqual(['lighter', 'source-over', 'lighter']);
		// Every colour is a token-free hex with alpha: no rgb() strings built at run time.
		const fills = calls.filter((c) => c[0] === '=fillStyle' && typeof c[1] === 'string');
		expect(fills.every((c) => /^#[0-9a-f]{8}$/.test(c[1] as string))).toBe(true);
	});

	it('is null where there is no 2D context', () => {
		const { canvas } = fakeCanvas('webgl2', []);
		expect(createGalaxy2d(canvas)).toBeNull();
	});
});
