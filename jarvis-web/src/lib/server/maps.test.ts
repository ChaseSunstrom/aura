import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { BBOX, CATALOGUE_Q_MAX, MAPS_BODY_MAX, MAPS_OPS, REGION_ID, mapsTarget, regionsTarget } from './maps';

describe('mapsTarget', () => {
	it('passes the calls the contract names, as the adapter does (tests/contracts/house-maps.json)', () => {
		const contract = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../tests/contracts/house-maps.json', import.meta.url)), 'utf-8'));
		expect(MAPS_OPS).toEqual(contract.ops);
		expect(MAPS_BODY_MAX).toBe(contract.body_max);
		expect(mapsTarget('http://core', 'buildings', 'GET', '1,2,3,4.123456789')).toBeNull();
		expect(BBOX.source).toBe(contract.bbox);
	});

	it('names the house path for each op on its own method', () => {
		expect(mapsTarget('http://core:8080/', 'route', 'POST', null)).toBe('http://core:8080/api/maps/route');
		expect(mapsTarget('http://core:8080', 'match', 'POST', null)).toBe('http://core:8080/api/maps/match');
		expect(mapsTarget('http://core:8080', 'buildings', 'GET', '-0.1,51.5,-0.05,51.55')).toBe(
			'http://core:8080/api/maps/buildings?bbox=-0.1,51.5,-0.05,51.55'
		);
		expect(mapsTarget('http://core:8080', 'live', 'GET', '-0.1,51.5,-0.05,51.55')).toBe(
			'http://core:8080/api/maps/live?bbox=-0.1,51.5,-0.05,51.55'
		);
		expect(mapsTarget('http://core', 'live', 'GET', '0,0,0.1')).toBeNull();
	});

	it('refuses any other op, and an op on the wrong method', () => {
		for (const op of ['tiles', '../memory/export', 'route/../../config', '__proto__', 'constructor']) {
			expect(mapsTarget('http://core', op, 'POST', null)).toBeNull();
			expect(mapsTarget('http://core', op, 'GET', null)).toBeNull();
		}
		expect(mapsTarget('http://core', 'route', 'GET', null)).toBeNull();
		expect(mapsTarget('http://core', 'buildings', 'POST', '0,0,0.1,0.1')).toBeNull();
	});

	it('lets only four numbers into the buildings query', () => {
		for (const bbox of [null, '', '0,0,0.1', '0,0,0.1,0.1&admin=1', '0,0,0.1,0.1#', 'a,b,c,d', '0,0,0.1,0.1,0']) {
			expect(mapsTarget('http://core', 'buildings', 'GET', bbox)).toBeNull();
		}
	});
});

describe('regionsTarget', () => {
	const contract = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../tests/contracts/map-pack.json', import.meta.url)), 'utf-8'));

	it('passes every call the contract names, on its method, to the same house path', () => {
		expect(REGION_ID.source).toBe(contract.id);
		for (const [method, path] of Object.values(contract.calls) as [string, string][]) {
			const parts = path.replace('/api/maps/', '').replace('{id}', 'us-utah').split('/');
			const target = regionsTarget('http://core:8080/', parts, method, 'utah');
			expect(target?.split('?')[0]).toBe('http://core:8080' + path.replace('{id}', 'us-utah'));
		}
		expect(regionsTarget('http://core', ['catalogue'], 'GET', 'new larch & co')).toBe('http://core/api/maps/catalogue?q=new%20larch%20%26%20co');
	});

	it('refuses another method, an id that is not one, a deeper path and a long search', () => {
		expect(regionsTarget('http://core', ['regions'], 'DELETE', null)).toBeNull();
		expect(regionsTarget('http://core', ['packs'], 'GET', null)).toBeNull();
		expect(regionsTarget('http://core', ['packs', 'us-utah'], 'DELETE', null)).toBeNull();
		expect(regionsTarget('http://core', ['regions', 'us-utah'], 'GET', null)).toBeNull();
		expect(regionsTarget('http://core', ['cover'], 'POST', null)).toBeNull();
		for (const id of ['', '..', 'US-UTAH', 'a/b', 'a%2fb', '-a', 'a'.repeat(65), '__proto__']) {
			expect(regionsTarget('http://core', ['packs', id], 'GET', null)).toBeNull();
		}
		expect(regionsTarget('http://core', ['packs', 'us-utah', 'x'], 'GET', null)).toBeNull();
		expect(regionsTarget('http://core', ['catalogue'], 'GET', 'x'.repeat(CATALOGUE_Q_MAX + 1))).toBeNull();
	});
});
