// The panel types and their envelopes (GLASS_HUD §9.1).
import { describe, it, expect } from 'vitest';
import { clampExtent, envelopeOf, GRID_UNIT_PX, KIND_TO_TYPE, PANEL_TYPES, PANEL_TYPE_NAMES, typeForKind, unitsToCss } from './panelTypes';

describe('the §9.1 table', () => {
	it('gives every type a min ≤ preferred ≤ max on both axes', () => {
		for (const [name, envelope] of Object.entries(PANEL_TYPES)) {
			for (const axis of ['w', 'h'] as const) {
				const { min, preferred, max } = envelope;
				expect(min[axis], `${name}.min.${axis}`).not.toBeNull();
				expect(preferred[axis]!, `${name}.preferred.${axis}`).toBeGreaterThanOrEqual(min[axis]!);
				if (max[axis] !== null) expect(max[axis]!, `${name}.max.${axis}`).toBeGreaterThanOrEqual(preferred[axis]!);
			}
		}
	});
});

describe('the content contract mapping (§9.1 last paragraph)', () => {
	it('maps every kind the spec lists', () => {
		expect(KIND_TO_TYPE).toMatchObject({
			text: 'document',
			code: 'document',
			table: 'telemetry',
			chart: 'telemetry',
			telemetry: 'telemetry',
			image: 'image',
			camera: 'image',
			terminal: 'task',
			web: 'browser',
			device: 'house',
			goal: 'goals',
			approval: 'approvals',
			clarify: 'approvals',
			notice: 'notifications',
			video: 'video',
			audio: 'audio',
			document: 'document'
		});
	});

	it('lands an unknown kind on a readable panel rather than on nothing', () => {
		expect(typeForKind('something-new')).toBe('document');
	});

	it('only ever names a type that exists', () => {
		for (const type of Object.values(KIND_TO_TYPE)) expect(PANEL_TYPE_NAMES).toContain(type);
	});
});

describe('clampExtent', () => {
	it('holds a panel above the size its content is legible at', () => {
		expect(clampExtent('chat', { w: 1, h: 1 })).toEqual({ w: 4, h: 4 });
	});

	it('holds it under the type maximum where the type has one', () => {
		expect(clampExtent('approvals', { w: 99, h: 99 })).toEqual({ w: 6, h: 4 });
	});

	it('lets a type with no maximum fill', () => {
		expect(clampExtent('kanban', { w: 99, h: 99 })).toEqual({ w: 99, h: 99 });
	});

	it('reads a null request as "fill", which is the table own ∞', () => {
		expect(clampExtent('timeline', { w: null, h: null })).toEqual({ w: 6, h: null });
	});

	it('refuses a type the table does not have', () => {
		expect(() => envelopeOf('nonesuch' as never)).toThrow(/unknown panel type/);
	});
});

describe('grid units', () => {
	it('turns units into the CSS length the frame advertises', () => {
		expect(unitsToCss(6)).toBe(`${6 * GRID_UNIT_PX}px`);
		expect(unitsToCss(null)).toBeUndefined();
	});
});
