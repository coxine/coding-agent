import React, {useEffect, useMemo, useReducer, useRef, useState} from 'react';
import {Box, Text, useApp, useInput} from 'ink';
import {randomUUID} from 'node:crypto';
import {CoreClient} from './core-client.js';
import {CoreEvent} from './protocol.js';
import {Approval, initialState, reducer, ToolView, TranscriptItem} from './state.js';

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

	const canSubmit = state.connection === 'ready' && !state.activeTurnId;

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

		if (key.return) {
			if (key.meta || key.ctrl) {
				setInput(value => `${value}\n`);
				return;
			}
			const text = input.trim();
			if (!text) return;
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
			<Header model={state.model} workspace={state.workspaceRoot} status={cancelling ? 'Cancelling…' : state.status} step={state.step} />
			<Box flexDirection="column" marginY={1}>
				{state.items.length === 0 ? (
					<Text dimColor>Describe a coding task. The agent can inspect, edit, and test this workspace.</Text>
				) : (
					state.items.map(item => <Transcript key={`${item.kind}-${item.id}`} item={item} tool={item.kind === 'tool' ? state.tools[item.id] : undefined} />)
				)}
			</Box>
			{state.pendingApproval ? (
				<ApprovalDialog approval={state.pendingApproval} choice={approvalChoice} />
			) : state.connection === 'fatal' ? (
				<Box borderStyle="round" borderColor="red" paddingX={1} flexDirection="column">
					<Text bold color="red">Agent Core failed</Text>
					<Text>{state.fatalError}</Text>
				</Box>
			) : (
				<Composer value={input} enabled={canSubmit} />
			)}
			<Footer active={Boolean(state.activeTurnId)} approval={Boolean(state.pendingApproval)} />
		</Box>
	);
}

function Header({model, workspace, status, step}: {model: string; workspace: string; status: string; step: number}): React.ReactNode {
	return (
		<Box flexDirection="column" borderStyle="single" borderColor="cyan" paddingX={1}>
			<Text bold color="cyan">coding-agent</Text>
			<Text dimColor>{model || 'model not configured'} • {shortPath(workspace)} • {step ? `step ${step} • ` : ''}{status}</Text>
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
		return <Box flexDirection="column" marginBottom={1}><Text bold color="cyan">Agent</Text><Text>{item.text}</Text></Box>;
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

function Composer({value, enabled}: {value: string; enabled: boolean}): React.ReactNode {
	return (
		<Box borderStyle="round" borderColor={enabled ? 'blue' : 'gray'} paddingX={1}>
			<Text color={enabled ? 'blue' : 'gray'}>{'> '}</Text>
			<Text>{enabled ? value || 'Type a task…' : 'Agent is working…'}{enabled && <Text inverse> </Text>}</Text>
		</Box>
	);
}

function Footer({active, approval}: {active: boolean; approval: boolean}): React.ReactNode {
	const text = approval
		? 'y allow • n deny • enter confirm • esc deny'
		: active
			? 'esc cancel • ctrl+c cancel'
			: 'enter send • ctrl+enter newline • ctrl+c exit';
	return <Box marginTop={1}><Text dimColor>{text}</Text></Box>;
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
