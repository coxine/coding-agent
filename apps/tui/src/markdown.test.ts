import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToString} from 'ink';
import stringWidth from 'string-width';
import {
	highlightCode,
	MarkdownText,
	renderMarkdownForTerminal,
	stripTerminalControls,
} from './markdown.js';

test('renders common GFM blocks and inline formatting', () => {
	const rendered = renderMarkdownForTerminal(
		'# Result\n\nUse **bold**, *italic*, `code` and [docs](https://example.com).\n\n- first\n- second\n\n> note',
		80,
	);
	const plain = stripTerminalControls(rendered);
	assert.doesNotMatch(plain, /\*\*bold\*\*/);
	assert.match(plain, /Result/);
	assert.match(plain, /bold/);
	assert.match(plain, /first/);
	assert.match(plain, /│ note/);
	assert.match(plain, /docs/);
	assert.doesNotMatch(rendered, /\u001B]8/);
});

test('renders GFM tables with alignment and terminal-width truncation', () => {
	const rendered = renderMarkdownForTerminal(
		'| 名称 | Value | Note |\n| :--- | ---: | :---: |\n| 测试 | 42 | a very long value that must fit |',
		42,
	);
	const plain = stripTerminalControls(rendered);
	const lines = plain.split('\n').filter(line => line.trim());
	assert.match(plain, /名称/);
	assert.match(plain, /测试/);
	assert.ok(lines.every(line => stringWidth(line) <= 42));
	assert.match(plain, /…/);
});

test('highlights known fenced languages and falls back for unknown ones', () => {
	const highlighted = highlightCode('const answer = 42;', 'typescript');
	assert.match(highlighted, /\u001B\[/);
	assert.equal(stripTerminalControls(highlighted), 'const answer = 42;');
	assert.equal(highlightCode('plain text', 'not-a-language'), 'plain text');
	assert.equal(highlightCode('plain text'), 'plain text');
});

test('renders unfinished streaming markdown without throwing', () => {
	assert.match(stripTerminalControls(renderMarkdownForTerminal('working on **partial', 60)), /working on/);
	assert.match(
		stripTerminalControls(renderMarkdownForTerminal('~~~python\nprint("hello")', 60)),
		/print\("hello"\)/,
	);
});

test('MarkdownText renders through Ink with ANSI styling intact', () => {
	const output = renderToString(
		React.createElement(MarkdownText, {
			children: '| Name | Value |\n| --- | ---: |\n| test | 42 |\n\n~~~ts\nconst answer = 42;\n~~~',
		}),
		{columns: 80},
	);
	assert.match(stripTerminalControls(output), /const answer = 42;/);
	assert.match(stripTerminalControls(output), /Name/);
	assert.match(output, /\u001B\[/);
});

test('strips CSI, OSC and control characters from model text', () => {
	assert.equal(stripTerminalControls('\u001B[31mred\u001B[0m\u0000 text'), 'red text');
	assert.equal(
		stripTerminalControls('\u001B]8;;https://evil.example\u0007click\u001B]8;;\u0007'),
		'click',
	);
	assert.equal(stripTerminalControls('safe\u001B]8;;https://evil.example'), 'safe');
});
