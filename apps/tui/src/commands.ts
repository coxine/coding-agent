export type SlashCommand = {
	name: string;
	description: string;
};

export const SLASH_COMMANDS: SlashCommand[] = [
	{name: '/session', description: 'Browse, switch, or create workspace conversations'},
	{name: '/status', description: 'Show model, workspace, context usage, and session metadata'},
];

export function matchingCommands(input: string): SlashCommand[] {
	if (!input.startsWith('/') || input.includes('\n')) return [];
	const query = input.slice(1).toLowerCase();
	if (query.includes(' ')) return [];
	return SLASH_COMMANDS.filter(command => command.name.slice(1).startsWith(query));
}
