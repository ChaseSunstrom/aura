/**
 * The phone's daily power ledger (T-0582) as the Devices screen shows it: today beside yesterday,
 * a row a feature (wake word, location, link, car). The phone keeps the ledger and answers it from
 * `control_device power_report` under `ledger`; its shape is pinned by `tests/contracts/power-ledger.json`,
 * which the phone's own tests read too.
 *
 * "Per feature" is time on plus a share of the battery drop while it was on, not attribution: two
 * features on together split the drop, so a row says what the phone cost while the feature ran.
 */
import type { CompanionDevice } from '$lib/jarvisClient';

const FEATURE_LABELS: Record<string, string> = { wake: 'Wake word', location: 'Location', link: 'Link', car: 'Car' };

export interface PowerRow {
	key: string;
	label: string;
	/** Time on, or `off` when the feature never ran that day. */
	on: string;
	/** Estimated share of the unplugged battery drop, empty when the feature was off. */
	drop: string;
	/** Hottest reading taken while it was on, empty when there was none. */
	peak: string;
}

export type PowerColumn =
	| {
			status: 'samples';
			label: string;
			date: string;
			/** How long the readings cover. */
			covered: string;
			rows: PowerRow[];
			total: { cpu: string; drop: string; peak: string };
	  }
	| { status: 'none'; label: string; date: string; said: string };

export type PowerLoad =
	| { status: 'loading' }
	| { status: 'ready'; phone: string; columns: PowerColumn[] }
	| { status: 'empty' }
	| { status: 'error'; detail: string }
	| { status: 'offline' };

type Call = (name: string, args: Record<string, unknown>) => Promise<unknown>;
type Obj = Record<string, unknown>;

const obj = (v: unknown): Obj | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Obj) : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** `1 h 30 min`, `5 min 40 s`: seconds only while the figure is under an hour. */
function span(seconds: number): string {
	const s = Math.round(seconds);
	if (s < 60) return `${s} s`;
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	const rest = s % 60;
	return [h ? `${h} h` : '', m ? `${m} min` : '', !h && rest ? `${rest} s` : ''].filter(Boolean).join(' ');
}
const percent = (v: number): string => `${+v.toFixed(1)}%`;
const degrees = (v: number): string => `${+v.toFixed(1)} °C`;

function column(label: string, word: string, date: unknown, days: Obj[]): PowerColumn {
	const when = typeof date === 'string' ? date : '';
	const none = (): PowerColumn => ({
		status: 'none',
		label,
		date: when,
		said: label === 'Today' ? 'No readings have been taken today yet.' : `No readings were taken ${word}.`
	});
	const day = days.find((d) => d.date === when);
	const features = obj(day?.features);
	const covered = num(day?.covered_min);
	// A day with one reading and no interval yet has covered nothing: not a day of zeros.
	if (!day || !features || !covered || covered <= 0) return none();
	const rows = Object.keys(FEATURE_LABELS).map((key): PowerRow => {
		const f = obj(features[key]);
		const seconds = num(f?.seconds) ?? 0;
		const drop = num(f?.drop_pct);
		const peak = num(f?.peak_temp_c);
		return {
			key,
			label: FEATURE_LABELS[key],
			on: seconds > 0 ? span(seconds) : 'off',
			drop: seconds > 0 && drop !== null ? percent(drop) : '',
			peak: seconds > 0 && peak !== null ? degrees(peak) : ''
		};
	});
	const peak = num(day.peak_temp_c);
	return {
		status: 'samples',
		label,
		date: when,
		covered: span(covered * 60),
		rows,
		total: { cpu: span(num(day.cpu_s) ?? 0), drop: percent(num(day.drop_pct) ?? 0), peak: peak === null ? '' : degrees(peak) }
	};
}

/** Today and yesterday from the phone's ledger; anything it cannot read is a day without samples. */
export function powerColumns(ledger: unknown): PowerColumn[] {
	const l = obj(ledger);
	const days = Array.isArray(l?.days) ? (l.days as unknown[]).map(obj).filter((d): d is Obj => d !== null) : [];
	return [column('Today', 'today', l?.today, days), column('Yesterday', 'yesterday', l?.yesterday, days)];
}

/** Ask the first connected phone for its power report. Never throws: a failure is a state. */
export async function loadPower(call: Call | null, devices: CompanionDevice[]): Promise<PowerLoad> {
	if (!call) return { status: 'offline' };
	const phone = devices.find((d) => d.connected && d.platform === 'android');
	if (!phone) return { status: 'empty' };
	try {
		const reply = obj(await call('control_device', { device: phone.device_id, action: 'power_report', reason: 'The console shows the phone’s daily power ledger.' }));
		const payload = obj(reply?.result);
		if (!payload || payload.status !== 'ok') {
			return { status: 'error', detail: typeof payload?.error === 'string' && payload.error ? payload.error : 'the phone did not answer' };
		}
		const ledger = obj(obj(payload.result)?.ledger);
		if (!ledger) return { status: 'error', detail: "the phone's answer has no ledger: its app is older than the ledger" };
		return { status: 'ready', phone: phone.name, columns: powerColumns(ledger) };
	} catch (e) {
		return { status: 'error', detail: e instanceof Error ? e.message : String(e) };
	}
}
