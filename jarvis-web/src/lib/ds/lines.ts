// Geometry for the lines on the stage: leader lines and connectors.
//
// Both are drawn in a 0–100 box stretched over the stage (`viewBox="0 0 100
// 100"`, `preserveAspectRatio="none"`), so a point is "percent across, percent
// down" and the host never has to know the stage's pixel size. Stretching is
// affine, so a point sampled here sits on the drawn line at any aspect ratio.

export type Point = readonly [number, number];

/** The points that can be drawn: finite, clamped to the box. */
export function cleanPoints(points: readonly Point[]): Point[] {
	return points
		.filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))
		.map(([x, y]) => [clamp(x), clamp(y)] as const);
}

const clamp = (v: number): number => Math.min(100, Math.max(0, v));

export interface Curve {
	/** The SVG path. */
	d: string;
	/** The point `t` (0..1) of the way along. */
	at: (t: number) => Point;
}

/**
 * A smooth S from `a` to `b`: it leaves `a` and arrives at `b` along the
 * longer axis, so a connector to the side bends sideways and one below bends
 * down.
 */
export function connectorCurve(a: Point, b: Point): Curve {
	const [x0, y0] = a;
	const [x1, y1] = b;
	const sideways = Math.abs(x1 - x0) >= Math.abs(y1 - y0);
	const mx = (x0 + x1) / 2;
	const my = (y0 + y1) / 2;
	const c1: Point = sideways ? [mx, y0] : [x0, my];
	const c2: Point = sideways ? [mx, y1] : [x1, my];
	const at = (t: number): Point => {
		const u = Math.min(1, Math.max(0, t));
		const v = 1 - u;
		const w0 = v * v * v;
		const w1 = 3 * v * v * u;
		const w2 = 3 * v * u * u;
		const w3 = u * u * u;
		return [
			w0 * x0 + w1 * c1[0] + w2 * c2[0] + w3 * x1,
			w0 * y0 + w1 * c1[1] + w2 * c2[1] + w3 * y1
		];
	};
	return { d: `M${x0} ${y0} C${c1[0]} ${c1[1]} ${c2[0]} ${c2[1]} ${x1} ${y1}`, at };
}

/** How far the core's light reaches from its centre, in widths of its scaled box (Stage's `.card` comment: 0.3 at the alert scale; the card's `--clear` is 0.32). */
export const CORE_LIGHT = 0.3;

export interface Box {
	left: number;
	top: number;
	width: number;
	height: number;
}

/** A client rect in the stage's own coordinates (`root` is the stage's client rect). */
export function relativeTo(r: Box, root: { left: number; top: number }): Box {
	return { left: r.left - root.left, top: r.top - root.top, width: r.width, height: r.height };
}

/**
 * A box with any part inside a stage `w` x `h` and the point its line ends on inside it too;
 * a line to a target outside the stage points at nothing, and one ending in the margin is cut.
 */
export function onStage(b: Box, w: number, h: number, edge: 'left' | 'right' | 'top'): boolean {
	if (!(b.width > 0 && b.height > 0 && b.left < w && b.left + b.width > 0 && b.top < h && b.top + b.height > 0)) return false;
	const [x, y] = edgePoint(b, edge);
	return x >= 0 && x <= w && y >= 0 && y <= h;
}

/** The point of the box a line lands on, in the box's own units. */
export function edgePoint(b: Box, edge: 'left' | 'right' | 'top'): Point {
	return edge === 'right' ? [b.left + b.width, b.top + b.height / 2] : edge === 'left' ? [b.left, b.top + b.height / 2] : [b.left + b.width / 2, b.top];
}
