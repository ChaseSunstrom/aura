// The projection contract, from the console's side.
//
// `tests/contracts/projections.json` is what the model may project and what
// the console draws; the jarvis-hud plugin validates `hud_project` against its
// own copy of it and `tests/test_project.py` there holds the same examples and
// the same malformed payloads to the same first error. A field the plugin lets
// through that this side refuses would put an error where a chart should be;
// one this side lets through that the plugin refuses never arrives.
import { readFileSync } from 'node:fs';
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';

import {
	CONTRACT,
	PROJECTION_TYPES,
	isEmptyProjection,
	projectionFromEvent,
	validateProjection
} from './contract';
import Projected from './Projected.svelte';

const CANONICAL = new URL('../../../../../tests/contracts/projections.json', import.meta.url);
const COPY = new URL('./projections.json', import.meta.url);

type Malformed = { type: string; data: unknown; error: string };
const examples = CONTRACT.examples as Record<string, Record<string, unknown>>;
const malformed = CONTRACT.malformed as Malformed[];

describe('the contract', () => {
	it('the console copy is the canonical file, byte for byte', () => {
		expect(readFileSync(COPY, 'utf8')).toBe(readFileSync(CANONICAL, 'utf8'));
	});

	it('names every type the brief asks for, and has an example of each', () => {
		expect([...PROJECTION_TYPES].sort()).toEqual(
			[
				'timeline', 'stat', 'line', 'bar', 'map', 'list', 'table', 'comparison', 'image',
				'model', 'code', 'diff', 'approval', 'progress', 'media', 'device', 'person',
				'scene', 'custom_html'
			].sort()
		);
		expect(Object.keys(examples).sort()).toEqual([...PROJECTION_TYPES].sort());
	});

	it.each([...PROJECTION_TYPES])('the %s example is valid and not empty', (type) => {
		expect(validateProjection(type, examples[type])).toBeNull();
		expect(isEmptyProjection(type, examples[type])).toBe(false);
	});

	it.each(malformed.map((m) => [m.error, m] as const))('refuses: %s', (_, m) => {
		const error = validateProjection(m.type, m.data);
		expect(error).not.toBeNull();
		expect(error).toContain(m.error);
	});

	it('refuses data over the size cap before reading a field', () => {
		const items = Array.from({ length: 1500 }, (_, i) => ({ text: `row ${i} `.padEnd(150, 'x') }));
		expect(validateProjection('list', { items })).toBe(
			`data is larger than ${CONTRACT.limits.data_bytes} bytes`
		);
	});

	it('counts a string in UTF-16 units, as the plugin does', () => {
		// 40 astral characters are 80 units: exactly the title cap; one more is over.
		const at = '😀'.repeat(40);
		expect(validateProjection('list', { title: at, items: [] })).toBeNull();
		expect(validateProjection('list', { title: `${at}x`, items: [] })).toContain('longer than 80');
	});

	it('takes no key that only looks like a field', () => {
		const data = JSON.parse('{"items": [], "__proto__": {"x": 1}}');
		expect(validateProjection('list', data)).toBe('__proto__ is not a field of list');
		expect(validateProjection('constructor', {})).toBe('constructor is not a projection type');
	});

	it('is empty only when every empty_when field is absent or empty', () => {
		expect(isEmptyProjection('bar', { bars: [] })).toBe(true);
		expect(isEmptyProjection('map', { points: [], route: [] })).toBe(true);
		expect(isEmptyProjection('map', { route: [[1, 2], [3, 4]] })).toBe(false);
		expect(isEmptyProjection('stat', { label: 'x', value: '' })).toBe(true);
		expect(isEmptyProjection('stat', { label: 'x', value: 0 })).toBe(false);
	});
});

describe('the event', () => {
	const frame = { id: 'p1', type: 'bar', data: { bars: [] }, origin: 'agent' };

	it('reads the envelope and leaves the data to the component', () => {
		expect(projectionFromEvent(frame)).toEqual({ id: 'p1', type: 'bar', data: { bars: [] } });
		// An unknown type still makes a projection: it shows its error, not nothing.
		expect(projectionFromEvent({ ...frame, type: 'hologram' })?.type).toBe('hologram');
	});

	it.each([
		['no id', { ...frame, id: undefined }],
		['an id with a space', { ...frame, id: 'p 1' }],
		['an id too long', { ...frame, id: 'p'.repeat(65) }],
		['no type', { ...frame, type: 7 }],
		['not an object', 'bar'],
		['null', null]
	])('drops a frame with %s', (_, payload) => {
		expect(projectionFromEvent(payload)).toBeNull();
	});
});

describe('Projected on the server', () => {
	const body = (props: Record<string, unknown>) => render(Projected, { props: props as never }).body;

	it.each([...PROJECTION_TYPES])('draws %s with its own component', (type) => {
		const out = body({ type, data: examples[type] });
		expect(out).toContain(`data-gen="${type}"`);
		expect(out).toContain('data-state="ready"');
	});

	it('says loading while the data is on its way', () => {
		expect(body({ type: 'bar', data: undefined })).toContain('data-state="loading"');
	});

	it('says empty when there is nothing to draw', () => {
		expect(body({ type: 'bar', data: { bars: [] } })).toContain('data-state="empty"');
	});

	it.each(malformed.map((m) => [m.error, m] as const))('shows the error for: %s', (_, m) => {
		const out = body({ type: m.type, data: m.data });
		expect(out).toContain('data-state="error"');
		expect(out).not.toContain('data-gen=');
	});

	it('draws model text as text, never as markup', () => {
		const out = body({ type: 'list', data: { items: [{ text: '<img src=x onerror=alert(1)>' }] } });
		expect(out).not.toContain('<img src=x');
		expect(out).toContain('&lt;img src=x');
	});

	it('runs nothing on the server: custom html is a placeholder until mounted', () => {
		const out = body({ type: 'custom_html', data: examples.custom_html });
		expect(out).not.toContain('<iframe');
		expect(out).not.toContain('<style>body');
	});

	it('takes the placement it is given', () => {
		const out = body({ type: 'stat', data: examples.stat, placement: { edge: 'left', leaving: true } });
		expect(out).toContain('data-leaving="true"');
	});
});
