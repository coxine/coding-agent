import assert from 'node:assert/strict';
import test from 'node:test';
import {matchingCommands} from './commands.js';

test('slash opens the full command list', () => {
	assert.deepEqual(matchingCommands('/').map(command => command.name), ['/session']);
});

test('command list filters by typed prefix', () => {
	assert.deepEqual(matchingCommands('/ses').map(command => command.name), ['/session']);
	assert.deepEqual(matchingCommands('/unknown'), []);
});

test('normal and multiline input do not open command list', () => {
	assert.deepEqual(matchingCommands('fix it'), []);
	assert.deepEqual(matchingCommands('/session\nmore'), []);
});
