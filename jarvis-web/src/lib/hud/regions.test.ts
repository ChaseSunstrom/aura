import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { CATALOGUE_LIMIT, OFFERED_SHOWN, STATE_WORD, moreResults, regionLine, sizeWords, unsettled } from './regions';

const contract = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../tests/contracts/map-pack.json', import.meta.url)), 'utf-8'));

describe('map regions', () => {
	it('has a word for every state the house can say (tests/contracts/map-pack.json)', () => {
		expect(Object.keys(STATE_WORD).sort()).toEqual([...contract.states].sort());
	});

	it('reads a size as a person would', () => {
		expect(sizeWords(122_236_928)).toBe('117 MB');
		expect(sizeWords(1.5 * 2 ** 30)).toBe('1.5 GB');
		expect(sizeWords(10)).toBe('1 MB');
		expect(sizeWords(0)).toBe('');
		expect(sizeWords(undefined)).toBe('');
	});

	it('says a ready region with its size, and any other with what the worker last said', () => {
		expect(regionLine({ id: 'us-utah', name: 'Utah', state: 'ready', bytes: 734_003_200 })).toBe('Ready · 700 MB');
		expect(regionLine({ id: 'us-utah', name: 'Utah', state: 'working', note: 'reading the extract' })).toBe('Downloading · reading the extract');
		expect(regionLine({ id: 'us-utah', name: 'Utah', state: 'failed', note: 'the network is down' })).toBe('Failed · the network is down');
		expect(regionLine({ id: 'us-utah', name: 'Utah', state: 'queued', note: '' })).toBe('Waiting to download');
		expect(regionLine({ id: 'us-utah', name: 'Utah', state: 'held', note: 'the quiet house is on' })).toBe('On hold · the quiet house is on');
		expect(regionLine({ id: 'x', name: 'X', state: 'later' })).toBe('later');
	});

	it('reads the list again only while something is on its way in or out', () => {
		const r = (state: string) => ({ id: state, name: state, state });
		expect(unsettled([r('ready'), r('failed')])).toBe(false);
		for (const s of ['queued', 'working', 'removing']) expect(unsettled([r('ready'), r(s)])).toBe(true);
		expect(unsettled([])).toBe(false);
	});

	it('says when a search has more results than the list shows (T-1019)', () => {
		expect(moreResults(0)).toBe('');
		expect(moreResults(OFFERED_SHOWN)).toBe('');
		expect(moreResults(OFFERED_SHOWN + 1)).toBe(`Showing ${OFFERED_SHOWN} of ${OFFERED_SHOWN + 1}. Add a word to narrow the search.`);
		// The house sends at most CATALOGUE_LIMIT, so a full page is "at least", never a count.
		expect(moreResults(CATALOGUE_LIMIT)).toBe(`Showing ${OFFERED_SHOWN} of at least ${CATALOGUE_LIMIT}. Add a word to narrow the search.`);
	});
});
