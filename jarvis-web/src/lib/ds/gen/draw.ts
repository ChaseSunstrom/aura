// The arithmetic behind the projections, out of the components so it is tested
// without a DOM. Nothing here knows a colour or a size: a chart is laid out in
// percent of its box and the components size the box from tokens.

/** The rows a virtual list draws: the ones in view and `overscan` either side. */
export function windowOf(scrollTop: number, viewport: number, rowH: number, count: number, overscan = 4) {
	const h = rowH > 0 ? rowH : 1;
	const first = Math.floor(Math.max(0, scrollTop) / h);
	return {
		start: Math.min(count, Math.max(0, first - overscan)),
		end: Math.min(count, first + Math.ceil(Math.max(0, viewport) / h) + overscan)
	};
}

/** A number as it is read: at most two decimals, no float noise (0.1 + 0.2 is 0.3). */
export const fmt = (n: number) => (Number.isInteger(n) ? String(n) : String(Number(n.toFixed(2))));

/** Seconds as a clock: 95 → 1:35, 3725 → 1:02:05. */
export function clock(seconds: number): string {
	const t = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
	const two = (n: number) => String(n).padStart(2, '0');
	const h = Math.floor(t / 3600);
	const m = Math.floor((t % 3600) / 60);
	return h ? `${h}:${two(m)}:${two(t % 60)}` : `${m}:${two(t % 60)}`;
}

const lowest = (xs: number[]) => xs.reduce((a, b) => (b < a ? b : a), Infinity);
const highest = (xs: number[]) => xs.reduce((a, b) => (b > a ? b : a), -Infinity);

/**
 * Line and area paths in a 100 × 100 box (y down), every series over the same
 * evenly spaced x. An area chart's range takes in 0 so the fill stands on it;
 * a flat series sits in the middle rather than on an edge.
 */
export function lineChart(series: { points: number[] }[], area = false) {
	const all = series.flatMap((s) => s.points);
	let lo = all.length ? lowest(all) : 0;
	let hi = all.length ? highest(all) : 1;
	if (area) {
		lo = Math.min(lo, 0);
		hi = Math.max(hi, 0);
	}
	if (hi === lo) {
		hi += 1;
		lo -= 1;
	}
	const n = highest(series.map((s) => s.points.length));
	const x = (i: number) => (n > 1 ? (i / (n - 1)) * 100 : 50).toFixed(2);
	const y = (v: number) => (100 - ((v - lo) / (hi - lo)) * 100).toFixed(2);
	const paths = series.map((s) => {
		const line = s.points.length ? `M${s.points.map((v, i) => `${x(i)},${y(v)}`).join('L')}` : '';
		const base = y(Math.min(Math.max(0, lo), hi));
		const fill =
			area && s.points.length > 1 ? `${line}L${x(s.points.length - 1)},${base}L${x(0)},${base}Z` : '';
		return { line, fill };
	});
	return { lo, hi, paths };
}

/**
 * Where [lat, lon] lands in a box of `aspect` (width / height), in percent.
 * Equirectangular, longitude shrunk by the cosine of the middle latitude so a
 * town keeps its shape; padded a tenth each side. One place, or a line along
 * one axis, still gets a box to sit in.
 *
 * ponytail: a route over the antimeridian is drawn the long way round; split it
 * at ±180 when a projection ever needs the Pacific.
 */
export function mapFit(coords: [number, number][], aspect: number) {
	if (!coords.length) return () => [50, 50] as [number, number];
	const lats = coords.map((c) => c[0]);
	const lons = coords.map((c) => c[1]);
	const k = Math.cos((((lowest(lats) + highest(lats)) / 2) * Math.PI) / 180);
	const x0 = lowest(lons) * k;
	const x1 = highest(lons) * k;
	const y0 = -highest(lats);
	const y1 = -lowest(lats);
	const SMALLEST = 0.005; // degrees: a street, about half a kilometre
	let w = Math.max(x1 - x0, SMALLEST);
	let h = Math.max(y1 - y0, SMALLEST);
	if (w / h < aspect) w = h * aspect;
	else h = w / aspect;
	w *= 1.25;
	h *= 1.25;
	const cx = (x0 + x1) / 2;
	const cy = (y0 + y1) / 2;
	return ([lat, lon]: [number, number]) =>
		[((lon * k - cx) / w + 0.5) * 100, ((-lat - cy) / h + 0.5) * 100] as [number, number];
}

export interface SceneShape {
	kind: 'line' | 'polygon' | 'circle' | 'text' | 'path';
	points?: number[][];
	r?: number;
	text?: string;
	d?: string;
	fill?: boolean;
	width?: number;
	tone?: string;
}

export interface Camera {
	yaw?: number;
	pitch?: number;
	distance?: number;
}

/** A scene shape in its box's coordinates, ready to draw. */
export type Laid = SceneShape & { at: [number, number][]; radius: number };

/**
 * A scene's shapes in its width × height box, drawn far to near. Without a
 * camera the points are box coordinates already. With one, each [x, y, z] is
 * turned by yaw then pitch, seen in perspective from `distance`, and the
 * whole is fitted to nine tenths of the box: the distance sets how strong the
 * perspective is, never whether the picture fits. Paths are 2D in box
 * coordinates either way, drawn first, behind the rest.
 */
export function sceneLayout(width: number, height: number, shapes: SceneShape[], camera?: Camera): Laid[] {
	if (!camera) {
		return shapes.map((s) => ({
			...s,
			at: (s.points ?? []).map((p) => [p[0], p[1]] as [number, number]),
			radius: s.r ?? 0
		}));
	}
	const rad = Math.PI / 180;
	const yaw = (camera.yaw ?? 30) * rad;
	const pitch = (camera.pitch ?? 20) * rad;
	const dist = camera.distance ?? 6;
	const seen = (p: number[]) => {
		const [x, y, z = 0] = p;
		const x1 = x * Math.cos(yaw) + z * Math.sin(yaw);
		const z1 = -x * Math.sin(yaw) + z * Math.cos(yaw);
		const y2 = y * Math.cos(pitch) - z1 * Math.sin(pitch);
		const z2 = y * Math.sin(pitch) + z1 * Math.cos(pitch);
		// A point at or behind the eye is held just in front of it, not flipped.
		const f = dist / Math.max(dist - z2, dist / 100);
		return { x: x1 * f, y: -y2 * f, z: z2, f };
	};
	const projected = shapes.map((s) => (s.kind === 'path' ? [] : (s.points ?? []).map(seen)));
	const flat = projected.flat();
	const xs = flat.map((p) => p.x);
	const ys = flat.map((p) => p.y);
	const bw = flat.length ? highest(xs) - lowest(xs) : 0;
	const bh = flat.length ? highest(ys) - lowest(ys) : 0;
	const fits = [bw > 0 ? (width * 0.9) / bw : Infinity, bh > 0 ? (height * 0.9) / bh : Infinity];
	const scale = Number.isFinite(Math.min(...fits)) ? Math.min(...fits) : 1;
	const cx = flat.length ? (highest(xs) + lowest(xs)) / 2 : 0;
	const cy = flat.length ? (highest(ys) + lowest(ys)) / 2 : 0;
	return shapes
		.map((s, i) => {
			const pts = projected[i];
			const depth = pts.length ? pts.reduce((a, p) => a + p.z, 0) / pts.length : -Infinity;
			const laid: Laid = {
				...s,
				at:
					s.kind === 'path'
						? []
						: pts.map((p) => [(p.x - cx) * scale + width / 2, (p.y - cy) * scale + height / 2]),
				radius: (s.r ?? 0) * scale * (pts[0]?.f ?? 1)
			};
			return { laid, depth };
		})
		.sort((a, b) => a.depth - b.depth)
		.map((e) => e.laid);
}

export type DiffKind = 'add' | 'del' | 'hunk' | 'file' | 'same';

/** A unified diff's lines, each with what it is. */
export function diffLines(patch: string): { text: string; kind: DiffKind }[] {
	return patch
		.replace(/\n$/, '')
		.split('\n')
		.map((text) => ({
			text,
			kind: text.startsWith('@@')
				? 'hunk'
				: text.startsWith('+++') || text.startsWith('---')
					? 'file'
					: text.startsWith('+')
						? 'add'
						: text.startsWith('-')
							? 'del'
							: 'same'
		}));
}

/** Up to two initials, by code point (an emoji or an accent is one letter). */
export const initials = (name: string) =>
	name
		.trim()
		.split(/\s+/)
		.slice(0, 2)
		.map((w) => [...w][0] ?? '')
		.join('')
		.toUpperCase();
