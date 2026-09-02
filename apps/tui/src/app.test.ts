import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToString} from 'ink';
import {Composer, Transcript} from './app.js';

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

test('user messages use a full-width light background without a role label', () => {
	const item = {kind: 'user' as const, id: 'user-1', text: '简单介绍一下这个项目'};
	const element = Transcript({item}) as React.ReactElement<{backgroundColor: string; width: string}>;
	const output = renderToString(element, {columns: 40});

	assert.equal(element.props.backgroundColor, '#eeeeee');
	assert.equal(element.props.width, '100%');
	assert.match(output, /› 简单介绍一下这个项目/);
	assert.doesNotMatch(output, /You/);
});

test('assistant messages render without a role label', () => {
	const output = renderToString(
		React.createElement(Transcript, {
			item: {kind: 'assistant', id: 'assistant-1', text: '这是项目介绍。', finished: true},
		}),
		{columns: 40},
	);

	assert.match(output, /这是项目介绍。/);
	assert.doesNotMatch(output, /Agent/);
});

test('assistant reasoning is hidden by default and expands with the toggle prop', () => {
	const hidden = renderToString(
		React.createElement(Transcript, {
			item: {kind: 'assistant', id: 'assistant-1', text: 'Done', finished: true, reasoning: 'Private thought'},
		}),
		{columns: 60},
	);
	assert.match(hidden, /Reasoning hidden/);
	assert.doesNotMatch(hidden, /Private thought/);

	const shown = renderToString(
		React.createElement(Transcript, {
			item: {kind: 'assistant', id: 'assistant-1', text: 'Done', finished: true, reasoning: 'Private thought'},
			showReasoning: true,
		}),
		{columns: 60},
	);
	assert.match(shown, /Private thought/);
	assert.doesNotMatch(shown, /Reasoning hidden/);
});
