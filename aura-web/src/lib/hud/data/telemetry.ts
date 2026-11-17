// `panel.telemetry` from sensor readings, the sky summary and the GPU rows a
// `hud.panel.show telemetry` carries.
//
// A reading is a figure with its unit; a percentage also gets a meter, and a
// chart series its points as a trend line under the row. A chart stays a
// telemetry panel rather than a body of its own: the scene the frame-time and
// contrast gates were recorded against draws its chart and table this way.

import type { Reading, ReadingsPayload, SkySummary } from '$lib/dashboards/widgets';
import type { TelemetryReading } from '$lib/glass';

function num(value: unknown): number | null {
	const n = typeof value === 'number' ? value : Number(value);
	return Number.isFinite(n) ? Math.round(n * 10) / 10 : null;
}

/** Whether the name already contains the area, however either is capitalised. */
function says(name: string, area: string): boolean {
	return name.toLowerCase().includes(area.toLowerCase());
}

export function fromReading(reading: Reading): TelemetryReading {
	const value = reading.available ? num(reading.value) : null;
	const row: TelemetryReading = {
		id: reading.entity_id,
		// The area only when the name does not already carry it: Home Assistant
		// names a sensor after the room it is in, so this said "Garage Humidity
		// (Garage)" on most rows in the house.
		label: reading.area && !says(reading.name, reading.area) ? `${reading.name} (${reading.area})` : reading.name,
		value,
		unit: reading.unit || undefined
	};
	if (reading.unit === '%' && value !== null) row.percent = value;
	// A binary sensor's on/off or a text sensor's word is a state, not a missing figure.
	const word = typeof reading.value === 'string' ? reading.value.trim() : '';
	if (reading.available && value === null && word) row.text = word[0].toUpperCase() + word.slice(1).replace(/_/g, ' ');
	return row;
}

export function readingsView(payload: ReadingsPayload | null | undefined): TelemetryReading[] {
	return (payload?.readings ?? []).map(fromReading);
}

/** The moon and the next pass as readings: a phase is a percentage, an altitude a figure. */
export function skyView(sky: SkySummary | null | undefined): TelemetryReading[] {
	if (!sky?.configured) return [];
	const rows: TelemetryReading[] = [];
	if (sky.moon) {
		const lit = num(sky.moon.illumination);
		rows.push({
			id: 'moon',
			label: `Moon, ${sky.moon.state.replace(/_/g, ' ') || 'phase unknown'}`,
			value: lit,
			unit: lit === null ? undefined : '%',
			percent: lit
		});
	}
	if (sky.pass) {
		rows.push({
			id: 'pass',
			label: `${sky.satellite || 'Next pass'}${sky.pass.direction ? `, ${sky.pass.direction}` : ''}`,
			value: sky.pass.max_alt,
			// The degree sign sits on its figure ("13°"), not a space away as a unit would.
			text: sky.pass.max_alt === null ? undefined : `${sky.pass.max_alt}°`,
			unit: sky.pass.max_alt === null ? undefined : 'up'
		});
	}
	return rows;
}

/** The GPU rows of a `telemetry` content panel, one reading per figure. */
export function gpuReadings(payload: Record<string, unknown>): TelemetryReading[] {
	const gpus = Array.isArray(payload.gpus) ? payload.gpus : [];
	const rows: TelemetryReading[] = [];
	gpus.forEach((raw, index) => {
		const gpu = (raw ?? {}) as Record<string, unknown>;
		const name = String(gpu.gpu ?? gpu.name ?? index);
		const util = num(gpu.utilization);
		const vram = num(gpu.vram_pct);
		const temp = num(gpu.temperature);
		const power = num(gpu.power);
		if (util !== null) rows.push({ id: `gpu${name}-util`, label: `GPU ${name} busy`, value: util, unit: '%', percent: util });
		if (vram !== null) rows.push({ id: `gpu${name}-vram`, label: `GPU ${name} memory`, value: vram, unit: '%', percent: vram });
		if (temp !== null) rows.push({ id: `gpu${name}-temp`, label: `GPU ${name} temperature`, value: temp, unit: '°C' });
		if (power !== null) rows.push({ id: `gpu${name}-power`, label: `GPU ${name} power`, value: power, unit: 'W' });
	});
	return rows;
}

/** A `table` content panel as readings: the first column labels, the last is the figure. */
export function tableReadings(payload: Record<string, unknown>): TelemetryReading[] {
	const columns = Array.isArray(payload.columns) ? payload.columns.map(String) : [];
	const rows = Array.isArray(payload.rows) ? payload.rows : [];
	// "1,234 W" is a figure, and a cell with no leading figure ("Online", "$5") is shown as
	// written: both used to read as a dash with the whole cell for its unit (T-0455). A comma
	// decimal ("1,5 kW") is shown as written too, rather than as 1 of ",5 kW".
	const unitOf = (cell: string): { value: number | null; unit?: string; text?: string } => {
		const m = /^(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?![\d,.])\s*(.*)$/.exec(cell.trim());
		if (!m) return { value: null, text: cell.trim() || undefined };
		return { value: num(m[1].replace(/,/g, '')), unit: m[2] || undefined };
	};
	return rows.map((raw, index) => {
		const cells = Array.isArray(raw) ? raw.map(String) : [];
		const label = cells.length > 1 ? `${columns[0] ? `${columns[0]} ` : ''}${cells[0]}` : `Row ${index + 1}`;
		const last = cells.length ? cells[cells.length - 1] : '';
		const { value, unit, text } = unitOf(last);
		const reading: TelemetryReading = { id: `row-${index}`, label, value, unit, text };
		if (unit === '%' && value !== null) reading.percent = value;
		// The middle columns used to be dropped: a GPU table lost its VRAM.
		const middle = cells
			.slice(1, -1)
			.map((cell, i) => (cell.trim() ? `${columns[i + 1] ? `${columns[i + 1]} ` : ''}${cell.trim()}` : ''))
			.filter(Boolean);
		if (middle.length) reading.sub = middle.join(' · ');
		return reading;
	});
}

/** The points a row's trend line keeps: more than a row's width can show apart. */
const TREND_POINTS = 120;

/**
 * Every n-th point of a long series, first and last always kept, so "my power this week"
 * at five-minute samples still spans the week (taking the newest 120 drew ten hours).
 * Does not keep a spike that falls between two samples.
 */
function sample(ys: number[], n = TREND_POINTS): number[] {
	if (ys.length <= n) return ys;
	return Array.from({ length: n }, (_, i) => ys[Math.round((i * (ys.length - 1)) / (n - 1))]);
}

/** A `chart` content panel, one reading per series: its last point the figure, its points the trend. */
export function chartReadings(payload: Record<string, unknown>): TelemetryReading[] {
	const series = Array.isArray(payload.series) ? payload.series : [];
	const unit = typeof payload.unit === 'string' && payload.unit ? payload.unit : undefined;
	return series.map((raw, index) => {
		const s = (raw ?? {}) as Record<string, unknown>;
		const points = Array.isArray(s.points) ? s.points : [];
		// A point is a bare figure or [x, y]; only a number or a figure in a string counts,
		// since num(null) and num('') are 0 and would draw a drop to zero. Points are spaced
		// by index in the order sent (oldest first): x is not read, so a gap is not drawn.
		const ys = points
			.map((p) => (Array.isArray(p) ? p[p.length - 1] : p))
			.filter((y) => typeof y === 'number' || (typeof y === 'string' && y.trim() !== ''))
			.map(num)
			.filter((y): y is number => y !== null);
		const value = ys.length ? ys[ys.length - 1] : null;
		return { id: `series-${index}`, label: String(s.name ?? `Series ${index + 1}`), value, unit, points: sample(ys) };
	});
}
