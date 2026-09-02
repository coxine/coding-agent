import {EventEmitter} from 'node:events';
import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import {CoreEvent, isCoreEvent, message} from './protocol.js';

type CoreClientOptions = {
	repositoryRoot: string;
	workspaceRoot: string;
	model?: string;
	baseUrl?: string;
};

export class CoreClient extends EventEmitter {
	private process?: ChildProcessWithoutNullStreams;
	private buffer = '';
	private sessionId?: string;
	private activeTurnId?: string;

	constructor(private readonly options: CoreClientOptions) {
		super();
	}

	start(): void {
		this.process = spawn('uv', ['run', 'python', '-m', 'agent_coder'], {
			cwd: this.options.repositoryRoot,
			env: process.env,
			stdio: ['pipe', 'pipe', 'pipe'],
		});
		this.process.stdout.setEncoding('utf8');
		this.process.stderr.setEncoding('utf8');
		this.process.stdout.on('data', (chunk: string) => this.consume(chunk));
		this.process.stderr.on('data', (chunk: string) => this.emit('debug', sanitize(chunk)));
		this.process.on('error', error => this.emit('fatal', `Could not start Agent Core: ${error.message}`));
		this.process.on('exit', (code, signal) => {
			this.emit('exit', {code, signal});
		});

		this.send(
			message('initialize', {
				workspaceRoot: this.options.workspaceRoot,
				...(this.options.model ? {model: this.options.model} : {}),
				...(this.options.baseUrl ? {baseUrl: this.options.baseUrl} : {}),
				options: {maxSteps: 1000, commandTimeoutMs: 30_000},
			}),
		);
	}

	submit(text: string, turnId: string): void {
		if (!this.sessionId) throw new Error('Agent Core is not initialized');
		this.activeTurnId = turnId;
		this.send(message('submit_task', {text}, {sessionId: this.sessionId, turnId}));
	}

	approve(toolCallId: string, allowed: boolean): void {
		if (!this.sessionId || !this.activeTurnId) return;
		this.send(
			message(
				'approval_response',
				{decision: allowed ? 'allow_once' : 'deny'},
				{sessionId: this.sessionId, turnId: this.activeTurnId, toolCallId},
			),
		);
	}

	answerQuestion(toolCallId: string, answer?: string): void {
		if (!this.sessionId || !this.activeTurnId) return;
		this.send(
			message(
				'user_input_response',
				answer === undefined ? {cancelled: true} : {answer},
				{sessionId: this.sessionId, turnId: this.activeTurnId, toolCallId},
			),
		);
	}

	cancel(): void {
		if (!this.sessionId || !this.activeTurnId) return;
		this.send(
			message(
				'cancel_turn',
				{reason: 'user_requested'},
				{sessionId: this.sessionId, turnId: this.activeTurnId},
			),
		);
	}

	pause(): void {
		if (!this.sessionId || !this.activeTurnId) return;
		this.send(message('pause_turn', {}, {sessionId: this.sessionId, turnId: this.activeTurnId}));
	}

	resume(): void {
		if (!this.sessionId || !this.activeTurnId) return;
		this.send(message('resume_turn', {}, {sessionId: this.sessionId, turnId: this.activeTurnId}));
	}

	listSessions(): void {
		if (!this.sessionId) throw new Error('Agent Core is not initialized');
		this.send(message('list_sessions', {}, {sessionId: this.sessionId}));
	}

	requestStatus(): void {
		if (!this.sessionId) throw new Error('Agent Core is not initialized');
		this.send(message('get_status', {}, {sessionId: this.sessionId}));
	}

	switchSession(conversationId: string): void {
		if (!this.sessionId) throw new Error('Agent Core is not initialized');
		this.send(message('switch_session', {conversationId}, {sessionId: this.sessionId}));
	}

	createSession(): void {
		if (!this.sessionId) throw new Error('Agent Core is not initialized');
		this.send(message('create_session', {}, {sessionId: this.sessionId}));
	}

	renameSession(name: string): void {
		if (!this.sessionId) throw new Error('Agent Core is not initialized');
		this.send(message('rename_session', {name}, {sessionId: this.sessionId}));
	}

	deleteSession(conversationId: string): void {
		if (!this.sessionId) throw new Error('Agent Core is not initialized');
		this.send(message('delete_session', {conversationId}, {sessionId: this.sessionId}));
	}

	shutdown(): void {
		if (!this.process || this.process.killed) return;
		if (this.sessionId) this.send(message('shutdown', {}, {sessionId: this.sessionId}));
		else this.process.kill('SIGTERM');
	}

	forceKill(): void {
		this.process?.kill('SIGTERM');
	}

	private send(value: CoreEvent): void {
		if (!this.process?.stdin.writable) throw new Error('Agent Core stdin is not writable');
		this.process.stdin.write(`${JSON.stringify(value)}\n`);
	}

	private consume(chunk: string): void {
		this.buffer += chunk;
		for (;;) {
			const newline = this.buffer.indexOf('\n');
			if (newline < 0) break;
			const line = this.buffer.slice(0, newline).trim();
			this.buffer = this.buffer.slice(newline + 1);
			if (!line) continue;
			try {
				const parsed: unknown = JSON.parse(line);
				if (!isCoreEvent(parsed)) {
					this.emit('protocolError', 'Agent Core returned an invalid protocol event');
					continue;
				}
				if (parsed.type === 'initialized') this.sessionId = parsed.sessionId;
				if (['turn_finished', 'turn_failed', 'turn_cancelled'].includes(parsed.type)) {
					this.activeTurnId = undefined;
				}
				this.emit('event', parsed);
			} catch (error) {
				this.emit('protocolError', `Invalid JSON from Agent Core: ${String(error)}`);
			}
		}
	}
}

function sanitize(text: string): string {
	return text.replaceAll(/\u001B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '');
}
