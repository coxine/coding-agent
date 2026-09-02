import assert from 'node:assert/strict';
import test from 'node:test';
import {matchingCommands, parseSlashCommand} from './commands.js';

test('slash opens the full command list', () => {
	assert.deepEqual(matchingCommands('/').map(command => command.name), ['/rename', '/session', '/status']);
});

test('command list filters by typed prefix', () => {
	assert.deepEqual(matchingCommands('/ses').map(command => command.name), ['/session']);
	assert.deepEqual(matchingCommands('/sta').map(command => command.name), ['/status']);
	assert.deepEqual(matchingCommands('/ren').map(command => command.name), ['/rename']);
	assert.deepEqual(matchingCommands('/unknown'), []);
});

test('parses slash command arguments', () => {
	assert.deepEqual(parseSlashCommand('/rename Parser fixes'), {
		command: {name: '/rename', description: 'Rename the current conversation: /rename <name>'},
		argument: 'Parser fixes',
	});
	assert.equal(parseSlashCommand('/unknown value'), undefined);
});

test('normal and multiline input do not open command list', () => {
	assert.deepEqual(matchingCommands('fix it'), []);
	assert.deepEqual(matchingCommands('/session\nmore'), []);
});
