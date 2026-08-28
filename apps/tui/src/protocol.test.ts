import assert from 'node:assert/strict';
import test from 'node:test';
import {isCoreEvent, message, PROTOCOL_VERSION} from './protocol.js';

test('message creates a valid protocol envelope', () => {
	const value = message('initialize', {workspaceRoot: '/tmp/project'});
	assert.equal(value.protocolVersion, PROTOCOL_VERSION);
	assert.equal(value.type, 'initialize');
	assert.ok(value.messageId.startsWith('msg_'));
	assert.ok(isCoreEvent(value));
});

test('isCoreEvent rejects malformed values', () => {
	assert.equal(isCoreEvent(null), false);
	assert.equal(isCoreEvent({protocolVersion: 1, type: 'x'}), false);
	assert.equal(isCoreEvent({protocolVersion: 2, type: 'x', messageId: 'm', payload: {}}), false);
});

