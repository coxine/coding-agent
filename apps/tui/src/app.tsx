import React, { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { Box, Text, useApp, useInput } from 'ink';
import { randomUUID } from 'node:crypto';
import { CoreClient } from './core-client.js';
import { matchingCommands, parseSlashCommand, SlashCommand } from './commands.js';
import { MarkdownText } from './markdown.js';
import { CoreEvent } from './protocol.js';
import { Approval, AssistantView, initialState, Question, reducer, SessionSummary, StatusReport, ToolView, TranscriptItem } from './state.js';
import { tokenBarSegments, TokenSegmentKind } from './token-bar.js';

type AppProps = {
	repositoryRoot: string;
	workspaceRoot: string;
	model: string;
	baseUrl?: string;
};

export function App({ repositoryRoot, workspaceRoot, model, baseUrl }: AppProps): React.ReactNode {
	const { exit } = useApp();
	const [state, dispatch] = useReducer(reducer, initialState(workspaceRoot, model));
	const [input, setInput] = useState('');
	const [approvalChoice, setApprovalChoice] = useState<'allow' | 'deny'>('deny');
	const [cancelling, setCancelling] = useState(false);
	const [sessionChoice, setSessionChoice] = useState(0);
	const [commandChoice, setCommandChoice] = useState(0);
	const [questionAnswer, setQuestionAnswer] = useState('');
	const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
	const [compacting, setCompacting] = useState(false);
	const clientRef = useRef<CoreClient | null>(null);

	useEffect(() => {
		const client = new CoreClient({ repositoryRoot, workspaceRoot, model, baseUrl });
		clientRef.current = client;
		const onEvent = (event: CoreEvent) => {
			dispatch({ type: 'core_event', event });
			if (['turn_finished', 'turn_failed', 'turn_cancelled'].includes(event.type)) setCancelling(false);
			if (event.type === 'context_compacted' || event.type === 'error') setCompacting(false);
			if (event.type === 'shutdown_complete') exit();
		};
		const onFatal = (message: string) => dispatch({ type: 'fatal', message });
		const onProtocolError = (message: string) => dispatch({ type: 'notice', message, level: 'error' });
		const onExit = ({ code }: { code: number | null }) => {
			if (code && code !== 0) dispatch({ type: 'fatal', message: `Agent Core exited with code ${code}` });
		};
		client.on('event', onEvent);
		client.on('fatal', onFatal);
		client.on('protocolError', onProtocolError);
		client.on('exit', onExit);
		client.start();
		return () => {
			client.removeAllListeners();
			client.forceKill();
		};
	}, [baseUrl, exit, model, repositoryRoot, workspaceRoot]);

	useEffect(() => {
		if (state.pendingApproval) setApprovalChoice('deny');
	}, [state.pendingApproval?.toolCallId]);

	useEffect(() => {
		setQuestionAnswer('');
	}, [state.pendingQuestion?.toolCallId]);

	useEffect(() => {
		if (!state.sessionPickerOpen) return;
		const activeIndex = state.sessions.findIndex(session => session.id === state.conversationId);
		setSessionChoice(activeIndex < 0 ? 0 : activeIndex);
	}, [state.conversationId, state.sessionPickerOpen, state.sessions]);

	useEffect(() => {
		if (!state.sessionPickerOpen) setConfirmingDelete(null);
	}, [state.sessionPickerOpen]);

	const canSubmit = state.connection === 'ready' && !state.activeTurnId;
	const commands = useMemo(() => matchingCommands(input), [input]);
	const commandPaletteOpen = canSubmit && commands.length > 0;
	const hasReasoning = state.items.some(item => item.kind === 'assistant' && Boolean(state.assistants[item.id]?.reasoning));

	useEffect(() => {
		setCommandChoice(0);
	}, [input]);

	useInput((character, key) => {
		const client = clientRef.current;
		if (!client) return;

		if (state.pendingApproval) {
			if (character.toLowerCase() === 'y' || key.leftArrow) setApprovalChoice('allow');
			if (character.toLowerCase() === 'n' || key.rightArrow || key.tab) setApprovalChoice('deny');
			if (key.return) client.approve(state.pendingApproval.toolCallId, approvalChoice === 'allow');
			if (key.escape) client.approve(state.pendingApproval.toolCallId, false);
			return;
		}

		if (state.pendingQuestion) {
			if (key.ctrl && character === 'c') {
				client.cancel();
				setCancelling(true);
				return;
			}
			if (key.escape) {
				client.answerQuestion(state.pendingQuestion.toolCallId);
				return;
			}

		if (key.return) {
				if (key.meta || key.ctrl) {
					setQuestionAnswer(value => `${value}\n`);
					return;
				}
				const answer = questionAnswer.trim();
				if (answer) client.answerQuestion(state.pendingQuestion.toolCallId, answer);
				return;
			}
			if (key.backspace || key.delete) {
				setQuestionAnswer(value => value.slice(0, -1));
				return;
			}
			if (character && !key.ctrl && !key.meta) {
				setQuestionAnswer(value => value + sanitize(character));
			}
			return;
		}

		if (state.statusReport) {
			if (key.escape || key.return) dispatch({ type: 'close_status' });
			return;
		}

		if (state.sessionPickerOpen) {
			if (confirmingDelete) {
				if (character.toLowerCase() === 'y') {
					client.deleteSession(confirmingDelete);
					setConfirmingDelete(null);
				} else if (character.toLowerCase() === 'n' || key.escape) {
					setConfirmingDelete(null);
				}
				return;
			}
			if (key.escape) dispatch({ type: 'close_session_picker' });
			if (key.upArrow) setSessionChoice(value => Math.max(0, value - 1));
			if (key.downArrow) setSessionChoice(value => Math.min(state.sessions.length - 1, value + 1));
			if (character.toLowerCase() === 'n') client.createSession();
			if (character.toLowerCase() === 'd' && state.sessions[sessionChoice]) {
				setConfirmingDelete(state.sessions[sessionChoice].id);
				return;
			}
			if (key.return && state.sessions[sessionChoice]) client.switchSession(state.sessions[sessionChoice].id);
			return;
		}

		if (key.ctrl && character === 'r') {
			dispatch({ type: 'toggle_reasoning' });
			return;
		}

		if (key.ctrl && character === 'c') {
			if (state.activeTurnId) {
				if (cancelling) {
					client.forceKill();
					exit();
				} else {
					client.cancel();
					setCancelling(true);
				}
			} else {
				client.shutdown();
				setTimeout(() => exit(), 500);
			}
			return;
		}
		if (key.escape && state.activeTurnId) {
			if (state.paused) client.resume();
			else client.pause();
			return;
		}
		if (!canSubmit) return;

		if (commandPaletteOpen) {
			if (key.escape) {
				setInput('');
				return;
			}
			if (key.upArrow) {
				setCommandChoice(value => Math.max(0, value - 1));
				return;
			}
			if (key.downArrow || key.tab) {
				setCommandChoice(value => Math.min(commands.length - 1, value + 1));
				return;
			}
			if (key.return && commands[commandChoice]) {
				const command = commands[commandChoice];
				if (command.name === '/rename') setInput('/rename ');
				else {
					runSlashCommand(command.name, '', client);
					if (command.name === '/compact') setCompacting(true);
					setInput('');
				}
				return;
			}
		}

		if (key.return) {
			if (key.meta || key.ctrl) {
				setInput(value => `${value}\n`);
				return;
			}
			const text = input.trim();
			if (!text) return;
			if (text.startsWith('/')) {
				const invocation = parseSlashCommand(text);
				if (!invocation) dispatch({ type: 'notice', message: `Unknown command: ${text}`, level: 'error' });
				else {
					const error = runSlashCommand(invocation.command.name, invocation.argument, client);
					if (error) dispatch({ type: 'notice', message: error, level: 'error' });
					else if (invocation.command.name === '/compact') setCompacting(true);
				}
				setInput('');
				return;
			}
			const turnId = `turn_${randomUUID().replaceAll('-', '')}`;
			try {
				client.submit(text, turnId);
				dispatch({ type: 'submitted', turnId, text });
				setInput('');
			} catch (error) {
				dispatch({ type: 'notice', message: String(error), level: 'error' });
			}
			return;
		}
		if (key.backspace || key.delete) {
			setInput(value => value.slice(0, -1));
			return;
		}
		if (character && !key.ctrl && !key.meta) setInput(value => value + sanitize(character));
	});

	return (
		<Box flexDirection="column" paddingX={1}>
			<Header model={state.model} workspace={state.workspaceRoot} conversation={state.conversationTitle} status={cancelling ? 'Cancelling…' : state.status} step={state.step} />
			<Box flexDirection="column" marginY={1}>
				{state.items.length === 0 ? (
					<Text dimColor>Describe a coding task. The agent can inspect, edit, and test this workspace.</Text>
				) : (
					state.items.map(item => <Transcript key={`${item.kind}-${item.id}`} item={item} tool={item.kind === 'tool' ? state.tools[item.id] : undefined} assistant={item.kind === 'assistant' ? state.assistants[item.id] : undefined} showReasoning={state.showReasoning} />)
				)}
			</Box>
			{state.pendingApproval ? (
				<ApprovalDialog approval={state.pendingApproval} choice={approvalChoice} />
			) : state.pendingQuestion ? (
				<QuestionDialog question={state.pendingQuestion} answer={questionAnswer} />
			) : state.statusReport ? (
				<StatusPanel report={state.statusReport} />
			) : state.sessionPickerOpen ? (
				<SessionPicker sessions={state.sessions} activeId={state.conversationId} choice={sessionChoice} confirmingDelete={confirmingDelete} />
			) : state.connection === 'fatal' ? (
				<Box borderStyle="round" borderColor="red" paddingX={1} flexDirection="column">
					<Text bold color="red">Agent Core failed</Text>
					<Text>{state.fatalError}</Text>
				</Box>
			) : (
				<Box flexDirection="column">
					{commandPaletteOpen && <CommandPalette commands={commands} choice={commandChoice} />}
					{compacting && <Spinner label="Compacting context…" />}
					<Composer value={input} enabled={canSubmit && !compacting} />
				</Box>
			)}
			<Footer active={Boolean(state.activeTurnId)} paused={state.paused} approval={Boolean(state.pendingApproval)} question={Boolean(state.pendingQuestion)} sessions={state.sessionPickerOpen} status={Boolean(state.statusReport)} reasoning={hasReasoning} showReasoning={state.showReasoning} />
		</Box>
	);
}

function Header({ model, workspace, conversation, status, step }: { model: string; workspace: string; conversation: string; status: string; step: number }): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="single" borderColor="cyan" paddingX={1}>
			<Text bold color="cyan">coding-agent</Text>
			<Text dimColor>{model || 'model not configured'} • {shortPath(workspace)} • {conversation} • {step ? `step ${step} • ` : ''}{status}</Text>
		</Box>
	);
}

export function Transcript({ item, tool, assistant, showReasoning = false }: { item: TranscriptItem; tool?: ToolView; assistant?: AssistantView; showReasoning?: boolean }): React.ReactNode {
	if (item.kind === 'user') {
		return (
			<Box width="100%" backgroundColor="#eeeeee" paddingX={1} marginBottom={1}>
				<Text color="#303030"><Text color="#888888">› </Text>{item.text}</Text>
			</Box>
		);
	}
	if (item.kind === 'assistant') {
		if (!assistant) return null;
		const hasReasoning = Boolean(assistant.reasoning);
		if (!assistant.text && !assistant.finished) {
			return (
				<Box flexDirection="column" marginBottom={1}>
					{hasReasoning && showReasoning ? <ReasoningBlock text={assistant.reasoning} /> : null}
					<Spinner />
				</Box>
			);
		}
		if (!assistant.text && !hasReasoning) return null;
		return (
			<Box flexDirection="column" marginBottom={1}>
				{hasReasoning ? (
					showReasoning ? <ReasoningBlock text={assistant.reasoning} /> : <ReasoningHidden />
				) : null}
				{assistant.text ? <MarkdownText>{assistant.text}</MarkdownText> : null}
			</Box>
		);
	}
	if (item.kind === 'notice') {
		return <Box marginBottom={1}><Text color={item.level === 'error' ? 'red' : 'yellow'}>{item.level === 'error' ? 'Error' : 'Info'}: {item.text}</Text></Box>;
	}
	return tool ? <ToolCard tool={tool} /> : null;
}

function ToolCard({ tool }: { tool: ToolView }): React.ReactNode {
	const icon = { requested: '○', waiting_approval: '?', waiting_input: '?', running: '…', succeeded: '✓', failed: '✗', denied: '⊘', cancelled: '■' }[tool.status];
	const color = tool.status === 'succeeded' ? 'green' : ['failed', 'denied'].includes(tool.status) ? 'red' : 'yellow';
	const output = sanitize(tool.output).split('\n').slice(-6).join('\n');
	const diff = sanitize(tool.diff ?? '').split('\n').slice(0, 18).join('\n');
	return (
		<Box flexDirection="column" borderStyle="round" borderColor={color} paddingX={1} marginBottom={1}>
			<Text><Text color={color}>{icon}</Text> <Text bold>{tool.name}</Text>  <Text dimColor>{tool.summary}{tool.durationMs !== undefined ? ` • ${tool.durationMs} ms` : ''}</Text></Text>
			{tool.status === 'running' && <Spinner />}
			{output && <Text dimColor>{output}</Text>}
			{diff && <Text>{diff}</Text>}
			{tool.error && <Text color="red">{tool.error}</Text>}
		</Box>
	);
}

function ApprovalDialog({ approval, choice }: { approval: Approval; choice: 'allow' | 'deny' }): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="double" borderColor="yellow" paddingX={1}>
			<Text bold color="yellow">Approval required: {approval.name}</Text>
			<Text>{approval.summary}</Text>
			<Text dimColor>{approval.reason}</Text>
			<Text>{JSON.stringify(approval.arguments, null, 2)}</Text>
			<Box marginTop={1} gap={2}>
				<Text inverse={choice === 'allow'} color="green"> <Text color="gray">Y</Text> Allow once </Text>
				<Text inverse={choice === 'deny'} color="red"> <Text color="gray">N</Text> Deny </Text>
			</Box>
		</Box>
	);
}

function QuestionDialog({ question, answer }: { question: Question; answer: string }): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="double" borderColor="cyan" paddingX={1}>
			<Text bold color="cyan">Agent needs your input</Text>
			<Text>{question.question}</Text>
			<Box marginTop={1} borderStyle="round" borderColor="blue" paddingX={1}>
				<Text color="blue">{'> '}</Text>
				<Text>{answer}<Text inverse> </Text></Text>
			</Box>
		</Box>
	);
}

function SessionPicker({ sessions, activeId, choice, confirmingDelete }: { sessions: SessionSummary[]; activeId?: string; choice: number; confirmingDelete: string | null }): React.ReactNode {
	const selected = sessions[choice];
	return (
		<Box flexDirection="column" borderStyle="double" borderColor="magenta" paddingX={1}>
			<Text bold color="magenta">Sessions</Text>
			{sessions.map((session, index) => (
				<Text key={session.id} inverse={index === choice}>
					{index === choice ? '› ' : '  '}{session.id === activeId ? '● ' : '  '}{session.title} <Text dimColor>({session.messageCount} turns • {formatDate(session.updatedAt)})</Text>
				</Text>
			))}
			{confirmingDelete && selected ? (
				<Text color="red">Delete "{selected.title}"? <Text bold>y</Text> to confirm, <Text bold>n</Text> to cancel.</Text>
			) : (
				<HintLine hints={[
					{ key: '↑/↓', label: 'select' },
					{ key: 'enter', label: 'switch' },
					{ key: 'n', label: 'new' },
					{ key: 'd', label: 'delete' },
					{ key: 'esc', label: 'close' },
				]} />
			)}
		</Box>
	);
}

function StatusPanel({ report }: { report: StatusReport }): React.ReactNode {
	const latest = report.tokenUsage.latestMeasured;
	const contextLimit = report.tokenUsage.contextWindowTokens;
	return (
		<Box flexDirection="column" borderStyle="double" borderColor="cyan" paddingX={1}>
			<Text bold color="cyan">coding-agent status</Text>
			<Box flexDirection="column">
				<StatusRow label="Model" value={report.model} />
				<StatusRow label="Directory" value={shortPath(report.workspaceRoot)} />
				<StatusRow label="Conversation" value={report.conversationId} />
				<StatusRow label="Core session" value={report.coreSessionId} />
			</Box>
			<Box flexDirection="column" marginTop={1}>
				<Text bold>API token usage</Text>
				{latest?.available ? (
					<TokenUsageChart
						title="Session distribution"
						usage={latest}
						contextWindowTokens={contextLimit}
					/>
				) : (
					<Text dimColor>No measured token usage</Text>
				)}
			</Box>
			<Box flexDirection="column" marginTop={1}>
				<Text bold>Metadata</Text>
				{Object.entries(report.metadata).map(([key, value]) => (
					<StatusRow key={key} label={metadataLabel(key)} value={formatMetadata(key, value)} />
				))}
			</Box>
			<HintLine hints={[{ key: 'enter/esc', label: 'close' }]} />
		</Box>
	);
}

function TokenUsageChart({
	title,
	usage,
	contextWindowTokens,
	detail,
}: {
	title: string;
	usage: { promptTokens: number; completionTokens: number; totalTokens: number; cachedTokens: number; reasoningTokens: number };
	contextWindowTokens?: number;
	detail?: string;
}): React.ReactNode {
	const used = usage.promptTokens + usage.completionTokens;
	const percentage = contextWindowTokens
		? Math.round((used / contextWindowTokens) * 1000) / 10
		: undefined;
	const capacity = contextWindowTokens
		? `${formatNumber(used)} / ${formatNumber(contextWindowTokens)} tokens (${percentage}%)`
		: `${formatNumber(used)} tokens`;
	const segments = tokenBarSegments(usage, 42, contextWindowTokens);
	return (
		<Box flexDirection="column">
			<Text>{title} <Text dimColor>• {capacity}{detail ? ` • ${detail}` : ''}</Text></Text>
			<Text>
				{'['}
				{segments.map(segment => segment.width > 0 && (
					<Text key={segment.kind} color={TOKEN_COLORS[segment.kind]}>
						{(segment.kind === 'remaining' ? '░' : '█').repeat(segment.width)}
					</Text>
				))}
				{']'}
			</Text>
			<TokenLegend usage={usage} showRemaining={Boolean(contextWindowTokens && used < contextWindowTokens)} />
		</Box>
	);
}

const TOKEN_COLORS: Record<TokenSegmentKind, 'blue' | 'cyan' | 'green' | 'magenta' | 'gray'> = {
	input: 'blue',
	cached: 'cyan',
	output: 'green',
	reasoning: 'magenta',
	remaining: 'gray',
};

function TokenLegend({
	usage,
	showRemaining,
}: {
	usage: { promptTokens: number; completionTokens: number; cachedTokens: number; reasoningTokens: number };
	showRemaining: boolean;
}): React.ReactNode {
	const cached = Math.min(usage.promptTokens, usage.cachedTokens);
	const reasoning = Math.min(usage.completionTokens, usage.reasoningTokens);
	const items = [
		{ kind: 'input' as const, label: 'input', value: Math.max(0, usage.promptTokens - cached) },
		{ kind: 'cached' as const, label: 'cached', value: cached },
		{ kind: 'output' as const, label: 'output', value: Math.max(0, usage.completionTokens - reasoning) },
		{ kind: 'reasoning' as const, label: 'reasoning', value: reasoning },
	].filter(item => item.value > 0);
	return (
		<Text dimColor>
			{items.map((item, index) => (
				<React.Fragment key={item.kind}>
					{index > 0 && '  '}
					<Text color={TOKEN_COLORS[item.kind]}>■</Text> {item.label} {formatNumber(item.value)}
				</React.Fragment>
			))}
			{showRemaining && <><Text color={TOKEN_COLORS.remaining}>  ░</Text> remaining</>}
		</Text>
	);
}

function StatusRow({ label, value }: { label: string; value: string }): React.ReactNode {
	return <Text><Text dimColor>{`${label}:`.padEnd(20)}</Text>{value}</Text>;
}

function CommandPalette({ commands, choice }: { commands: SlashCommand[]; choice: number }): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="round" borderColor="blue" paddingX={1} marginBottom={1}>
			<Text bold color="blue">Commands</Text>
			{commands.map((command, index) => (
				<Text key={command.name} inverse={index === choice}>
					{index === choice ? '› ' : '  '}<Text bold color="gray">{command.name}</Text> <Text dimColor>— {command.description}</Text>
				</Text>
			))}
			<HintLine prefix="type to filter" hints={[
				{ key: '↑/↓', label: 'select' },
				{ key: 'enter', label: 'run' },
				{ key: 'esc', label: 'close' },
			]} />
		</Box>
	);
}

export function Composer({ value, enabled }: { value: string; enabled: boolean }): React.ReactNode {
	return (
		<Box borderStyle="round" borderColor={enabled ? 'blue' : 'gray'} paddingX={1}>
			<Text color={enabled ? 'blue' : 'gray'}>{'> '}</Text>
			{enabled ? (
				value ? (
					<Text>{value}<Text inverse> </Text></Text>
				) : (
					<Text><Text inverse> </Text><Text dimColor>Type a task…</Text></Text>
				)
			) : (
				<Text dimColor>Agent is working…</Text>
			)}
		</Box>
	);
}

function ReasoningBlock({ text }: { text: string }): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="round" borderColor="magenta" paddingX={1} marginBottom={1}>
			<Text bold color="magenta">Reasoning</Text>
			<Text dimColor>{sanitize(text)}</Text>
		</Box>
	);
}

function ReasoningHidden(): React.ReactNode {
	return <Text dimColor>Reasoning hidden — Ctrl+R to show</Text>;
}

function Footer({ active, paused, approval, question, sessions, status, reasoning, showReasoning }: { active: boolean; paused: boolean; approval: boolean; question: boolean; sessions: boolean; status: boolean; reasoning: boolean; showReasoning: boolean }): React.ReactNode {
	const reasoningHint: Hint | undefined = reasoning
		? { key: 'ctrl+r', label: showReasoning ? 'hide reasoning' : 'show reasoning' }
		: undefined;
	const hints: Hint[] = approval
		? [
			{ key: 'y', label: 'allow' },
			{ key: 'n', label: 'deny' },
			{ key: 'enter', label: 'confirm' },
			{ key: 'esc', label: 'deny' },
		]
		: question
			? [
				{ key: 'enter', label: 'answer' },
				{ key: 'ctrl+enter', label: 'newline' },
				{ key: 'esc', label: 'skip' },
				{ key: 'ctrl+c', label: 'cancel task' },
			]
			: status
				? [{ key: 'enter/esc', label: 'close status' }]
				: sessions
					? [
						{ key: '↑/↓', label: 'select' },
						{ key: 'enter', label: 'switch' },
						{ key: 'n', label: 'new' },
						{ key: 'esc', label: 'close' },
					]
					: active
						? [
							{ key: 'esc', label: paused ? 'resume' : 'pause' },
							{ key: 'ctrl+c', label: 'cancel task' },
							...(reasoningHint ? [reasoningHint] : []),
						]
						: [
							{ key: 'enter', label: 'send' },
							{ key: '/session', label: 'history' },
							{ key: '/status', label: 'info' },
							{ key: 'ctrl+enter', label: 'newline' },
							{ key: 'ctrl+c', label: 'exit' },
							...(reasoningHint ? [reasoningHint] : []),
						];
	return <Box marginTop={1}><HintLine hints={hints} /></Box>;
}

type Hint = { key: string; label: string };

function HintLine({ hints, prefix }: { hints: Hint[]; prefix?: string }): React.ReactNode {
	return (
		<Text>
			{prefix && <Text dimColor>{prefix} • </Text>}
			{hints.map((hint, index) => (
				<React.Fragment key={`${hint.key}-${hint.label}`}>
					{index > 0 && <Text dimColor> • </Text>}
					<Text color="gray">{hint.key}</Text>
					<Text dimColor> {hint.label}</Text>
				</React.Fragment>
			))}
		</Text>
	);
}

function formatDate(value: string): string {
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function runSlashCommand(name: string, argument: string, client: CoreClient): string | undefined {
	if (name === '/rename') {
		if (!argument) return 'Usage: /rename <name>';
		client.renameSession(argument);
		return;
	}
	if (argument) return `${name} does not accept arguments.`;
	if (name === '/session') client.listSessions();
	if (name === '/status') client.requestStatus();
	if (name === '/compact') client.compactContext();
}

function Spinner({label = 'working'}: {label?: string}): React.ReactNode {
	const frames = useMemo(() => ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'], []);
	const [index, setIndex] = useState(0);
	useEffect(() => {
		const timer = setInterval(() => setIndex(value => (value + 1) % frames.length), 80);
		return () => clearInterval(timer);
	}, [frames]);
	return <Text color="yellow">{frames[index]} {label}</Text>;
}

function sanitize(text: string): string {
	return text.replaceAll(/\u001B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '');
}

function shortPath(path: string): string {
	const home = process.env.HOME;
	return home && path.startsWith(home) ? `~${path.slice(home.length)}` : path;
}

function formatNumber(value: number): string {
	return Number.isFinite(value) ? value.toLocaleString('en-US') : '0';
}

function metadataLabel(key: string): string {
	return key.replaceAll(/([A-Z])/g, ' $1').replace(/^./, character => character.toUpperCase());
}

function formatMetadata(key: string, value: string | number | boolean): string {
	if ((key === 'createdAt' || key === 'updatedAt') && typeof value === 'string') {
		return formatDate(value);
	}
	if (typeof value === 'number') return formatNumber(value);
	return String(value);
}
