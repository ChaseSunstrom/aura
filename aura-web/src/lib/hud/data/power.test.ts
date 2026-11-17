import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import type { CompanionDevice } from '$lib/jarvisClient';
import { loadPower, powerColumns } from './power';

/** The phone's answer as `tests/contracts/power-ledger.json` pins it: the Kotlin reads the same file. */
const contract = JSON.parse(
	readFileSync(fileURLToPath(new URL('../../../../../tests/contracts/power-ledger.json', import.meta.url)), 'utf-8')
) as { answer: { today: string; yesterday: string; days: { date: string }[] } };

const ledger = contract.answer;
const phone: CompanionDevice = { device_id: 'pixel-8', name: 'Pixel 8', platform: 'android', connected: true };
/** What `jarvis/tools/call` answers for `control_device`: the tool's payload, the phone's own under `result`. */
const answered = (inner: unknown) => ({ tool: 'control_device', result: { status: 'ok', device: 'Pixel 8', action: 'power_report', result: inner } });

describe('the phone power ledger on the Devices screen (T-0582)', () => {
	it('maps today and yesterday into two columns, a feature to a row', () => {
		const cols = powerColumns(ledger);
		expect(cols.map((c) => [c.label, c.date, c.status])).toEqual([
			['Today', '2030-01-10', 'samples'],
			['Yesterday', '2030-01-09', 'samples']
		]);
		const today = cols[0];
		if (today.status !== 'samples') throw new Error('today has samples');
		expect(today.covered).toBe('1 h');
		expect(today.rows.map((r) => r.key)).toEqual(['wake', 'location', 'link', 'car']);
		expect(today.rows[0]).toMatchObject({ label: 'Wake word', on: '30 min', drop: '3.5%', peak: '34.5 °C' });
		expect(today.total).toMatchObject({ drop: '5%', peak: '40.5 °C', cpu: '5 min 40 s' });
		const yesterday = cols[1];
		if (yesterday.status !== 'samples') throw new Error('yesterday has samples');
		// a feature that was never on says "off", not a column of zeros
		expect(yesterday.rows.find((r) => r.key === 'car')).toMatchObject({ on: 'off', drop: '', peak: '' });
		expect(yesterday.rows.find((r) => r.key === 'wake')).toMatchObject({ on: '1 h 30 min', drop: '7%' });
	});

	it('says so when a day has no samples, rather than a column of zeros', () => {
		const onlyToday = { ...ledger, days: ledger.days.slice(0, 1) };
		const cols = powerColumns(onlyToday);
		expect(cols[0].status).toBe('samples');
		expect(cols[1]).toMatchObject({ label: 'Yesterday', status: 'none', said: 'No readings were taken yesterday.' });
		expect(powerColumns({ ...ledger, days: [] }).map((c) => c.status)).toEqual(['none', 'none']);
		// a day that only started (one reading, no interval yet) is not a day of readings either
		const started = { ...ledger, days: [{ ...ledger.days[0], covered_min: 0 }] };
		expect(powerColumns(started)[0]).toMatchObject({ status: 'none', said: 'No readings have been taken today yet.' });
	});

	it('reads a ledger it does not know the shape of as no samples, never as a throw', () => {
		for (const bad of [null, {}, { today: 4 }, { today: '2030-01-10', yesterday: '2030-01-09', days: 'no' }]) {
			expect(() => powerColumns(bad as never)).not.toThrow();
		}
		expect(powerColumns({ today: '2030-01-10', yesterday: '2030-01-09', days: [{ date: '2030-01-10', features: 7 }] } as never)[0].status).toBe('none');
	});

	it('asks the connected phone for its power report and returns the two columns', async () => {
		const asked: { name: string; args: Record<string, unknown> }[] = [];
		const call = async (name: string, args: Record<string, unknown>) => {
			asked.push({ name, args });
			return answered({ ledger });
		};
		const got = await loadPower(call, [{ device_id: 'desk', name: 'Desk', platform: 'linux', connected: true }, phone]);
		expect(got.status).toBe('ready');
		expect(asked).toHaveLength(1);
		expect(asked[0].name).toBe('control_device');
		expect(asked[0].args).toMatchObject({ device: 'pixel-8', action: 'power_report' });
		if (got.status === 'ready') expect(got.phone).toBe('Pixel 8');
	});

	it('is empty with no phone connected, offline with no house, and says why when the phone cannot answer', async () => {
		const never = async () => {
			throw new Error('must not be asked');
		};
		expect((await loadPower(never, [{ ...phone, connected: false }])).status).toBe('empty');
		expect((await loadPower(never, [])).status).toBe('empty');
		expect((await loadPower(null, [phone])).status).toBe('offline');
		expect(await loadPower(async () => ({ result: { status: 'error', error: 'the phone is unreachable' } }), [phone])).toEqual({
			status: 'error',
			detail: 'the phone is unreachable'
		});
		expect(await loadPower(async () => answered({ thermal_status: 'none' }), [phone])).toEqual({
			status: 'error',
			detail: "the phone's answer has no ledger: its app is older than the ledger"
		});
		expect(
			await loadPower(async () => {
				throw new Error('socket closed');
			}, [phone])
		).toEqual({ status: 'error', detail: 'socket closed' });
	});
});
