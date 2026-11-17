// The two drawers of the galaxy's draw list (galaxy.ts): raw WebGL2, one instanced
// program for every star, blob and line, and Canvas2D for where there is no GPU or
// after the GPU context is lost. Both draw the same list in the same order with the
// same blends, so they agree to the antialiasing.
//
// Raw WebGL2 rather than three.js (which the reactor uses): the galaxy is one flat
// list of screen-space instances, a scene graph would only stand between it and
// the buffer. No new dependency.
//
// Does not cover: restoring a lost context. The owner (renderer.ts) drops to 2D
// on a fresh canvas instead, as the reactor does.

import { BLOB, LINE, STRIDE, type DrawList } from './galaxy';

export interface GalaxyDrawer {
	readonly kind: 'webgl' | 'canvas2d';
	/** CSS pixels and the device pixel ratio (already capped); the list is in device pixels. */
	resize(width: number, height: number, dpr: number): void;
	draw(list: DrawList): void;
	dispose(): void;
}

/** A side in device pixels: never 0, so a hidden surface still has a framebuffer. */
export const pixels = (css: number, dpr: number) =>
	Math.max(1, Math.round((Number.isFinite(css) ? css : 0) * (Number.isFinite(dpr) ? dpr : 1)));

const VERTEX = /* glsl */ `#version 300 es
layout(location = 0) in vec2 corner;
layout(location = 1) in vec4 seg;
layout(location = 2) in vec2 ws;
layout(location = 3) in vec3 color;
layout(location = 4) in vec2 alpha;
uniform vec2 size;
out vec2 local;
out float hw;
out float shape;
out vec3 tone;
out vec2 fade;
void main() {
	vec2 p;
	shape = ws.y;
	if (shape < 1.5) {
		// A square (half-side w) or a soft blob (radius w), one pixel of margin to antialias.
		float e = shape < 0.5 ? ws.x + 1.0 : ws.x;
		p = seg.xy + corner * e;
		local = corner * e;
		hw = ws.x;
	} else {
		// A line w wide, widened a pixel each side for its antialiased edge.
		vec2 d = seg.zw - seg.xy;
		float len = length(d);
		vec2 dir = len > 0.0 ? d / len : vec2(1.0, 0.0);
		hw = max(ws.x * 0.5, 0.5);
		float t = corner.x * 0.5 + 0.5;
		p = mix(seg.xy, seg.zw, t) + vec2(-dir.y, dir.x) * corner.y * (hw + 1.0);
		local = vec2(t, corner.y * (hw + 1.0));
	}
	tone = color;
	fade = alpha;
	vec2 clip = p / size * 2.0 - 1.0;
	gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
}`;

const FRAGMENT = /* glsl */ `#version 300 es
precision mediump float;
in vec2 local;
in float hw;
in float shape;
in vec3 tone;
in vec2 fade;
out vec4 outColor;
void main() {
	float a;
	if (shape < 0.5) {
		// Pixel coverage per axis, so a star smaller than a pixel dims rather than vanishes.
		float k = min(1.0, hw * 2.0);
		vec2 c = clamp(hw + 0.5 - abs(local), 0.0, 1.0) * k;
		a = fade.x * c.x * c.y;
	} else if (shape < 1.5) {
		a = fade.x * max(0.0, 1.0 - length(local) / max(hw, 0.001));
	} else {
		a = mix(fade.x, fade.y, local.x) * clamp(hw + 0.5 - abs(local.y), 0.0, 1.0) * min(1.0, hw * 2.0);
	}
	outColor = vec4(tone * a, a);
}`;

const BYTES = STRIDE * 4;

/** The WebGL2 drawer, or null when the page has no WebGL2 or the shaders will not build. */
export function createGalaxyGl(canvas: HTMLCanvasElement, onLost: () => void): GalaxyDrawer | null {
	const maybe = canvas.getContext('webgl2', { alpha: true, antialias: false, premultipliedAlpha: true });
	if (!maybe) return null;
	const gl = maybe;
	const shader = (type: number, src: string) => {
		const s = gl.createShader(type);
		if (!s) return null;
		gl.shaderSource(s, src);
		gl.compileShader(s);
		if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
			gl.deleteShader(s);
			return null;
		}
		return s;
	};
	const vs = shader(gl.VERTEX_SHADER, VERTEX);
	const fs = shader(gl.FRAGMENT_SHADER, FRAGMENT);
	const program = gl.createProgram();
	if (!vs || !fs || !program) return null;
	gl.attachShader(program, vs);
	gl.attachShader(program, fs);
	gl.linkProgram(program);
	gl.deleteShader(vs);
	gl.deleteShader(fs);
	if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
		gl.deleteProgram(program);
		return null;
	}
	const size = gl.getUniformLocation(program, 'size');
	const vao = gl.createVertexArray();
	gl.bindVertexArray(vao);
	const corners = gl.createBuffer();
	gl.bindBuffer(gl.ARRAY_BUFFER, corners);
	gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
	gl.enableVertexAttribArray(0);
	gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
	const instances = gl.createBuffer();
	gl.bindBuffer(gl.ARRAY_BUFFER, instances);
	let capacity = 0;
	// location, floats, offset in floats: x0 y0 x1 y1 | w shape | r g b | a0 a1
	const LAYOUT = [
		[1, 4, 0],
		[2, 2, 4],
		[3, 3, 6],
		[4, 2, 9]
	] as const;
	for (const [loc] of LAYOUT) {
		gl.enableVertexAttribArray(loc);
		gl.vertexAttribDivisor(loc, 1);
	}
	const point = (first: number) => {
		for (const [loc, n, at] of LAYOUT)
			gl.vertexAttribPointer(loc, n, gl.FLOAT, false, BYTES, first * BYTES + at * 4);
	};
	gl.bindVertexArray(null);
	const lost = () => onLost();
	canvas.addEventListener('webglcontextlost', lost);

	return {
		kind: 'webgl',
		resize(width, height, dpr) {
			canvas.width = pixels(width, dpr);
			canvas.height = pixels(height, dpr);
		},
		draw(list) {
			gl.viewport(0, 0, canvas.width, canvas.height);
			gl.clearColor(0, 0, 0, 0);
			gl.clear(gl.COLOR_BUFFER_BIT);
			if (!list.n) return;
			gl.useProgram(program);
			gl.uniform2f(size, list.width || 1, list.height || 1);
			gl.bindVertexArray(vao);
			gl.bindBuffer(gl.ARRAY_BUFFER, instances);
			const used = list.data.subarray(0, list.n * STRIDE);
			if (list.n > capacity) {
				// Grow by doubling, from the list's own capacity: a few reallocations a session, not one a frame.
				capacity = Math.max(list.n, capacity * 2, list.data.length / STRIDE);
				gl.bufferData(gl.ARRAY_BUFFER, capacity * BYTES, gl.DYNAMIC_DRAW);
			}
			gl.bufferSubData(gl.ARRAY_BUFFER, 0, used);
			gl.enable(gl.BLEND);
			for (const b of list.batches) {
				if (!b.count) continue;
				gl.blendFunc(gl.ONE, b.over ? gl.ONE_MINUS_SRC_ALPHA : gl.ONE);
				point(b.start);
				gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, b.count);
			}
			gl.bindVertexArray(null);
		},
		dispose() {
			canvas.removeEventListener('webglcontextlost', lost);
			gl.deleteBuffer(corners);
			gl.deleteBuffer(instances);
			gl.deleteVertexArray(vao);
			gl.deleteProgram(program);
			gl.getExtension('WEBGL_lose_context')?.loseContext();
		}
	};
}

const hx = (v: number) =>
	Math.round(Math.min(255, Math.max(0, v * 255)))
		.toString(16)
		.padStart(2, '0');
/** The list's colours are 0..1; Canvas2D takes a hex with alpha (no colour typed here). */
const paint = (d: Float32Array, o: number, a: number) =>
	`#${hx(d[o + 6])}${hx(d[o + 7])}${hx(d[o + 8])}${hx(a)}`;

/** The Canvas2D drawer, or null when even 2D is refused. */
export function createGalaxy2d(canvas: HTMLCanvasElement): GalaxyDrawer | null {
	const maybe = canvas.getContext('2d');
	if (!maybe) return null;
	const ctx = maybe;
	return {
		kind: 'canvas2d',
		resize(width, height, dpr) {
			canvas.width = pixels(width, dpr);
			canvas.height = pixels(height, dpr);
		},
		draw(list) {
			// clearRect ignores the composite mode; each batch sets its own below.
			ctx.clearRect(0, 0, canvas.width, canvas.height);
			ctx.lineCap = 'butt';
			const d = list.data;
			for (const b of list.batches) {
				if (!b.count) continue;
				ctx.globalCompositeOperation = b.over ? 'source-over' : 'lighter';
				for (let i = b.start; i < b.start + b.count; i++) {
					const o = i * STRIDE;
					const x = d[o];
					const y = d[o + 1];
					const w = d[o + 4];
					const shape = d[o + 5];
					const a0 = d[o + 9];
					if (shape === LINE) {
						const a1 = d[o + 10];
						if (a0 === a1) ctx.strokeStyle = paint(d, o, a0);
						else {
							const g = ctx.createLinearGradient(x, y, d[o + 2], d[o + 3]);
							g.addColorStop(0, paint(d, o, a0));
							g.addColorStop(1, paint(d, o, a1));
							ctx.strokeStyle = g;
						}
						ctx.lineWidth = w;
						ctx.beginPath();
						ctx.moveTo(x, y);
						ctx.lineTo(d[o + 2], d[o + 3]);
						ctx.stroke();
					} else if (shape === BLOB) {
						const g = ctx.createRadialGradient(x, y, 0, x, y, w);
						g.addColorStop(0, paint(d, o, a0));
						g.addColorStop(1, paint(d, o, 0));
						ctx.fillStyle = g;
						ctx.fillRect(x - w, y - w, w * 2, w * 2);
					} else {
						ctx.fillStyle = paint(d, o, a0);
						ctx.fillRect(x - w, y - w, w * 2, w * 2);
					}
				}
			}
		},
		dispose() {
			// Nothing held but the canvas, which its owner removes.
		}
	};
}
