#!/usr/bin/env node
import React from 'react';
import {render} from 'ink';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {App} from './app.js';

type Options = {
	workspaceRoot: string;
	model: string;
	baseUrl?: string;
};

function parseArguments(argv: string[]): Options {
	let workspaceRoot = process.cwd();
	let model = process.env.AGENT_MODEL ?? '';
	let baseUrl = process.env.OPENAI_BASE_URL;
	for (let index = 0; index < argv.length; index += 1) {
		const argument = argv[index];
		if (argument === '--cwd') workspaceRoot = path.resolve(requireValue(argv, ++index, '--cwd'));
		else if (argument === '--model') model = requireValue(argv, ++index, '--model');
		else if (argument === '--base-url') baseUrl = requireValue(argv, ++index, '--base-url');
		else if (argument === '--help' || argument === '-h') {
			process.stdout.write('Usage: npm run dev -- [--cwd PATH] [--model NAME] [--base-url URL]\n');
			process.exit(0);
		} else {
			throw new Error(`Unknown argument: ${argument}`);
		}
	}
	return {workspaceRoot, model, baseUrl};
}

function requireValue(argv: string[], index: number, option: string): string {
	const value = argv[index];
	if (!value) throw new Error(`${option} requires a value`);
	return value;
}

const repositoryRoot = fileURLToPath(new URL('../../..', import.meta.url));

try {
	const options = parseArguments(process.argv.slice(2));
	render(
		<App
			repositoryRoot={repositoryRoot}
			workspaceRoot={options.workspaceRoot}
			model={options.model}
			baseUrl={options.baseUrl}
		/>,
		{exitOnCtrlC: false},
	);
} catch (error) {
	process.stderr.write(`${String(error)}\n`);
	process.exitCode = 2;
}
