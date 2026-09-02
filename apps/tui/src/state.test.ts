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

test('conversation_deleted removes the session and keeps the picker open', () => {
	const initial = initialState('/tmp/project', 'model');
	const listed = reducer(initial, {
		type: 'core_event',
		event: message('sessions_listed', {
			activeConversationId: 'conv_1',
			sessions: [
				{id: 'conv_1', title: 'One', createdAt: 'now', updatedAt: 'now', messageCount: 1},
				{id: 'conv_2', title: 'Two', createdAt: 'now', updatedAt: 'now', messageCount: 1},
			],
		}),
	});
	const deleted = reducer(listed, {
		type: 'core_event',
		event: message('conversation_deleted', {
			deletedConversationId: 'conv_2',
			activeConversationId: 'conv_1',
			conversationTitle: 'One',
			activeChanged: false,
			sessions: [{id: 'conv_1', title: 'One', createdAt: 'now', updatedAt: 'now', messageCount: 1}],
		}),
	});
	assert.equal(deleted.sessions.length, 1);
	assert.equal(deleted.conversationId, 'conv_1');
	assert.equal(deleted.sessionPickerOpen, true);
});

test('conversation_deleted with activeChanged rebuilds the transcript', () => {
	const initial = initialState('/tmp/project', 'model');
	const deleted = reducer(initial, {
		type: 'core_event',
		event: message('conversation_deleted', {
			deletedConversationId: 'conv_1',
			activeConversationId: 'conv_2',
			conversationTitle: 'Two',
			activeChanged: true,
			transcript: [{role: 'user', content: 'replacement task'}],
			sessions: [{id: 'conv_2', title: 'Two', createdAt: 'now', updatedAt: 'now', messageCount: 1}],
		}),
	});
	assert.equal(deleted.conversationId, 'conv_2');
	assert.equal(deleted.conversationTitle, 'Two');
	assert.equal(deleted.items.length, 1);
	assert.equal(deleted.items[0]?.kind, 'user');
});

test('status report stores context usage and metadata', () => {
	let state = reducer(initialState('/tmp/project', 'model'), {
		type: 'core_event',
		event: message('status_report', {
			model: 'test-model',
			workspaceRoot: '/tmp/project',
			coreSessionId: 'sess_1',
			conversationId: 'conv_1',
			context: {totalChars: 1200, requestChars: 900, maxChars: 2000, messageCount: 8, requestMessageCount: 6, truncated: true},
			tokenUsage: {
				contextWindowTokens: 128000,
				requestCount: 2,
				measuredRequests: 2,
				unavailableRequests: 0,
				latest: {available: true, recordedAt: 'now', turnId: 'turn_1', step: 2, promptTokens: 120, completionTokens: 8, totalTokens: 128},
				latestMeasured: {available: true, recordedAt: 'now', turnId: 'turn_1', step: 2, promptTokens: 120, completionTokens: 8, totalTokens: 128},
				totals: {promptTokens: 200, completionTokens: 20, totalTokens: 220},
			},
			metadata: {maxSteps: 30, conversationTitle: 'Parser', titleSource: 'custom', persistedMessages: 8},
		}),
	});
	assert.equal(state.statusReport?.context.requestChars, 900);
	assert.equal(state.statusReport?.tokenUsage.latest?.promptTokens, 120);
	assert.equal(state.statusReport?.tokenUsage.latestMeasured?.promptTokens, 120);
	assert.equal(state.statusReport?.tokenUsage.contextWindowTokens, 128000);
	assert.equal(state.statusReport?.tokenUsage.totals.completionTokens, 20);
	assert.equal(state.statusReport?.metadata.maxSteps, 30);
	assert.equal(state.statusReport?.metadata.titleSource, undefined);
	assert.equal(state.statusReport?.metadata.persistedMessages, undefined);

	state = reducer(state, {type: 'close_status'});
	assert.equal(state.statusReport, undefined);
});

test('user input events open and close the question panel', () => {
	let state = reducer(initialState('/tmp/project', 'model'), {
		type: 'core_event',
		event: message('tool_requested', {name: 'request_user_input', arguments: {question: 'Which format?'}, risk: 'low'}, {toolCallId: 'call_1'}),
	});
	state = reducer(state, {
		type: 'core_event',
		event: message('user_input_required', {question: 'Which format?'}, {toolCallId: 'call_1'}),
	});
	assert.equal(state.pendingQuestion?.question, 'Which format?');
	assert.equal(state.tools.call_1?.status, 'waiting_input');

	state = reducer(state, {
		type: 'core_event',
		event: message('tool_finished', {ok: true, summary: 'User answered'}, {toolCallId: 'call_1'}),
	});
	assert.equal(state.pendingQuestion, undefined);
	assert.equal(state.tools.call_1?.status, 'succeeded');
});

test('cancelling a turn closes active tool cards', () => {
	let state = reducer(initialState('/tmp/project', 'model'), {
		type: 'core_event',
		event: message('tool_requested', {name: 'request_user_input', arguments: {question: 'Continue?'}, risk: 'low'}, {toolCallId: 'call_1'}),
	});
	state = reducer(state, {
		type: 'core_event',
		event: message('user_input_required', {question: 'Continue?'}, {toolCallId: 'call_1'}),
	});
	state = reducer(state, {type: 'core_event', event: message('turn_cancelled', {})});

	assert.equal(state.pendingQuestion, undefined);
	assert.equal(state.tools.call_1?.status, 'cancelled');
});

test('pause and resume events keep the active turn', () => {
	let state = reducer(initialState('/tmp/project', 'model'), {
		type: 'submitted',
		turnId: 'turn_1',
		text: 'Inspect the project',
	});
	state = reducer(state, {type: 'core_event', event: message('turn_paused', {}, {turnId: 'turn_1'})});

	assert.equal(state.paused, true);
	assert.equal(state.status, 'Paused');
	assert.equal(state.activeTurnId, 'turn_1');

	state = reducer(state, {type: 'core_event', event: message('turn_resumed', {}, {turnId: 'turn_1'})});
	assert.equal(state.paused, false);
	assert.equal(state.status, 'Resuming');
	assert.equal(state.activeTurnId, 'turn_1');
});

test('reasoning deltas are hidden by default and can be toggled', () => {
	let state = reducer(initialState('/tmp/project', 'model'), {
		type: 'core_event',
		event: message('assistant_message_started', {assistantMessageId: 'asst_1'}),
	});
	state = reducer(state, {
		type: 'core_event',
		event: message('assistant_reasoning_delta', {assistantMessageId: 'asst_1', text: 'Think '}),
	});
	state = reducer(state, {
		type: 'core_event',
		event: message('assistant_reasoning_delta', {assistantMessageId: 'asst_1', text: 'hard.'}),
	});
	state = reducer(state, {
		type: 'core_event',
		event: message('assistant_message_finished', {assistantMessageId: 'asst_1', text: 'Done', reasoning: 'Think hard.'}),
	});

	const assistant = state.items.find(item => item.kind === 'assistant');
	assert.equal(assistant?.kind, 'assistant');
	assert.equal(assistant?.reasoning, 'Think hard.');
	assert.equal(state.showReasoning, false);

	state = reducer(state, {type: 'toggle_reasoning'});
	assert.equal(state.showReasoning, true);
	state = reducer(state, {type: 'toggle_reasoning'});
	assert.equal(state.showReasoning, false);
});
