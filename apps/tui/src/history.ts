export type HistoryStep = {
	index: number;
	draft: string;
	input: string;
};

export function pushHistory(history: string[], entry: string, cap = 100): string[] {
	const next = [...history, entry];
	return next.length > cap ? next.slice(next.length - cap) : next;
}

export function stepHistory(
	history: string[],
	index: number,
	draft: string,
	current: string,
	direction: 'up' | 'down',
): HistoryStep {
	if (direction === 'up') {
		if (history.length === 0) return {index, draft, input: current};
		if (index === -1) {
			const next = history.length - 1;
			return {index: next, draft: current, input: history[next]};
		}
		const next = Math.max(0, index - 1);
		return {index: next, draft, input: history[next]};
	}
	if (index === -1) return {index, draft, input: current};
	const next = index + 1;
	if (next >= history.length) return {index: -1, draft: '', input: draft};
	return {index: next, draft, input: history[next]};
}
