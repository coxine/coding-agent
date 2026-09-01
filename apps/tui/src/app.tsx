import React, {useEffect, useMemo, useReducer, useRef, useState} from 'react';
import {Box, Text, useApp, useInput} from 'ink';
import {randomUUID} from 'node:crypto';
import {CoreClient} from './core-client.js';
import {matchingCommands, SlashCommand} from './commands.js';
import {MarkdownText} from './markdown.js';
import {CoreEvent} from './protocol.js';
import {Approval, initialState, reducer, SessionSummary, ToolView, TranscriptItem} from './state.js';

type AppProps = {
	repositoryRoot: string;
	workspaceRoot: string;
	model: string;
	baseUrl?: string;
};

export function App({repositoryRoot, workspaceRoot, model, baseUrl}: AppProps): React.ReactNode {
	const {exit} = useApp();
	const [state, dispatch] = useReducer(reducer, initialState(workspaceRoot, model));
	const [input, setInput] = useState('');
	const [approvalChoice, setApprovalChoice] = useState<'allow' | 'deny'>('deny');
	const [cancelling, setCancelling] = useState(false);
	const [sessionChoice, setSessionChoice] = useState(0);
	const [commandChoice, setCommandChoice] = useState(0);
	const clientRef = useRef<CoreClient | null>(null);

	useEffect(() => {
		const client = new CoreClient({repositoryRoot, workspaceRoot, model, baseUrl});
		clientRef.current = client;
		const onEvent = (event: CoreEvent) => {
			dispatch({type: 'core_event', event});
			if (['turn_finished', 'turn_failed', 'turn_cancelled'].includes(event.type)) setCancelling(false);
			if (event.type === 'shutdown_complete') exit();
		};
		const onFatal = (message: string) => dispatch({type: 'fatal', message});
		const onProtocolError = (message: string) => dispatch({type: 'notice', message, level: 'error'});
		const onExit = ({code}: {code: number | null}) => {
			if (code && code !== 0) dispatch({type: 'fatal', message: `Agent Core exited with code ${code}`});
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
		if (!state.sessionPickerOpen) return;
		const activeIndex = state.sessions.findIndex(session => session.id === state.conversationId);
		setSessionChoice(activeIndex < 0 ? 0 : activeIndex);
	}, [state.conversationId, state.sessionPickerOpen, state.sessions]);

	const canSubmit = state.connection === 'ready' && !state.activeTurnId;
	const commands = useMemo(() => matchingCommands(input), [input]);
	const commandPaletteOpen = canSubmit && commands.length > 0;

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

		if (state.sessionPickerOpen) {
			if (key.escape) dispatch({type: 'close_session_picker'});
			if (key.upArrow) setSessionChoice(value => Math.max(0, value - 1));
			if (key.downArrow) setSessionChoice(value => Math.min(state.sessions.length - 1, value + 1));
			if (character.toLowerCase() === 'n') client.createSession();
			if (key.return && state.sessions[sessionChoice]) client.switchSession(state.sessions[sessionChoice].id);
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
			client.cancel();
			setCancelling(true);
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
				runSlashCommand(commands[commandChoice].name, client);
				setInput('');
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
				dispatch({type: 'notice', message: `Unknown command: ${text}`, level: 'error'});
				setInput('');
				return;
			}
			const turnId = `turn_${randomUUID().replaceAll('-', '')}`;
			try {
				client.submit(text, turnId);
				dispatch({type: 'submitted', turnId, text});
				setInput('');
			} catch (error) {
				dispatch({type: 'notice', message: String(error), level: 'error'});
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
					state.items.map(item => <Transcript key={`${item.kind}-${item.id}`} item={item} tool={item.kind === 'tool' ? state.tools[item.id] : undefined} />)
				)}
			</Box>
			{state.pendingApproval ? (
				<ApprovalDialog approval={state.pendingApproval} choice={approvalChoice} />
			) : state.sessionPickerOpen ? (
				<SessionPicker sessions={state.sessions} activeId={state.conversationId} choice={sessionChoice} />
			) : state.connection === 'fatal' ? (
				<Box borderStyle="round" borderColor="red" paddingX={1} flexDirection="column">
					<Text bold color="red">Agent Core failed</Text>
					<Text>{state.fatalError}</Text>
				</Box>
			) : (
				<Box flexDirection="column">
					{commandPaletteOpen && <CommandPalette commands={commands} choice={commandChoice} />}
					<Composer value={input} enabled={canSubmit} />
				</Box>
			)}
			<Footer active={Boolean(state.activeTurnId)} approval={Boolean(state.pendingApproval)} sessions={state.sessionPickerOpen} />
		</Box>
	);
}

function Header({model, workspace, conversation, status, step}: {model: string; workspace: string; conversation: string; status: string; step: number}): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="single" borderColor="cyan" paddingX={1}>
			<Text bold color="cyan">coding-agent</Text>
			<Text dimColor>{model || 'model not configured'} • {shortPath(workspace)} • {conversation} • {step ? `step ${step} • ` : ''}{status}</Text>
		</Box>
	);
}

function Transcript({item, tool}: {item: TranscriptItem; tool?: ToolView}): React.ReactNode {
	if (item.kind === 'user') {
		return <Box flexDirection="column" marginBottom={1}><Text bold color="green">You</Text><Text>{item.text}</Text></Box>;
	}
	if (item.kind === 'assistant') {
		if (!item.text && !item.finished) return <Box marginBottom={1}><Text color="cyan">Agent </Text><Spinner /></Box>;
		if (!item.text) return null;
		return <Box flexDirection="column" marginBottom={1}><Text bold color="cyan">Agent</Text><MarkdownText>{item.text}</MarkdownText></Box>;
	}
	if (item.kind === 'notice') {
		return <Box marginBottom={1}><Text color={item.level === 'error' ? 'red' : 'yellow'}>{item.level === 'error' ? 'Error' : 'Info'}: {item.text}</Text></Box>;
	}
	return tool ? <ToolCard tool={tool} /> : null;
}

function ToolCard({tool}: {tool: ToolView}): React.ReactNode {
	const icon = {requested: '○', waiting_approval: '?', running: '…', succeeded: '✓', failed: '✗', denied: '⊘', cancelled: '■'}[tool.status];
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

function ApprovalDialog({approval, choice}: {approval: Approval; choice: 'allow' | 'deny'}): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="double" borderColor="yellow" paddingX={1}>
			<Text bold color="yellow">Approval required: {approval.name}</Text>
			<Text>{approval.summary}</Text>
			<Text dimColor>{approval.reason}</Text>
			<Text>{JSON.stringify(approval.arguments, null, 2)}</Text>
			<Box marginTop={1} gap={2}>
				<Text inverse={choice === 'allow'} color="green"> Y Allow once </Text>
				<Text inverse={choice === 'deny'} color="red"> N Deny </Text>
			</Box>
		</Box>
	);
}

function SessionPicker({sessions, activeId, choice}: {sessions: SessionSummary[]; activeId?: string; choice: number}): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="double" borderColor="magenta" paddingX={1}>
			<Text bold color="magenta">Sessions</Text>
			{sessions.map((session, index) => (
				<Text key={session.id} inverse={index === choice}>
					{index === choice ? '› ' : '  '}{session.id === activeId ? '● ' : '  '}{session.title} <Text dimColor>({session.messageCount} turns • {formatDate(session.updatedAt)})</Text>
				</Text>
			))}
			<Text dimColor>↑/↓ select • enter switch • n new • esc close</Text>
		</Box>
	);
}

function CommandPalette({commands, choice}: {commands: SlashCommand[]; choice: number}): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="round" borderColor="blue" paddingX={1} marginBottom={1}>
			<Text bold color="blue">Commands</Text>
			{commands.map((command, index) => (
				<Text key={command.name} inverse={index === choice}>
					{index === choice ? '› ' : '  '}<Text bold>{command.name}</Text> <Text dimColor>— {command.description}</Text>
				</Text>
			))}
			<Text dimColor>type to filter • ↑/↓ select • enter run • esc close</Text>
		</Box>
	);
}

function Composer({value, enabled}: {value: string; enabled: boolean}): React.ReactNode {
	return (
		<Box borderStyle="round" borderColor={enabled ? 'blue' : 'gray'} paddingX={1}>
			<Text color={enabled ? 'blue' : 'gray'}>{'> '}</Text>
			<Text>{enabled ? value || 'Type a task…' : 'Agent is working…'}{enabled && <Text inverse> </Text>}</Text>
		</Box>
	);
}

function Footer({active, approval, sessions}: {active: boolean; approval: boolean; sessions: boolean}): React.ReactNode {
	const text = approval
		? 'y allow • n deny • enter confirm • esc deny'
		: sessions
			? '↑/↓ select • enter switch • n new • esc close'
		: active
			? 'esc cancel • ctrl+c cancel'
			: 'enter send • /session history • ctrl+enter newline • ctrl+c exit';
	return <Box marginTop={1}><Text dimColor>{text}</Text></Box>;
}

function formatDate(value: string): string {
	const date = new Date(value);
	return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function runSlashCommand(name: string, client: CoreClient): void {
	if (name === '/session') client.listSessions();
}

function Spinner(): React.ReactNode {
	const frames = useMemo(() => ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'], []);
	const [index, setIndex] = useState(0);
	useEffect(() => {
		const timer = setInterval(() => setIndex(value => (value + 1) % frames.length), 80);
		return () => clearInterval(timer);
	}, [frames]);
	return <Text color="yellow">{frames[index]} working</Text>;
}

function sanitize(text: string): string {
	return text.replaceAll(/\u001B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '');
}

function shortPath(path: string): string {
	const home = process.env.HOME;
	return home && path.startsWith(home) ? `~${path.slice(home.length)}` : path;
}
