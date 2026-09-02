import assert from 'node:assert/strict';
import test from 'node:test';
import {pushHistory, stepHistory} from './history.js';

test('pushHistory appends and caps at the given length', () => {
	assert.deepEqual(pushHistory([], 'a'), ['a']);
	assert.deepEqual(pushHistory(['a', 'b'], 'c'), ['a', 'b', 'c']);
	assert.deepEqual(pushHistory(['b', 'c'], 'd', 3), ['b', 'c', 'd']);
});

test('stepHistory recalls previous entries and preserves the draft', () => {
	const first = stepHistory(['one', 'two', 'three'], -1, '', 'typed', 'up');
	assert.equal(first.index, 2);
	assert.equal(first.draft, 'typed');
	assert.equal(first.input, 'three');

	const second = stepHistory(['one', 'two', 'three'], 2, 'typed', 'three', 'up');
	assert.equal(second.index, 1);
	assert.equal(second.input, 'two');
});

test('stepHistory returns the draft when navigating past the newest entry', () => {
	const next = stepHistory(['one', 'two'], 1, 'typed', 'two', 'down');
	assert.equal(next.index, -1);
	assert.equal(next.draft, '');
	assert.equal(next.input, 'typed');
});

test('stepHistory is a no-op on empty history and at bounds', () => {
	assert.equal(stepHistory([], -1, '', 'x', 'up').input, 'x');
	assert.equal(stepHistory(['one'], -1, '', 'x', 'down').input, 'x');
	assert.equal(stepHistory(['one'], 0, 'draft', 'one', 'up').input, 'one');
});
