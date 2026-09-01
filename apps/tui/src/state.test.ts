import assert from 'node:assert/strict';
import test from 'node:test';
import {initialState, reducer} from './state.js';
import {message} from './protocol.js';

test('initialized restores transcript and active conversation', () => {
	const state = reducer(initialState('/tmp/project', 'model'), {
		type: 'core_event',
		event: message('initialized', {
			conversationId: 'conv_1',
			conversationTitle: 'Fix parser',
			transcript: [
				{role: 'user', content: 'Fix it'},
				{role: 'assistant', content: '**Done**'},
			],
		}),
	});
	assert.equal(state.conversationId, 'conv_1');
	assert.equal(state.conversationTitle, 'Fix parser');
	assert.deepEqual(state.items.map(item => item.kind), ['user', 'assistant']);
});

test('session events open picker and switching replaces transcript', () => {
	const initial = initialState('/tmp/project', 'model');
	const listed = reducer(initial, {
		type: 'core_event',
		event: message('sessions_listed', {
			activeConversationId: 'conv_1',
			sessions: [{id: 'conv_1', title: 'One', createdAt: 'now', updatedAt: 'now', messageCount: 1}],
		}),
	});
	assert.equal(listed.sessionPickerOpen, true);
	assert.equal(listed.sessions[0]?.title, 'One');

	const switched = reducer(listed, {
		type: 'core_event',
		event: message('conversation_switched', {
			conversationId: 'conv_2',
			conversationTitle: 'Two',
			transcript: [{role: 'user', content: 'Another task'}],
		}),
	});
	assert.equal(switched.sessionPickerOpen, false);
	assert.equal(switched.conversationId, 'conv_2');
	assert.equal(switched.items.length, 1);
});
