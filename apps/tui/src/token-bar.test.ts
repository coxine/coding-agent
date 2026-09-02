import assert from 'node:assert/strict';
import test from 'node:test';
import {tokenBarSegments} from './token-bar.js';

test('token bar divides input, cached, output, reasoning, and remaining capacity', () => {
	const segments = tokenBarSegments(
		{promptTokens: 80, completionTokens: 20, cachedTokens: 20, reasoningTokens: 5},
		40,
		200,
	);

	assert.deepEqual(segments.map(segment => segment.value), [60, 20, 15, 5, 100]);
	assert.equal(segments.find(segment => segment.kind === 'remaining')?.width, 20);
	assert.equal(segments.reduce((sum, segment) => sum + segment.width, 0), 40);
});

test('token bar uses consumed tokens as scale when context limit is unknown', () => {
	const segments = tokenBarSegments(
		{promptTokens: 90, completionTokens: 10, cachedTokens: 0, reasoningTokens: 0},
		20,
	);

	assert.equal(segments.find(segment => segment.kind === 'remaining')?.width, 0);
	assert.equal(segments.reduce((sum, segment) => sum + segment.width, 0), 20);
});
