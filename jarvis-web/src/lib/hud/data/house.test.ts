import { describe, expect, it, vi } from 'vitest';
import type { EntityState } from '$lib/jarvisClient';
import { nameDevice, nameOffers } from './house';

const census = (devices: unknown[]): EntityState => ({
	entity_id: 'sensor.lan_devices',
	state: String(devices.length),
	attributes: { devices }
});

const tv = {
	mac: 'b0:a7:37:11:22:33',
	ip: '192.168.1.40',
	name: null,
	private: false,
	first_seen: 1,
	kind: 'tv',
	confidence: 'high',
	suggest: 'cast',
	evidence: { vendor: 'Roku', mdns: [], ssdp: 'Roku TV', ports: [8060], private_mac: false }
};

describe('nameOffers', () => {
	it('offers each unnamed device with its kind, evidence and a suggested name', () => {
		const offers = nameOffers([
			census([
				tv,
				{ ...tv, mac: 'aa:aa:aa:aa:aa:aa', ip: '192.168.1.2', name: 'router' },
				{ mac: '7a:00:00:00:00:01', ip: '192.168.1.77', name: null, private: true }
			])
		]);
		expect(offers).toEqual([
			{
				mac: 'b0:a7:37:11:22:33',
				ip: '192.168.1.40',
				kind: 'tv',
				confidence: 'high',
				evidence: 'Roku · Roku TV · ports 8060',
				suggested: 'Roku TV'
			},
			{
				mac: '7a:00:00:00:00:01',
				ip: '192.168.1.77',
				kind: 'unknown',
				confidence: 'low',
				evidence: 'private MAC',
				suggested: ''
			}
		]);
	});

	it('is empty with no census, and with every device named', () => {
		expect(nameOffers([])).toEqual([]);
		expect(nameOffers([census([{ ...tv, name: 'lounge tv' }])])).toEqual([]);
		expect(nameOffers([{ entity_id: 'sensor.lan_devices', state: 'unknown', attributes: {} }])).toEqual([]);
	});
});

describe('nameDevice', () => {
	it('calls netscan.name_device with the mac and the trimmed name', async () => {
		const callService = vi.fn().mockResolvedValue({ response: { ok: true } });
		await nameDevice({ callService }, tv.mac, '  lounge tv ');
		expect(callService).toHaveBeenCalledWith(
			'netscan',
			'name_device',
			{ mac: tv.mac, name: 'lounge tv' },
			{ returnResponse: true }
		);
	});

	it('throws the server\'s refusal in its words', async () => {
		const callService = vi.fn().mockResolvedValue({ response: { ok: false, error: 'mac and name are required' } });
		await expect(nameDevice({ callService }, tv.mac, 'x')).rejects.toThrow('mac and name are required');
	});

	it('refuses an empty name without calling', async () => {
		const callService = vi.fn();
		await expect(nameDevice({ callService }, tv.mac, '   ')).rejects.toThrow();
		expect(callService).not.toHaveBeenCalled();
	});
});
