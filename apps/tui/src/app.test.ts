import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToString} from 'ink';
import {Composer} from './app.js';

test('empty composer places the cursor before a dim placeholder', () => {
	const output = renderToString(React.createElement(Composer, {value: '', enabled: true}));
	// renderToString strips color attributes, but preserves the cursor's leading cell.
	assert.match(output, />  Type a task…/);
});

test('composer places the cursor after entered text', () => {
	const output = renderToString(React.createElement(Composer, {value: 'fix parser', enabled: true}));
	assert.match(output, /> fix parser /);
	assert.doesNotMatch(output, /Type a task/);
});
