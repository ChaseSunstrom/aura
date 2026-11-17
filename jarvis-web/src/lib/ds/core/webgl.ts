// The core on the GPU (three.js): the same model canvas2d.ts draws, with a real
// depth buffer, so a ring's dark faces hide the shell and the ring behind them
// as solid things do.
//
// One world unit is the shell's radius and lands on `SPREAD × min(w, h)` pixels
// (the camera's field of view is set from the size), so the picture matches
// the Canvas2D port point for point. Colours go through untouched (linear
// output, raw token values in), so a token means the same thing in both.
//
// Loaded on demand by Core.svelte (`import('./webgl')`): three stays out of any
// page that never shows the core.
//
// Does not cover: restoring a lost context. Core.svelte falls back to Canvas2D
// on `webglcontextlost` and stays there until it is mounted again.

import {
	AdditiveBlending,
	BufferAttribute,
	BufferGeometry,
	ExtrudeGeometry,
	Group,
	InstancedMesh,
	LineBasicMaterial,
	LineLoop,
	LineSegments,
	LinearSRGBColorSpace,
	Matrix4,
	Mesh,
	MeshBasicMaterial,
	PerspectiveCamera,
	PlaneGeometry,
	Points,
	PointsMaterial,
	Scene,
	Shape,
	ShaderMaterial,
	Vector3,
	WebGLRenderer,
	type Material,
	type Object3D
} from 'three';

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
	ringPose,
	segmentLit,
	shellInto,
	shocks,
	type Core,
	type Renderer,
	type Rgb,
	type Shell
} from './engine';

const v3 = (c: Rgb) => new Vector3(c[0] / 255, c[1] / 255, c[2] / 255);
/** Points along each arc of a segment's edges. */
const ARC = 6;
/** Lines sit this far off the faces they trace, so the faces do not z-fight them. */
const LIFT = 0.002;
/** The planes the core's glow and the shock rings are drawn on, as half-widths (core radii). */
const GLOW_HALF = 0.8;
const SHOCK_HALF = 1.45;

const SHELL_VERTEX = /* glsl */ `
	attribute float aAlpha;
	attribute float aHot;
	uniform float uSize;
	uniform float uCam;
	varying float vAlpha;
	varying float vHot;
	void main() {
		vec4 mv = modelViewMatrix * vec4(position, 1.0);
		gl_Position = projectionMatrix * mv;
		// mv.z is -uCam at the centre: 0 on the far side of the shell, 1 on the near.
		// The far side faint and fine, as the reference: at a quarter as bright and nearly
		// as big, the two halves merged into one speckle (T-0749).
		float depth = clamp((mv.z + uCam + 1.1) / 2.2, 0.0, 1.0);
		vAlpha = aAlpha * (0.06 + 0.94 * depth);
		vHot = aHot;
		gl_PointSize = uSize * (0.4 + 1.5 * vAlpha) * (1.0 + 0.7 * aHot) * (uCam / -mv.z);
	}
`;
const SHELL_FRAGMENT = /* glsl */ `
	uniform vec3 uColor;
	uniform vec3 uHot;
	varying float vAlpha;
	varying float vHot;
	void main() {
		float d = length(gl_PointCoord - 0.5);
		if (d > 0.5) discard;
		gl_FragColor = vec4(mix(uColor, uHot, vHot), vAlpha * smoothstep(0.5, 0.15, d) * mix(0.9, 1.0, vHot));
	}
`;
const PLANE_VERTEX = /* glsl */ `
	varying vec2 vUv;
	void main() {
		vUv = uv;
		gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
	}
`;
// The core's glow: the same gradient stops as canvas2d.ts's halo and disk.
const GLOW_FRAGMENT = /* glsl */ `
	uniform vec3 uColor;
	uniform vec3 uWhite;
	uniform vec3 uHalo;
	uniform vec3 uSpark;
	uniform float uGlow;
	uniform float uRadius;
	uniform float uHalf;
	varying vec2 vUv;
	vec4 halo(float h, float g) {
		vec4 s0 = vec4(uWhite, g);
		vec4 s1 = vec4(uHalo, 0.55 * g);
		vec4 s2 = vec4(uColor, 0.2 * min(1.5, uGlow));
		if (h < 0.22) return mix(s0, s1, h / 0.22);
		if (h < 0.5) return mix(s1, s2, (h - 0.22) / 0.28);
		return mix(s2, vec4(uColor, 0.0), min(1.0, (h - 0.5) / 0.5));
	}
	vec4 disk(float h, float g) {
		if (h >= 1.0) return vec4(0.0);
		if (h < 0.7) return mix(vec4(uWhite, 1.0), vec4(uSpark, 0.8 * g), h / 0.7);
		return mix(vec4(uSpark, 0.8 * g), vec4(uColor, 0.0), (h - 0.7) / 0.3);
	}
	void main() {
		float d = length(vUv - 0.5) * 2.0 * uHalf;
		float g = min(1.0, uGlow);
		vec4 a = halo(d / (uRadius * 4.0), g);
		vec4 b = disk(d / uRadius, g);
		// Light, not paint: alpha 1 here made the whole plane an opaque black
		// square over the page (additive blending adds alpha too). The alpha is
		// the light's own strength and the colour is divided by it, so
		// SRC_ALPHA · rgb is still exactly the light.
		vec3 c = a.rgb * a.a + b.rgb * b.a;
		float k = min(1.0, max(max(c.r, c.g), c.b));
		if (k < 0.002) discard;
		gl_FragColor = vec4(c / k, k);
	}
`;
// The shock rings: up to four at once, each its progress f (or -1), as
// engine.shockRing has them: radius .2 + 1.1f, width 6 - 5f board px, alpha .8(1 - f).
const SHOCK_FRAGMENT = /* glsl */ `
	uniform vec3 uColor;
	uniform vec4 uF;
	uniform float uUnit;
	uniform float uPx;
	uniform float uHalf;
	varying vec2 vUv;
	float ring(float f, float d) {
		if (f < 0.0 || f >= 1.0) return 0.0;
		float w = max((6.0 - 5.0 * f) * uUnit, uPx) * 0.5;
		return 0.8 * (1.0 - f) * (1.0 - smoothstep(w, w + uPx, abs(d - (0.2 + 1.1 * f))));
	}
	void main() {
		float d = length(vUv - 0.5) * 2.0 * uHalf;
		float a = ring(uF.x, d) + ring(uF.y, d) + ring(uF.z, d) + ring(uF.w, d);
		gl_FragColor = vec4(uColor, min(1.0, a));
	}
`;

const additive = { blending: AdditiveBlending, transparent: true, depthWrite: false } as const;

/** A geometry of line segments from a flat list of [x, y, z] pairs' ends. */
function lines(points: number[]): BufferGeometry {
	const g = new BufferGeometry();
	g.setAttribute('position', new BufferAttribute(new Float32Array(points), 3));
	return g;
}

const circle = (r: number, steps = 64) =>
	lines(Array.from({ length: steps }, (_, j) => {
		const th = (j / steps) * Math.PI * 2;
		return [Math.cos(th) * r, Math.sin(th) * r, 0];
	}).flat());

/** One ring: its turned frame, the spinning segments, the flow, the comet. */
interface RingParts {
	frame: Group;
	spin: Group;
	flow: Group;
	comet: Group;
	edges: BufferGeometry;
	segOf: Uint8Array;
	weight: Float32Array;
	tint: LineBasicMaterial[];
}

/** Points drawn at a size in board pixels, rescaled with the canvas. */
type Sized = [PointsMaterial, number][];

function dot(sized: Sized, size: number, vertexColors = false): PointsMaterial {
	const mat = new PointsMaterial({ sizeAttenuation: false, size, vertexColors, ...additive });
	sized.push([mat, size]);
	return mat;
}

function buildRing(ri: number, faceMat: MeshBasicMaterial, sized: Sized): RingParts {
	const R = RINGS[ri];
	const span = (Math.PI * 2) / R.seg;
	const S = segmentOf(ri);
	const half = (span - S.gap) / 2;
	const r0 = R.r - S.inner;
	const r1 = R.r + S.outer;
	const h = S.depth / 2;
	const frame = new Group();
	frame.rotation.order = 'YXZ';
	const spin = new Group();
	frame.add(spin);

	// The segments: one solid block, instanced round the ring.
	const shape = new Shape();
	shape.absarc(0, 0, r1, -half, half, false);
	shape.absarc(0, 0, r0, half, -half, true);
	const block = new ExtrudeGeometry(shape, { depth: h * 2, bevelEnabled: false, curveSegments: ARC });
	block.translate(0, 0, -h);
	const blocks = new InstancedMesh(block, faceMat, R.seg);
	const m = new Matrix4();
	for (let k = 0; k < R.seg; k++) blocks.setMatrixAt(k, m.makeRotationZ(k * span));
	spin.add(blocks);

	// The lit edges, every segment's in one geometry: both faces' outlines, the
	// windings across them and the corners joining them. A colour per vertex,
	// so each segment lights on its own (the thinking and music waves).
	const pts: number[] = [];
	const segs: number[] = [];
	const weights: number[] = [];
	const at = (th: number, r: number, z: number) => [Math.cos(th) * r, Math.sin(th) * r, z];
	const seg = (k: number, w: number, a: number[], b: number[]) => {
		pts.push(...a, ...b);
		segs.push(k, k);
		weights.push(w, w);
	};
	for (let k = 0; k < R.seg; k++) {
		const mid = k * span;
		const ro = r1 + LIFT;
		const rn = r0 - LIFT;
		for (const z of [h + LIFT, -h - LIFT]) {
			for (let j = 0; j < ARC; j++) {
				const t0 = mid - half + (j / ARC) * half * 2;
				const t1 = mid - half + ((j + 1) / ARC) * half * 2;
				seg(k, 0.85, at(t0, ro, z), at(t1, ro, z));
				seg(k, 0.85, at(t0, rn, z), at(t1, rn, z));
			}
			seg(k, 0.85, at(mid - half, ro, z), at(mid - half, rn, z));
			seg(k, 0.85, at(mid + half, ro, z), at(mid + half, rn, z));
			for (let w = 1; w <= SEGMENT.windings; w++) {
				const th = mid - half + (w / (SEGMENT.windings + 1)) * half * 2;
				seg(k, 0.3, at(th, ro, z), at(th, rn, z));
			}
		}
		for (const th of [mid - half, mid + half])
			for (const r of [r1 + LIFT, r0 - LIFT]) seg(k, 0.5, at(th, r, h + LIFT), at(th, r, -h - LIFT));
	}
	const edges = lines(pts);
	edges.setAttribute('color', new BufferAttribute(new Float32Array(pts.length), 3));
	spin.add(new LineSegments(edges, new LineBasicMaterial({ vertexColors: true, ...additive })));

	// The LEDs: a spark on each segment's outer rim.
	const leds = dot(sized, 2.2);
	leds.color.setRGB(...(PALETTE.spark.map((v) => (v / 255) * 0.9) as Rgb));
	spin.add(new Points(lines(Array.from({ length: R.seg }, (_, k) => at(k * span, r1 + 0.006, 0)).flat()), leds));

	const tint: LineBasicMaterial[] = [];
	const tinted = (opacity: number) => {
		const mat = new LineBasicMaterial({ ...additive, opacity });
		tint.push(mat);
		return mat;
	};
	if (ri === 0) {
		// The struts: ring 0 held to the core, between its segments.
		const struts = [0, 1, 2, 3].flatMap((s) => {
			const th = span / 2 + (s * Math.PI) / 2;
			return [...at(th, STRUT_IN, 0), ...at(th, r0, 0)];
		});
		spin.add(new LineSegments(lines(struts), tinted(0.45)));
	}

	// The flow: dots running round just outside the ring, each with a short trail.
	const r = R.r + segmentOf(ri).outer + FLOW.lift;
	frame.add(new LineLoop(circle(r), tinted(0.12)));
	const flow = new Group();
	const back = Math.sign(R.dir) * FLOW.trail;
	const dots: number[] = [];
	const trails: number[] = [];
	for (let i = 0; i < FLOW.dots; i++) {
		const th = (i / FLOW.dots) * Math.PI * 2;
		dots.push(...at(th, r, 0));
		trails.push(...at(th - back, r, 0), ...at(th, r, 0));
	}
	flow.add(new LineSegments(lines(trails), tinted(0.35)));
	flow.add(new Points(lines(dots), dot(sized, 1.8)));
	frame.add(flow);

	// The comet: a head and a fading tail, running against the flow.
	const comet = new Group();
	const tail: number[] = [];
	const fade: number[] = [];
	for (let j = 0; j < FLOW.comet; j++) {
		tail.push(...at(j * 0.03 * Math.sign(R.dir), r, 0));
		const a = 0.9 * (1 - j / FLOW.comet);
		fade.push(...PALETTE.white.map((c) => (c / 255) * a));
	}
	const cometGeo = lines(tail);
	cometGeo.setAttribute('color', new BufferAttribute(new Float32Array(fade), 3));
	comet.add(new Points(cometGeo, dot(sized, 2.2, true)));
	frame.add(comet);

	return {
		frame,
		spin,
		flow,
		comet,
		edges,
		segOf: Uint8Array.from(segs),
		weight: Float32Array.from(weights),
		tint
	};
}

/**
 * The GPU renderer, or null when the page has no WebGL.
 *
 * @public Reached only through Core.svelte's `import('./webgl')`, whose `.then`
 * destructuring knip does not follow.
 */
export function createWebgl(canvas: HTMLCanvasElement, capacity: number, onlost: () => void): Renderer | null {
	let gl: WebGLRenderer;
	try {
		gl = new WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
	} catch {
		// No WebGL2 here (or none at all): the caller draws in 2D instead.
		return null;
	}
	gl.outputColorSpace = LinearSRGBColorSpace;
	gl.setClearColor(0, 0);
	const lost = () => onlost();
	canvas.addEventListener('webglcontextlost', lost);

	const scene = new Scene();
	const camera = new PerspectiveCamera(50, 1, 0.1, 20);
	camera.position.z = CAMERA;
	const tilt = new Group();
	tilt.rotation.x = TILT;
	scene.add(tilt);

	// The shell: a point each, moved every frame from the model.
	const n = Math.max(0, Math.floor(capacity));
	const buf = new Float32Array(n * 4);
	const shellGeo = new BufferGeometry();
	const position = new BufferAttribute(new Float32Array(n * 3), 3);
	const alpha = new BufferAttribute(new Float32Array(n), 1);
	const hot = new BufferAttribute(new Float32Array(n), 1);
	shellGeo.setAttribute('position', position);
	shellGeo.setAttribute('aAlpha', alpha);
	shellGeo.setAttribute('aHot', hot);
	const shellMat = new ShaderMaterial({
		vertexShader: SHELL_VERTEX,
		fragmentShader: SHELL_FRAGMENT,
		uniforms: {
			uSize: { value: 1 },
			uCam: { value: CAMERA },
			uColor: { value: new Vector3() },
			uHot: { value: v3(PALETTE.hot) }
		},
		...additive
	});
	const points = new Points(shellGeo, shellMat);
	// The positions move every frame; a stale bounding sphere must not cull them.
	points.frustumCulled = false;
	tilt.add(points);
	let lastShell: Shell | null = null;

	// The wire sphere round the core: three great circles, turning.
	const wireMat = new LineBasicMaterial({ ...additive, opacity: 0.35 });
	wireMat.color.setRGB(...(PALETTE.halo.map((c) => c / 255) as Rgb));
	const wire = new Group();
	const unitCircle = circle(1, 48);
	for (let i = 0; i < 3; i++) {
		const loop = new LineLoop(unitCircle, wireMat);
		loop.rotation.set(i === 1 ? Math.PI / 2 : 0, i === 2 ? Math.PI / 2 : 0, 0);
		wire.add(loop);
	}
	tilt.add(wire);

	const faceMat = new MeshBasicMaterial();
	faceMat.color.setRGB(...(PALETTE.face.map((c) => c / 255) as Rgb));
	const sized: Sized = [];
	const rings = RINGS.map((_, ri) => buildRing(ri, faceMat, sized));
	for (const r of rings) tilt.add(r.frame);

	// The core's glow and the shock rings: flat on the screen, as in the reference.
	const glowMat = new ShaderMaterial({
		vertexShader: PLANE_VERTEX,
		fragmentShader: GLOW_FRAGMENT,
		uniforms: {
			uColor: { value: new Vector3() },
			uWhite: { value: v3(PALETTE.white) },
			uHalo: { value: v3(PALETTE.halo) },
			uSpark: { value: v3(PALETTE.spark) },
			uGlow: { value: 1 },
			uRadius: { value: 0.15 },
			uHalf: { value: GLOW_HALF }
		},
		...additive
	});
	scene.add(new Mesh(new PlaneGeometry(GLOW_HALF * 2, GLOW_HALF * 2), glowMat));
	const shockMat = new ShaderMaterial({
		vertexShader: PLANE_VERTEX,
		fragmentShader: SHOCK_FRAGMENT,
		uniforms: {
			uColor: { value: new Vector3() },
			uF: { value: [-1, -1, -1, -1] },
			uUnit: { value: 0.01 },
			uPx: { value: 0.01 },
			uHalf: { value: SHOCK_HALF }
		},
		...additive,
		depthTest: false
	});
	const shock = new Mesh(new PlaneGeometry(SHOCK_HALF * 2, SHOCK_HALF * 2), shockMat);
	shock.renderOrder = 1;
	scene.add(shock);

	const lit = new Float32Array(Math.max(...RINGS.map((R) => R.seg)));

	return {
		kind: 'webgl',
		resize(w, h, dpr) {
			const width = Math.max(1, w);
			const height = Math.max(1, h);
			gl.setPixelRatio(dpr);
			gl.setSize(width, height, false);
			const S = Math.min(width, height) * SPREAD;
			const sc = width / BOARD_WIDTH;
			camera.aspect = width / height;
			// One world unit at the centre is S pixels: the view's half-height is H/2S units at CAMERA.
			camera.fov = (2 * Math.atan(height / (2 * S * CAMERA)) * 180) / Math.PI;
			camera.updateProjectionMatrix();
			shellMat.uniforms.uSize.value = 1.5 * sc * dpr;
			shockMat.uniforms.uUnit.value = sc / S;
			shockMat.uniforms.uPx.value = 1 / S;
			for (const [mat, size] of sized) mat.size = size * sc;
		},
		draw(core: Core, sh: Shell) {
			const count = Math.min(n, sh.n);
			if (sh !== lastShell) {
				lastShell = sh;
				for (let i = 0; i < count; i++) hot.array[i] = sh.hot[i];
				hot.needsUpdate = true;
				shellGeo.setDrawRange(0, count);
			}
			shellInto(core, sh, buf);
			const pos = position.array;
			const al = alpha.array;
			for (let i = 0; i < count; i++) {
				pos[i * 3] = buf[i * 4];
				pos[i * 3 + 1] = buf[i * 4 + 1];
				pos[i * 3 + 2] = buf[i * 4 + 2];
				al[i] = buf[i * 4 + 3];
			}
			position.needsUpdate = true;
			alpha.needsUpdate = true;

			const [cr, cg, cb] = core.look.color.map((c) => c / 255);
			shellMat.uniforms.uColor.value.set(cr, cg, cb);
			rings.forEach((part, ri) => {
				const R = RINGS[ri];
				const p = ringPose(core, ri);
				part.frame.rotation.x = p.a;
				part.frame.rotation.y = p.b;
				part.frame.scale.setScalar(p.pulse);
				part.spin.rotation.z = core.spinA * R.dir;
				part.flow.rotation.z = core.flowA * R.dir * 0.8 + ri;
				part.comet.rotation.z = cometAngle(core, ri);
				for (let k = 0; k < R.seg; k++) lit[k] = segmentLit(core, k);
				const color = part.edges.getAttribute('color') as BufferAttribute;
				const c = color.array;
				for (let v = 0; v < part.weight.length; v++) {
					const w = Math.min(1, part.weight[v] * lit[part.segOf[v]]);
					c[v * 3] = cr * w;
					c[v * 3 + 1] = cg * w;
					c[v * 3 + 2] = cb * w;
				}
				color.needsUpdate = true;
				for (const mat of part.tint) mat.color.setRGB(cr, cg, cb);
			});

			const { glow, radius } = coreGlow(core);
			wire.scale.setScalar(radius * 1.35);
			wire.rotation.y = core.spinA * 0.7;
			glowMat.uniforms.uColor.value.set(cr, cg, cb);
			glowMat.uniforms.uGlow.value = glow;
			glowMat.uniforms.uRadius.value = radius;
			const f = shocks(core, core.t);
			const uF = shockMat.uniforms.uF.value as number[];
			for (let i = 0; i < 4; i++) uF[i] = f[i] ?? -1;
			shockMat.uniforms.uColor.value.set(cr, cg, cb);
			gl.render(scene, camera);
		},
		dispose() {
			canvas.removeEventListener('webglcontextlost', lost);
			const materials = new Set<Material>();
			scene.traverse((o: Object3D) => {
				const mesh = o as Mesh;
				mesh.geometry?.dispose();
				const mat = mesh.material;
				if (Array.isArray(mat)) mat.forEach((m) => materials.add(m));
				else if (mat) materials.add(mat);
				if (o instanceof InstancedMesh) o.dispose();
			});
			for (const m of materials) m.dispose();
			gl.dispose();
			// Give the context back now: a browser holds only a handful, and a
			// remount (or the gallery's nine cores) would otherwise run out.
			gl.forceContextLoss();
		}
	};
}
