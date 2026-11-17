import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '$lib/chat';
import { ago, dayBreaks, dayLabel, filterSessions, modelOptions, toolLine } from './chatScreen';

const msg = (id: string, at: number): ChatMessage => ({ id, role: 'user', content: id, thinking: '', tools: [], at, pending: false });

describe('the chat screen', () => {
	const now = new Date(2026, 8, 27, 12, 0).getTime();

	it('draws a day line where the day changes, named for a person', () => {
		const yesterday = new Date(2026, 8, 26, 20, 0).getTime();
		const breaks = dayBreaks([msg('a', yesterday), msg('b', yesterday + 60_000), msg('c', now)], now);
		expect([...breaks.entries()]).toEqual([
			[0, 'Yesterday'],
			[2, 'Today']
		]);
		expect(dayLabel(new Date(2026, 8, 23, 9).getTime(), now)).toMatch(/Wednesday/);
		expect(dayLabel(new Date(2026, 5, 2).getTime(), now)).toMatch(/Jun/);
	});

	it('says how old a conversation is briefly, and searches every word', () => {
		const s = now / 1000;
		expect(ago(s - 20, now)).toBe('now');
		expect(ago(s - 600, now)).toBe('10 min');
		expect(ago(s - 7200, now)).toBe('2 h');
		const rows = [
			{ id: '1', title: 'Garage door', preview: 'close it at night', started_at: 5 },
			{ id: '2', title: 'Dinner plans', preview: 'text Cameron', started_at: 9 }
		];
		expect(filterSessions(rows, '').map((r) => r.id)).toEqual(['2', '1']);
		expect(filterSessions(rows, 'text cam').map((r) => r.id)).toEqual(['2']);
		expect(filterSessions(rows, 'garage night').map((r) => r.id)).toEqual(['1']);
	});

	it('gives a tool call one readable line', () => {
		expect(toolLine({ key: 'k', name: 'phone_media', arguments: { playlist: 'Rocky 3', shuffle: true }, state: 'ok' })).toBe('phone media · Rocky 3');
		expect(toolLine({ key: 'k', name: 'routine_list', arguments: {}, state: 'running' })).toBe('routine list');
	});

	it('lists the servable models, marks a cold start, and keeps the session\'s own model', () => {
		const models = [
			{ id: 'a', name: 'A', state: 'loaded' as const },
			{ id: 'b', name: '', state: 'unloaded' as const },
			{ id: 'c', name: 'C', state: 'downloading' as const },
			{ id: 'd', name: 'D', state: 'missing' as const }
		];
		expect(modelOptions(models, 'a')).toEqual([
			{ value: 'a', label: 'A' },
			{ value: 'b', label: 'b, not loaded' }
		]);
		expect(modelOptions(models, 'elsewhere')[0]).toEqual({ value: 'elsewhere', label: 'elsewhere' });
		expect(modelOptions([], '')).toEqual([]);
	});
});
