import {randomUUID} from 'node:crypto';
import {CoreEvent} from './protocol.js';

export type ToolView = {
	id: string;
	name: string;
	arguments: Record<string, unknown>;
	risk: string;
	status: 'requested' | 'waiting_approval' | 'waiting_input' | 'running' | 'succeeded' | 'failed' | 'denied' | 'cancelled';
	summary: string;
	durationMs?: number;
	output: string;
	diff?: string;
	error?: string;
};

export type TranscriptItem =
	| {kind: 'user'; id: string; text: string}
	| {kind: 'assistant'; id: string; text: string; finished: boolean; reasoning?: string}
	| {kind: 'tool'; id: string}
	| {kind: 'notice'; id: string; text: string; level: 'info' | 'error'};

export type Approval = {
	toolCallId: string;
	name: string;
	summary: string;
	reason: string;
	arguments: Record<string, unknown>;
};

export type Question = {
	toolCallId: string;
	question: string;
};

export type SessionSummary = {
	id: string;
	title: string;
	createdAt: string;
	updatedAt: string;
	messageCount: number;
};

export type StatusReport = {
	model: string;
	workspaceRoot: string;
	coreSessionId: string;
	conversationId: string;
	context: {
		totalChars: number;
		requestChars: number;
		maxChars: number;
		messageCount: number;
		requestMessageCount: number;
		truncated: boolean;
	};
	tokenUsage: {
		contextWindowTokens?: number;
		requestCount: number;
		measuredRequests: number;
		unavailableRequests: number;
		latest?: TokenUsageRecord;
		latestMeasured?: TokenUsageRecord;
		totals: TokenTotals;
	};
	metadata: Record<string, string | number | boolean>;
};

type TokenUsageRecord = TokenTotals & {
	available: boolean;
	recordedAt: string;
	turnId: string;
	step: number;
};

type TokenTotals = {
	promptTokens: number;
	completionTokens: number;
	totalTokens: number;
	cachedTokens: number;
	reasoningTokens: number;
};

export type AppState = {
	connection: 'starting' | 'ready' | 'fatal' | 'closed';
	sessionId?: string;
	conversationId?: string;
	conversationTitle: string;
	sessions: SessionSummary[];
	sessionPickerOpen: boolean;
	statusReport?: StatusReport;
	model: string;
	workspaceRoot: string;
	status: string;
	step: number;
	activeTurnId?: string;
	paused: boolean;
	showReasoning: boolean;
	items: TranscriptItem[];
	tools: Record<string, ToolView>;
	pendingApproval?: Approval;
	pendingQuestion?: Question;
	fatalError?: string;
};

export type Action =
	| {type: 'core_event'; event: CoreEvent}
	| {type: 'submitted'; turnId: string; text: string}
	| {type: 'fatal'; message: string}
	| {type: 'notice'; message: string; level?: 'info' | 'error'}
	| {type: 'toggle_reasoning'}
	| {type: 'close_session_picker'}
	| {type: 'close_status'};

export function initialState(workspaceRoot: string, model: string): AppState {
	return {
		connection: 'starting',
		model,
		workspaceRoot,
		status: 'Starting Agent Core',
		conversationTitle: 'New session',
		sessions: [],
		sessionPickerOpen: false,
		step: 0,
		paused: false,
		showReasoning: false,
		items: [],
		tools: {},
	};
}

export function reducer(state: AppState, action: Action): AppState {
	if (action.type === 'close_session_picker') return {...state, sessionPickerOpen: false};
	if (action.type === 'close_status') return {...state, statusReport: undefined};
	if (action.type === 'toggle_reasoning') return {...state, showReasoning: !state.showReasoning};
	if (action.type === 'fatal') {
		return {...state, connection: 'fatal', status: 'Failed', fatalError: action.message};
	}
	if (action.type === 'notice') {
		return {
			...state,
			items: [...state.items, {kind: 'notice', id: randomUUID(), text: action.message, level: action.level ?? 'info'}],
		};
	}
	if (action.type === 'submitted') {
		return {
			...state,
			activeTurnId: action.turnId,
			paused: false,
			status: 'Submitting task',
			items: [...state.items, {kind: 'user', id: action.turnId, text: action.text}],
		};
	}

	const event = action.event;
	const payload = event.payload;
	switch (event.type) {
		case 'initialized':
			return {
				...state,
				connection: 'ready',
				sessionId: event.sessionId,
				model: String(payload.model ?? state.model),
				workspaceRoot: String(payload.workspaceRoot ?? state.workspaceRoot),
				conversationId: String(payload.conversationId ?? ''),
				conversationTitle: String(payload.conversationTitle ?? 'New session'),
				items: transcriptItems(payload.transcript),
				status: 'Ready',
			};
		case 'sessions_listed':
			return {
				...state,
				conversationId: String(payload.activeConversationId ?? state.conversationId ?? ''),
				sessions: sessionSummaries(payload.sessions),
				sessionPickerOpen: true,
			};
		case 'status_report':
			return {...state, statusReport: statusReport(payload)};
		case 'conversation_switched':
		case 'conversation_created':
			return {
				...state,
				conversationId: String(payload.conversationId ?? ''),
				conversationTitle: String(payload.conversationTitle ?? 'New session'),
				items: transcriptItems(payload.transcript),
				tools: {},
				step: 0,
				paused: false,
				status: 'Ready',
				sessionPickerOpen: false,
				statusReport: undefined,
			};
		case 'conversation_updated':
			return {
				...state,
				conversationId: String(payload.conversationId ?? state.conversationId ?? ''),
				conversationTitle: String(payload.conversationTitle ?? state.conversationTitle),
			};
		case 'conversation_deleted': {
			const activeChanged = Boolean(payload.activeChanged);
			const next: AppState = {
				...state,
				conversationId: String(payload.activeConversationId ?? state.conversationId ?? ''),
				conversationTitle: String(payload.conversationTitle ?? state.conversationTitle),
				sessions: sessionSummaries(payload.sessions),
			};
			if (!activeChanged) return next;
			return {
				...next,
				items: transcriptItems(payload.transcript),
				tools: {},
				step: 0,
				paused: false,
				status: 'Ready',
				statusReport: undefined,
			};
		}
		case 'agent_status':
			return {...state, status: prettyStatus(String(payload.status ?? 'running')), step: Number(payload.step ?? state.step)};
		case 'turn_paused':
			return {...state, paused: true, status: 'Paused'};
		case 'turn_resumed':
			return {...state, paused: false, status: 'Resuming'};
		case 'assistant_message_started': {
			const id = String(payload.assistantMessageId);
			return {...state, items: [...state.items, {kind: 'assistant', id, text: '', finished: false}]};
		}
		case 'assistant_delta': {
			const id = String(payload.assistantMessageId);
			return {
				...state,
				items: state.items.map(item =>
					item.kind === 'assistant' && item.id === id ? {...item, text: item.text + String(payload.text ?? '')} : item,
				),
			};
		}
		case 'assistant_reasoning_delta': {
			const id = String(payload.assistantMessageId);
			return {
				...state,
				items: state.items.map(item =>
					item.kind === 'assistant' && item.id === id
						? {...item, reasoning: (item.reasoning ?? '') + String(payload.text ?? '')}
						: item,
				),
			};
		}
		case 'assistant_message_finished': {
			const id = String(payload.assistantMessageId);
			return {
				...state,
				items: state.items.map(item =>
					item.kind === 'assistant' && item.id === id
						? {
								...item,
								text: String(payload.text ?? item.text),
								reasoning: String(payload.reasoning ?? item.reasoning ?? ''),
								finished: true,
							}
						: item,
				),
			};
		}
		case 'tool_requested': {
			const id = event.toolCallId ?? event.messageId;
			const tool: ToolView = {
				id,
				name: String(payload.name ?? 'unknown'),
				arguments: asRecord(payload.arguments),
				risk: String(payload.risk ?? 'unknown'),
				status: 'requested',
				summary: summarizeTool(String(payload.name ?? 'unknown'), asRecord(payload.arguments)),
				output: '',
			};
			return {...state, tools: {...state.tools, [id]: tool}, items: [...state.items, {kind: 'tool', id}]};
		}
		case 'approval_required': {
			const id = event.toolCallId ?? '';
			return {
				...state,
				pendingApproval: {
					toolCallId: id,
					name: String(payload.name ?? 'unknown'),
					summary: String(payload.summary ?? ''),
					reason: String(payload.reason ?? ''),
					arguments: asRecord(payload.arguments),
				},
				tools: updateTool(state.tools, id, {status: 'waiting_approval'}),
			};
		}
		case 'user_input_required': {
			const id = event.toolCallId ?? '';
			return {
				...state,
				pendingQuestion: {toolCallId: id, question: String(payload.question ?? '')},
				tools: updateTool(state.tools, id, {status: 'waiting_input'}),
			};
		}
		case 'tool_started': {
			const id = event.toolCallId ?? '';
			return {...state, tools: updateTool(state.tools, id, {status: 'running'})};
		}
		case 'tool_output_delta': {
			const id = event.toolCallId ?? '';
			const old = state.tools[id]?.output ?? '';
			return {...state, tools: updateTool(state.tools, id, {output: (old + String(payload.text ?? '')).slice(-6000)})};
		}
		case 'file_diff': {
			const id = event.toolCallId ?? '';
			return {...state, tools: updateTool(state.tools, id, {diff: String(payload.diff ?? '')})};
		}
		case 'tool_finished': {
			const id = event.toolCallId ?? '';
			const ok = Boolean(payload.ok);
			const error = asRecord(payload.error);
			const errorCode = typeof error.code === 'string' ? error.code : undefined;
			return {
				...state,
				pendingApproval: state.pendingApproval?.toolCallId === id ? undefined : state.pendingApproval,
				pendingQuestion: state.pendingQuestion?.toolCallId === id ? undefined : state.pendingQuestion,
				tools: updateTool(state.tools, id, {
					status: ok ? 'succeeded' : errorCode === 'approval_denied' ? 'denied' : 'failed',
					summary: String(payload.summary ?? ''),
					durationMs: Number(payload.durationMs ?? 0),
					error: typeof error.message === 'string' ? error.message : undefined,
				}),
			};
		}
		case 'context_updated':
			return {...state, items: [...state.items, {kind: 'notice', id: event.messageId, text: String(payload.summary ?? ''), level: 'info'}]};
		case 'turn_finished':
			return {...state, activeTurnId: undefined, paused: false, status: 'Ready', step: 0, pendingApproval: undefined, pendingQuestion: undefined};
		case 'turn_failed':
			return {
				...state,
				activeTurnId: undefined,
				paused: false,
				status: 'Failed',
				pendingApproval: undefined,
				pendingQuestion: undefined,
				items: [...state.items, {kind: 'notice', id: event.messageId, text: errorText(payload), level: 'error'}],
			};
		case 'turn_cancelled':
			return {
				...state,
				activeTurnId: undefined,
				paused: false,
				status: 'Cancelled',
				tools: cancelActiveTools(state.tools),
				pendingApproval: undefined,
				pendingQuestion: undefined,
				items: [...state.items, {kind: 'notice', id: event.messageId, text: 'Current task was cancelled.', level: 'info'}],
			};
		case 'error':
			return Boolean(payload.fatal)
				? {...state, connection: 'fatal', fatalError: String(payload.message ?? 'Agent Core error')}
				: {...state, items: [...state.items, {kind: 'notice', id: event.messageId, text: String(payload.message ?? 'Agent Core error'), level: 'error'}]};
		case 'shutdown_complete':
			return {...state, connection: 'closed', status: 'Closed'};
		default:
			return state;
	}
}

function cancelActiveTools(tools: Record<string, ToolView>): Record<string, ToolView> {
	return Object.fromEntries(
		Object.entries(tools).map(([id, tool]) => [
			id,
			['requested', 'waiting_approval', 'waiting_input', 'running'].includes(tool.status)
				? {...tool, status: 'cancelled'}
				: tool,
		]),
	);
}

function updateTool(tools: Record<string, ToolView>, id: string, update: Partial<ToolView>): Record<string, ToolView> {
	if (!tools[id]) return tools;
	return {...tools, [id]: {...tools[id], ...update}};
}

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function prettyStatus(value: string): string {
	return value.replaceAll('_', ' ').replace(/^./, character => character.toUpperCase());
}

function summarizeTool(name: string, args: Record<string, unknown>): string {
	if (name === 'run_command') return String(args.command ?? 'command');
	if (name === 'search_text') return `${String(args.query ?? '')} in ${String(args.path ?? '.')}`;
	if (name === 'apply_patch') return 'Apply file changes';
	if (name === 'git_status') return 'Read Git working-tree status';
	if (name === 'git_diff') return `Read ${String(args.scope ?? 'worktree')} Git diff`;
	if (name === 'move_path') return `${String(args.source ?? '')} → ${String(args.destination ?? '')}`;
	if (name === 'delete_path') return `Delete ${String(args.path ?? '')}`;
	if (name === 'request_user_input') return String(args.question ?? 'Ask the user');
	return String(args.path ?? '.');
}

function errorText(payload: Record<string, unknown>): string {
	const error = asRecord(payload.error);
	return String(error.message ?? 'Task failed');
}

function transcriptItems(value: unknown): TranscriptItem[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((entry, index): TranscriptItem[] => {
		const record = asRecord(entry);
		const role = record.role;
		const text = record.content;
		if (typeof text !== 'string' || (role !== 'user' && role !== 'assistant')) return [];
		return role === 'user'
			? [{kind: 'user', id: `history-user-${index}`, text}]
			: [{kind: 'assistant', id: `history-assistant-${index}`, text, finished: true}];
	});
}

function sessionSummaries(value: unknown): SessionSummary[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap(entry => {
		const record = asRecord(entry);
		if (typeof record.id !== 'string' || typeof record.title !== 'string') return [];
		return [{
			id: record.id,
			title: record.title,
			createdAt: String(record.createdAt ?? ''),
			updatedAt: String(record.updatedAt ?? ''),
			messageCount: Number(record.messageCount ?? 0),
		}];
	});
}

function statusReport(payload: Record<string, unknown>): StatusReport {
	const context = asRecord(payload.context);
	const tokenUsage = asRecord(payload.tokenUsage);
	const latestValue = tokenUsage.latest;
	const latest = latestValue && typeof latestValue === 'object'
		? tokenUsageRecord(asRecord(latestValue))
		: undefined;
	const latestMeasuredValue = tokenUsage.latestMeasured;
	const latestMeasured = latestMeasuredValue && typeof latestMeasuredValue === 'object'
		? tokenUsageRecord(asRecord(latestMeasuredValue))
		: undefined;
	const metadata = asRecord(payload.metadata);
	return {
		model: String(payload.model ?? ''),
		workspaceRoot: String(payload.workspaceRoot ?? ''),
		coreSessionId: String(payload.coreSessionId ?? ''),
		conversationId: String(payload.conversationId ?? ''),
		context: {
			totalChars: Number(context.totalChars ?? 0),
			requestChars: Number(context.requestChars ?? 0),
			maxChars: Number(context.maxChars ?? 0),
			messageCount: Number(context.messageCount ?? 0),
			requestMessageCount: Number(context.requestMessageCount ?? 0),
			truncated: Boolean(context.truncated),
		},
		tokenUsage: {
			contextWindowTokens: optionalPositiveNumber(tokenUsage.contextWindowTokens),
			requestCount: Number(tokenUsage.requestCount ?? 0),
			measuredRequests: Number(tokenUsage.measuredRequests ?? 0),
			unavailableRequests: Number(tokenUsage.unavailableRequests ?? 0),
			latest,
			latestMeasured,
			totals: tokenTotals(asRecord(tokenUsage.totals)),
		},
		metadata: Object.fromEntries(
			Object.entries(metadata).filter((entry): entry is [string, string | number | boolean] =>
				!['titleSource', 'userTurns', 'persistedMessages', 'commandTimeoutMs', 'protocolVersion'].includes(entry[0]) &&
				['string', 'number', 'boolean'].includes(typeof entry[1])),
		),
	};
}

function optionalPositiveNumber(value: unknown): number | undefined {
	const number = Number(value);
	return Number.isFinite(number) && number > 0 ? number : undefined;
}

function tokenUsageRecord(value: Record<string, unknown>): TokenUsageRecord {
	return {
		...tokenTotals(value),
		available: Boolean(value.available),
		recordedAt: String(value.recordedAt ?? ''),
		turnId: String(value.turnId ?? ''),
		step: Number(value.step ?? 0),
	};
}

function tokenTotals(value: Record<string, unknown>): TokenTotals {
	return {
		promptTokens: Number(value.promptTokens ?? 0),
		completionTokens: Number(value.completionTokens ?? 0),
		totalTokens: Number(value.totalTokens ?? 0),
		cachedTokens: Number(value.cachedTokens ?? 0),
		reasoningTokens: Number(value.reasoningTokens ?? 0),
	};
}
