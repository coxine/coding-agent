export type TokenSegmentKind = 'input' | 'cached' | 'output' | 'reasoning' | 'remaining';

export type TokenSegment = {
	kind: TokenSegmentKind;
	value: number;
	width: number;
};

export function tokenBarSegments(
	usage: {promptTokens: number; completionTokens: number; cachedTokens: number; reasoningTokens: number},
	width: number,
	contextWindowTokens?: number,
): TokenSegment[] {
	const prompt = nonnegative(usage.promptTokens);
	const completion = nonnegative(usage.completionTokens);
	const cached = Math.min(prompt, nonnegative(usage.cachedTokens));
	const reasoning = Math.min(completion, nonnegative(usage.reasoningTokens));
	const total = prompt + completion;
	const remaining = contextWindowTokens && contextWindowTokens > total
		? contextWindowTokens - total
		: 0;
	const values = [
		{kind: 'input' as const, value: prompt - cached},
		{kind: 'cached' as const, value: cached},
		{kind: 'output' as const, value: completion - reasoning},
		{kind: 'reasoning' as const, value: reasoning},
		{kind: 'remaining' as const, value: remaining},
	];
	const widths = allocateWidths(values.map(segment => segment.value), width);
	return values.map((segment, index) => ({...segment, width: widths[index] ?? 0}));
}

function allocateWidths(values: number[], width: number): number[] {
	const result = values.map(() => 0);
	const availableWidth = Math.max(0, Math.floor(width));
	const active = values.map((value, index) => ({value, index})).filter(item => item.value > 0);
	if (availableWidth === 0 || active.length === 0) return result;

	let remainingWidth = availableWidth;
	if (availableWidth >= active.length) {
		for (const item of active) result[item.index] = 1;
		remainingWidth -= active.length;
	}
	const total = active.reduce((sum, item) => sum + item.value, 0);
	const shares = active.map(item => ({
		...item,
		exact: (item.value / total) * remainingWidth,
	}));
	for (const share of shares) {
		const whole = Math.floor(share.exact);
		result[share.index] += whole;
		remainingWidth -= whole;
	}
	shares.sort((left, right) =>
		(right.exact % 1) - (left.exact % 1) || right.value - left.value,
	);
	for (let index = 0; index < remainingWidth; index += 1) {
		result[shares[index % shares.length]!.index] += 1;
	}
	return result;
}

function nonnegative(value: number): number {
	return Number.isFinite(value) ? Math.max(0, value) : 0;
}
