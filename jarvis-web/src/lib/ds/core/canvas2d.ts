// The core in Canvas2D: the reference's own drawing, kept as the fallback for a
// browser with no WebGL (or a lost context) and as the picture the GPU core is
// held to.
//
// Painter's order stands in for a depth buffer: the back half of the shell,
// then the ring segments far to near (their dark faces hide what is behind
// them), then the front half, the flow, the core and the shock rings. Lit
// things add ('lighter'), as light does.
//
// Does not cover: per-pixel depth. A segment that pierces another is drawn in
// segment order, not depth order; at these sizes it does not read.

import {
	BOARD_WIDTH,
	CAMERA,
	FLOW,
	PALETTE,
	RINGS,
	segmentOf,
	SEGMENT,
	SPREAD,
	STRUT_IN,
	TILT,
	cometAngle,
	coreGlow,
	flowAngle,
	ringPose,
	segmentLit,
	shellInto,
	shockRing,
	shocks,
	type Core,
	type Renderer,
	type Rgb,
	type Shell
} from './engine';

const hx = (v: number) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0');
/** A canvas style: colour c (a token's, through rgbOf) at alpha a, as #rrggbbaa. */
const paint = (c: Rgb, a = 1) => `#${hx(c[0])}${hx(c[1])}${hx(c[2])}${hx(a * 255)}`;

/** Alpha levels the shell's points are batched into: one fillStyle per level, not per point. */
const LEVELS = 8;
/** Points along each arc of a segment face. */
const ARC = 5;
const TC = Math.cos(TILT);
const TS = Math.sin(TILT);

/** A drawable segment: its two faces on screen (x, y pairs), how near each is, and how lit. */
interface Face {
	near: Float32Array;
	far: Float32Array;
	depth: number;
	lit: number;
	color: Rgb;
}

export function createCanvas2d(canvas: HTMLCanvasElement): Renderer | null {
	const maybe = canvas.getContext('2d');
	if (!maybe) return null;
	// Re-bound so the hoisted drawing functions below see it as non-null.
	const ctx = maybe;
	let W = 1;
	let H = 1;
	let dpr = 1;
	let buf = new Float32Array(0);
	let screen = new Float32Array(0);
	const faces: Face[] = RINGS.flatMap((R) =>
		Array.from({ length: R.seg }, () => ({
			near: new Float32Array(ARC * 4),
			far: new Float32Array(ARC * 4),
			depth: 0,
			lit: 1,
			color: PALETTE.white
		}))
	);

	// The view: x right, y up, z toward the viewer; tilted, then seen from CAMERA.
	let cx = 0;
	let cy = 0;
	let S = 1;
	const out = { x: 0, y: 0, z: 0, s: 1 };
	const project = (x: number, y: number, z: number) => {
		const y1 = y * TC - z * TS;
		const z1 = y * TS + z * TC;
		const s = CAMERA / (CAMERA - z1);
		out.x = cx + x * S * s;
		out.y = cy - y1 * S * s;
		out.z = z1;
		out.s = s;
		return out;
	};

	// A point on ring ri, in the ring's own plane at angle th, radius r, depth z:
	// turned a about X, then b about Y (three's 'YXZ'), scaled by the pulse.
	let ca = 1;
	let sa = 0;
	let cb = 1;
	let sb = 0;
	let pulse = 1;
	const onRing = (th: number, r: number, z: number) => {
		const x0 = Math.cos(th) * r * pulse;
		const y0 = Math.sin(th) * r * pulse;
		const z0 = z * pulse;
		const y1 = y0 * ca - z0 * sa;
		const z1 = y0 * sa + z0 * ca;
		return project(x0 * cb + z1 * sb, y1, z1 * cb - x0 * sb);
	};
	const pose = (core: Core, ri: number) => {
		const p = ringPose(core, ri);
		ca = Math.cos(p.a);
		sa = Math.sin(p.a);
		cb = Math.cos(p.b);
		sb = Math.sin(p.b);
		pulse = p.pulse;
	};

	// A point part way along a face's arc: `base` is 0 for the outer arc, ARC
	// for the inner; `idx` is fractional, between the arc's points.
	const pt = { x: 0, y: 0 };
	const along = (pts: Float32Array, base: number, idx: number) => {
		const o = Math.min(ARC - 2, Math.floor(idx));
		const t = idx - o;
		const i = (base + o) * 2;
		pt.x = pts[i] + (pts[i + 2] - pts[i]) * t;
		pt.y = pts[i + 1] + (pts[i + 3] - pts[i + 1]) * t;
	};

	const poly = (pts: Float32Array) => {
		ctx.beginPath();
		ctx.moveTo(pts[0], pts[1]);
		for (let i = 2; i < pts.length; i += 2) ctx.lineTo(pts[i], pts[i + 1]);
		ctx.closePath();
	};

	function shell(core: Core, sh: Shell, front: boolean, sc: number) {
		const color = core.look.color;
		for (let level = 0; level < LEVELS; level++) {
			ctx.fillStyle = paint(color, ((level + 0.5) / LEVELS) * 0.9);
			for (let i = 0; i < sh.n; i++) {
				const z = screen[i * 4 + 2];
				if (z >= 0 !== front || sh.hot[i]) continue;
				const a = screen[i * 4 + 3];
				if (Math.min(LEVELS - 1, Math.floor(a * LEVELS)) !== level) continue;
				const size = (0.4 + 1.5 * a) * sc;
				ctx.fillRect(screen[i * 4] - size / 2, screen[i * 4 + 1] - size / 2, size, size);
			}
		}
		for (let i = 0; i < sh.n; i++) {
			if (!sh.hot[i] || screen[i * 4 + 2] >= 0 !== front) continue;
			const size = 2.4 * sc;
			ctx.fillStyle = paint(PALETTE.hot, screen[i * 4 + 3]);
			ctx.fillRect(screen[i * 4] - size / 2, screen[i * 4 + 1] - size / 2, size, size);
		}
	}

	function rings(core: Core, sc: number) {
		let f = 0;
		RINGS.forEach((R, ri) => {
			pose(core, ri);
			const span = (Math.PI * 2) / R.seg;
			const S = segmentOf(ri);
			const h = S.depth / 2;
			const half = (span - S.gap) / 2;
			const r0 = R.r - S.inner;
			const r1 = R.r + S.outer;
			for (let k = 0; k < R.seg; k++, f++) {
				const face = faces[f];
				const mid = core.spinA * R.dir + k * span;
				let zFront = 0;
				let zBack = 0;
				for (let side = 0; side < 2; side++) {
					const pts = side ? face.far : face.near;
					const z = side ? -h : h;
					for (let j = 0; j < ARC; j++) {
						const th = mid - half + (j / (ARC - 1)) * half * 2;
						let p = onRing(th, r1, z);
						pts[j * 2] = p.x;
						pts[j * 2 + 1] = p.y;
						const zo = p.z;
						p = onRing(mid + half - (j / (ARC - 1)) * half * 2, r0, z);
						pts[ARC * 2 + j * 2] = p.x;
						pts[ARC * 2 + j * 2 + 1] = p.y;
						if (side) zBack += zo + p.z;
						else zFront += zo + p.z;
					}
				}
				// Whichever face is nearer the viewer is the lit one.
				if (zBack > zFront) {
					const t = face.near;
					face.near = face.far;
					face.far = t;
				}
				face.depth = (zFront + zBack) / (ARC * 4);
				face.lit = segmentLit(core, k);
				face.color = core.look.color;
			}
		});
		const order = faces.slice().sort((a, b) => a.depth - b.depth);
		const line = Math.max(1, 1.1 * sc);
		for (const face of order) {
			ctx.globalCompositeOperation = 'source-over';
			ctx.fillStyle = paint(PALETTE.face);
			poly(face.far);
			ctx.fill();
			poly(face.near);
			ctx.fill();
			ctx.globalCompositeOperation = 'lighter';
			ctx.lineWidth = line;
			ctx.strokeStyle = paint(face.color, 0.25 * face.lit);
			poly(face.far);
			ctx.stroke();
			ctx.strokeStyle = paint(face.color, Math.min(1, 0.85 * face.lit));
			poly(face.near);
			ctx.stroke();
			// The windings: short strokes across the lit face, outer arc to inner.
			ctx.strokeStyle = paint(face.color, 0.3 * face.lit);
			ctx.beginPath();
			for (let w = 1; w <= SEGMENT.windings; w++) {
				const u = w / (SEGMENT.windings + 1);
				along(face.near, 0, u * (ARC - 1));
				ctx.moveTo(pt.x, pt.y);
				// The inner arc runs the other way round.
				along(face.near, ARC, (1 - u) * (ARC - 1));
				ctx.lineTo(pt.x, pt.y);
			}
			ctx.stroke();
			// The LED: the outer arc's middle, a point of the spark colour.
			const m = Math.floor(ARC / 2) * 2;
			const size = 2.2 * sc;
			ctx.fillStyle = paint(PALETTE.spark, Math.min(1, 0.9 * face.lit));
			ctx.fillRect(face.near[m] - size / 2, face.near[m + 1] - size / 2, size, size);
		}
	}

	function flow(core: Core, sc: number) {
		ctx.globalCompositeOperation = 'lighter';
		const color = core.look.color;
		RINGS.forEach((R, ri) => {
			pose(core, ri);
			const r = R.r + segmentOf(ri).outer + FLOW.lift;
			// The orbit: a faint full circle the flow runs on.
			ctx.lineWidth = Math.max(1, 0.8 * sc);
			ctx.strokeStyle = paint(color, 0.12);
			ctx.beginPath();
			for (let j = 0; j <= 48; j++) {
				const p = onRing((j / 48) * Math.PI * 2, r, 0);
				if (j) ctx.lineTo(p.x, p.y);
				else ctx.moveTo(p.x, p.y);
			}
			ctx.stroke();
			const back = Math.sign(R.dir) * FLOW.trail;
			ctx.strokeStyle = paint(color, 0.35);
			ctx.beginPath();
			for (let i = 0; i < FLOW.dots; i++) {
				const th = flowAngle(core, ri, i);
				let p = onRing(th - back, r, 0);
				ctx.moveTo(p.x, p.y);
				p = onRing(th, r, 0);
				ctx.lineTo(p.x, p.y);
			}
			ctx.stroke();
			for (let i = 0; i < FLOW.dots; i++) {
				const p = onRing(flowAngle(core, ri, i), r, 0);
				const size = 1.8 * sc * p.s;
				ctx.fillStyle = paint(PALETTE.spark, 0.45 + 0.4 * Math.max(0, p.z));
				ctx.fillRect(p.x - size / 2, p.y - size / 2, size, size);
			}
			// The comet: a head and a fading tail, running against the flow.
			const head = cometAngle(core, ri);
			for (let j = 0; j < FLOW.comet; j++) {
				const p = onRing(head + j * 0.03 * Math.sign(R.dir), r, 0);
				const size = (2.6 - (1.6 * j) / FLOW.comet) * sc;
				ctx.fillStyle = paint(PALETTE.white, 0.9 * (1 - j / FLOW.comet));
				ctx.fillRect(p.x - size / 2, p.y - size / 2, size, size);
			}
			if (ri === 0) {
				// The struts: ring 0 held to the core, between its segments.
				ctx.strokeStyle = paint(color, 0.45);
				ctx.beginPath();
				const span = (Math.PI * 2) / R.seg;
				const r0 = R.r - segmentOf(ri).inner;
				for (let s = 0; s < 4; s++) {
					const th = core.spinA * R.dir + span / 2 + (s * Math.PI) / 2;
					let p = onRing(th, STRUT_IN, 0);
					ctx.moveTo(p.x, p.y);
					p = onRing(th, r0, 0);
					ctx.lineTo(p.x, p.y);
				}
				ctx.stroke();
			}
		});
	}

	function heart(core: Core, sc: number) {
		ctx.globalCompositeOperation = 'lighter';
		const color = core.look.color;
		const { glow, radius } = coreGlow(core);
		const r0 = radius * S;
		const g = Math.min(1, glow);
		const halo = ctx.createRadialGradient(cx, cy, 0, cx, cy, r0 * 4);
		halo.addColorStop(0, paint(PALETTE.white, g));
		halo.addColorStop(0.22, paint(PALETTE.halo, 0.55 * g));
		halo.addColorStop(0.5, paint(color, 0.2 * Math.min(1.5, glow)));
		halo.addColorStop(1, paint(color, 0));
		ctx.fillStyle = halo;
		ctx.fillRect(cx - r0 * 4, cy - r0 * 4, r0 * 8, r0 * 8);
		const disk = ctx.createRadialGradient(cx, cy, 0, cx, cy, r0);
		disk.addColorStop(0, paint(PALETTE.white, 1));
		disk.addColorStop(0.7, paint(PALETTE.spark, 0.8 * g));
		disk.addColorStop(1, paint(color, 0));
		ctx.fillStyle = disk;
		ctx.fillRect(cx - r0, cy - r0, r0 * 2, r0 * 2);
		// The wire sphere: three great circles round the core, turning.
		ctx.lineWidth = Math.max(1, 0.8 * sc);
		ctx.strokeStyle = paint(PALETTE.halo, 0.35);
		const rw = r0 * 1.35;
		for (let i = 0; i < 3; i++) {
			const turn = (i * Math.PI) / 3;
			ctx.beginPath();
			ctx.ellipse(cx, cy, rw, rw * Math.abs(Math.cos(core.spinA * 0.7 + turn)), turn, 0, Math.PI * 2);
			ctx.stroke();
		}
		for (const f of shocks(core, core.t)) {
			const ring = shockRing(f);
			ctx.lineWidth = Math.max(1, ring.width * sc);
			ctx.strokeStyle = paint(color, ring.alpha);
			ctx.beginPath();
			ctx.arc(cx, cy, ring.radius * S, 0, Math.PI * 2);
			ctx.stroke();
		}
	}

	return {
		kind: 'canvas2d',
		resize(width, height, ratio) {
			W = Math.max(1, width);
			H = Math.max(1, height);
			dpr = ratio;
			canvas.width = Math.round(W * dpr);
			canvas.height = Math.round(H * dpr);
		},
		draw(core, sh) {
			if (buf.length < sh.n * 4) {
				buf = new Float32Array(sh.n * 4);
				screen = new Float32Array(sh.n * 4);
			}
			cx = W / 2;
			cy = H / 2;
			S = Math.min(W, H) * SPREAD;
			const sc = W / BOARD_WIDTH;
			ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
			ctx.globalCompositeOperation = 'source-over';
			ctx.clearRect(0, 0, W, H);

			shellInto(core, sh, buf);
			for (let i = 0; i < sh.n; i++) {
				const p = project(buf[i * 4], buf[i * 4 + 1], buf[i * 4 + 2]);
				screen[i * 4] = p.x;
				screen[i * 4 + 1] = p.y;
				screen[i * 4 + 2] = p.z;
				// Depth-shaded: the far side faint and fine, as webgl.ts (T-0749).
				const depth = Math.min(1, Math.max(0, (p.z + 1.1) / 2.2));
				screen[i * 4 + 3] = buf[i * 4 + 3] * (0.06 + 0.94 * depth);
			}
			ctx.globalCompositeOperation = 'lighter';
			shell(core, sh, false, sc);
			rings(core, sc);
			ctx.globalCompositeOperation = 'lighter';
			shell(core, sh, true, sc);
			flow(core, sc);
			heart(core, sc);
		},
		dispose() {
			buf = new Float32Array(0);
			screen = new Float32Array(0);
		}
	};
}
