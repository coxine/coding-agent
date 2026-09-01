import assert from 'node:assert/strict';
import test from 'node:test';
import {parseInline, parseMarkdown, stripTerminalControls} from './markdown.js';

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

test('stripTerminalControls removes ANSI and control characters', () => {
	assert.equal(stripTerminalControls('\u001b[31mred\u001b[0m\u0000 text'), 'red text');
});

