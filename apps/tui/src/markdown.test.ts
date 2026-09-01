import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToString} from 'ink';
import stringWidth from 'string-width';
import {highlightCode, MarkdownText, parseInline, parseMarkdown, stripTerminalControls} from './markdown.js';

test('parseInline recognizes common inline markdown', () => {
	assert.deepEqual(parseInline('Use **bold**, *italic*, `code`, ~~old~~ and [docs](https://example.com).'), [
		{type: 'text', text: 'Use '},
		{type: 'bold', text: 'bold'},
		{type: 'text', text: ', '},
		{type: 'italic', text: 'italic'},
		{type: 'text', text: ', '},
		{type: 'code', text: 'code'},
		{type: 'text', text: ', '},
		{type: 'strike', text: 'old'},
		{type: 'text', text: ' and '},
		{type: 'link', text: 'docs', url: 'https://example.com'},
		{type: 'text', text: '.'},
	]);
});

test('parseInline leaves unfinished streaming markers visible', () => {
	assert.deepEqual(parseInline('working on **partial'), [{type: 'text', text: 'working on **partial'}]);
	assert.deepEqual(parseInline('call `unfinished'), [{type: 'text', text: 'call `unfinished'}]);
});

test('parseMarkdown recognizes headings, lists, quotes, rules and code blocks', () => {
	const blocks = parseMarkdown(
		'# Result\n\n- first\n- second\n\n> note\n\n---\n\n```ts\nconst value = 1;\n```',
	);
	assert.deepEqual(blocks, [
		{type: 'heading', level: 1, text: 'Result'},
		{type: 'space'},
		{
			type: 'list',
			ordered: false,
			items: [
				{text: 'first', indent: 0},
				{text: 'second', indent: 0},
			],
		},
		{type: 'space'},
		{type: 'quote', text: 'note'},
		{type: 'space'},
		{type: 'rule'},
		{type: 'space'},
		{type: 'code', language: 'ts', text: 'const value = 1;'},
	]);
});

test('parseMarkdown treats an unfinished fence as a streaming code block', () => {
	assert.deepEqual(parseMarkdown('```python\nprint("hello")'), [
		{type: 'code', language: 'python', text: 'print("hello")'},
	]);
});

test('parseMarkdown recognizes tables and column alignment', () => {
	assert.deepEqual(
		parseMarkdown('| Name | Value | Note |\n| :--- | ---: | :---: |\n| **one** | 42 | `a|b` |\n| two | 7 | a\\|b |'),
		[{
			type: 'table',
			headers: ['Name', 'Value', 'Note'],
			alignments: ['left', 'right', 'center'],
			rows: [
				['**one**', '42', '`a|b`'],
				['two', '7', 'a|b'],
			],
		}],
	);
});

test('highlightCode adds ANSI colors for known languages and falls back safely', () => {
	const highlighted = highlightCode('const answer = 42;', 'typescript');
	assert.match(highlighted, /\u001B\[/);
	assert.equal(stripTerminalControls(highlighted), 'const answer = 42;');
	assert.equal(highlightCode('plain text', 'not-a-language'), 'plain text');
});

test('MarkdownText renders aligned wide-character tables and highlighted code', () => {
	const output = renderToString(
		React.createElement(MarkdownText, {
			children: '| 名称 | Value |\n| --- | ---: |\n| 测试 | 42 |\n\n~~~ts\nconst answer = 42;\n~~~',
		}),
		{columns: 100},
	);
	const plain = stripTerminalControls(output);
	const lines = plain.split('\n');
	const tableStart = lines.findIndex(line => line.startsWith('┌'));
	const tableLines = lines.slice(tableStart, tableStart + 5);
	assert.equal(tableLines.length, 5);
	assert.equal(new Set(tableLines.map(line => stringWidth(line))).size, 1);
	assert.match(plain, /const answer = 42;/);
	assert.match(output, /\u001B\[/);
});

test('stripTerminalControls removes ANSI and control characters', () => {
	assert.equal(stripTerminalControls('\u001b[31mred\u001b[0m\u0000 text'), 'red text');
});
