import { describe, expect, it } from 'vitest';
import { sourceWord, type MemoryEntry } from './memory';

const entry = (source: string, person?: string) => ({ source, person }) as unknown as MemoryEntry;

describe('sourceWord', () => {
	it('says where a memory came from in words, never the raw source key', () => {
		expect(sourceWord(entry('user'))).toBe('From you');
		expect(sourceWord(entry('extracted', 'Casey'))).toBe('From something you mentioned about Casey');
		expect(sourceWord(entry('some_new_source'))).toBe('From some new source');
		expect(sourceWord(entry('', 'Casey'))).toBe('about Casey');
	});
});
