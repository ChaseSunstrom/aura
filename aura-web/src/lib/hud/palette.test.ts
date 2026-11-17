import { describe, expect, it } from 'vitest';
import { rank, score, type PaletteItem } from './palette';

const item = (group: PaletteItem['group'], title: string, sub?: string): PaletteItem => ({ id: title, group, title, sub, run: () => {} });

describe('the palette search', () => {
	it('scores a prefix over a word prefix over a substring over letters in order', () => {
		expect(score('Load model', 'load')).toBe(4);
		expect(score('Unload all models', 'mod')).toBe(3);
		expect(score('Notifications', 'fica')).toBe(2);
		expect(score('Notifications', 'ntf')).toBe(1);
		expect(score('Notifications', 'zz')).toBe(0);
		expect(score('Anything', '  ')).toBe(1);
	});

	it('ranks the best match first and drops what does not match', () => {
		const items = [item('Settings', 'Voice settings'), item('Sections', 'Models'), item('Models', 'qwen3.8-27b', 'cyankiwi/Qwen')];
		expect(rank(items, 'mod').map((i) => i.title)).toEqual(['Models']);
		expect(rank(items, 'qwen').map((i) => i.title)).toEqual(['qwen3.8-27b']);
	});

	it('finds an item by its second phrase, below a title match', () => {
		const items = [item('Models', 'Ripple 65B', 'AtomicChat/Ripple-65B'), item('Actions', 'Atomic clock')];
		expect(rank(items, 'atomic').map((i) => i.title)).toEqual(['Atomic clock', 'Ripple 65B']);
	});

	it('keeps the groups in order for an empty query', () => {
		const items = [item('Settings', 'B'), item('Sections', 'A'), item('Actions', 'C')];
		expect(rank(items, '').map((i) => i.group)).toEqual(['Sections', 'Actions', 'Settings']);
	});
});
