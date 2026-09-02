export type SlashCommand = {
	name: string;
	description: string;
};

export const SLASH_COMMANDS: SlashCommand[] = [
	{name: '/rename', description: 'Rename the current conversation: /rename <name>'},
	{name: '/session', description: 'Browse, switch, or create workspace conversations'},
	{name: '/status', description: 'Show model, workspace, context usage, and session metadata'},
	{name: '/compact', description: 'Compact the conversation context manually'},
];

export type SlashInvocation = {
	command: SlashCommand;
	argument: string;
};

export function matchingCommands(input: string): SlashCommand[] {
	if (!input.startsWith('/') || input.includes('\n')) return [];
	const query = input.slice(1).toLowerCase();
	if (query.includes(' ')) return [];
	return SLASH_COMMANDS.filter(command => command.name.slice(1).startsWith(query));
}

export function parseSlashCommand(input: string): SlashInvocation | undefined {
	const trimmed = input.trim();
	if (!trimmed.startsWith('/') || trimmed.includes('\n')) return undefined;
	const separator = trimmed.search(/\s/);
	const name = separator < 0 ? trimmed : trimmed.slice(0, separator);
	const command = SLASH_COMMANDS.find(candidate => candidate.name === name.toLowerCase());
	if (!command) return undefined;
	return {command, argument: separator < 0 ? '' : trimmed.slice(separator).trim()};
}
