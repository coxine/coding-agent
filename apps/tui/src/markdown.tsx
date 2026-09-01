import React, {useMemo} from 'react';
import {Box, Text} from 'ink';
import {highlight, supportsLanguage, type Theme} from 'cli-highlight';
import stringWidth from 'string-width';

export type InlineToken = {
	type: 'text' | 'bold' | 'italic' | 'code' | 'strike' | 'link';
	text: string;
	url?: string;
};

export type MarkdownBlock =
	| {type: 'heading'; level: number; text: string}
	| {type: 'paragraph'; text: string}
	| {type: 'code'; language: string; text: string}
	| {type: 'table'; headers: string[]; alignments: TableAlignment[]; rows: string[][]}
	| {type: 'list'; ordered: boolean; items: Array<{text: string; number?: number; indent: number}>}
	| {type: 'quote'; text: string}
	| {type: 'rule'}
	| {type: 'space'};

export type TableAlignment = 'left' | 'center' | 'right';

const ansi = (open: number, close = 39) => (value: string): string => `\u001B[${open}m${value}\u001B[${close}m`;
const HIGHLIGHT_THEME: Theme = {
	keyword: ansi(34),
	built_in: ansi(36),
	type: ansi(36),
	literal: ansi(35),
	number: ansi(33),
	regexp: ansi(31),
	string: ansi(32),
	title: ansi(36),
	function: ansi(36),
	comment: ansi(90),
	doctag: ansi(90),
	meta: ansi(35),
	tag: ansi(34),
	name: ansi(34),
	attr: ansi(36),
	addition: ansi(32),
	deletion: ansi(31),
};

export function MarkdownText({children}: {children: string}): React.ReactNode {
	const blocks = useMemo(() => parseMarkdown(children), [children]);
	return (
		<Box flexDirection="column">
			{blocks.map((block, index) => <MarkdownBlockView key={`${block.type}-${index}`} block={block} />)}
		</Box>
	);
}

export function parseMarkdown(source: string): MarkdownBlock[] {
	const lines = stripTerminalControls(source).replaceAll('\r\n', '\n').replaceAll('\r', '\n').split('\n');
	const blocks: MarkdownBlock[] = [];
	let index = 0;

	while (index < lines.length) {
		const line = lines[index] ?? '';
		if (!line.trim()) {
			if (blocks.length > 0 && blocks.at(-1)?.type !== 'space') blocks.push({type: 'space'});
			index += 1;
			continue;
		}

		const fence = line.match(/^ {0,3}(```|~~~)\s*([^\s`]*)?.*$/);
		if (fence) {
			const marker = fence[1] ?? '```';
			const language = fence[2] ?? '';
			const content: string[] = [];
			index += 1;
			while (index < lines.length && !new RegExp(`^ {0,3}${escapeRegExp(marker)}\\s*$`).test(lines[index] ?? '')) {
				content.push(lines[index] ?? '');
				index += 1;
			}
			if (index < lines.length) index += 1;
			blocks.push({type: 'code', language, text: content.join('\n')});
			continue;
		}

		const table = parseTable(lines, index);
		if (table) {
			blocks.push(table.block);
			index = table.nextIndex;
			continue;
		}

		const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*$/);
		if (heading) {
			blocks.push({type: 'heading', level: heading[1]?.length ?? 1, text: heading[2] ?? ''});
			index += 1;
			continue;
		}

		if (/^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
			blocks.push({type: 'rule'});
			index += 1;
			continue;
		}

		if (/^\s*>\s?/.test(line)) {
			const quoted: string[] = [];
			while (index < lines.length && /^\s*>\s?/.test(lines[index] ?? '')) {
				quoted.push((lines[index] ?? '').replace(/^\s*>\s?/, ''));
				index += 1;
			}
			blocks.push({type: 'quote', text: quoted.join(' ')});
			continue;
		}

		const firstListItem = parseListItem(line);
		if (firstListItem) {
			const items: Array<{text: string; number?: number; indent: number}> = [];
			const ordered = firstListItem.ordered;
			while (index < lines.length) {
				const item = parseListItem(lines[index] ?? '');
				if (!item || item.ordered !== ordered) break;
				items.push(
					item.number === undefined
						? {text: item.text, indent: item.indent}
						: {text: item.text, number: item.number, indent: item.indent},
				);
				index += 1;
			}
			blocks.push({type: 'list', ordered, items});
			continue;
		}

		const paragraph: string[] = [line.trim()];
		index += 1;
		while (
			index < lines.length &&
			(lines[index] ?? '').trim() &&
			!startsBlock(lines[index] ?? '') &&
			!parseTable(lines, index)
		) {
			paragraph.push((lines[index] ?? '').trim());
			index += 1;
		}
		blocks.push({type: 'paragraph', text: paragraph.join(' ')});
	}

	if (blocks.at(-1)?.type === 'space') blocks.pop();
	return blocks;
}

export function highlightCode(source: string, language: string): string {
	if (!source || source.length > 100_000) return source;
	const normalized = language.trim().toLowerCase();
	if (normalized && !supportsLanguage(normalized)) return source;
	try {
		return highlight(source, {
			...(normalized ? {language: normalized} : {}),
			ignoreIllegals: true,
			theme: HIGHLIGHT_THEME,
		});
	} catch {
		return source;
	}
}

export function parseInline(source: string): InlineToken[] {
	const tokens: InlineToken[] = [];
	let index = 0;

	const text = (value: string) => {
		if (!value) return;
		const previous = tokens.at(-1);
		if (previous?.type === 'text') previous.text += value;
		else tokens.push({type: 'text', text: value});
	};

	while (index < source.length) {
		if (source[index] === '\\' && index + 1 < source.length) {
			text(source[index + 1] ?? '');
			index += 2;
			continue;
		}

		if (source[index] === '`') {
			const closing = source.indexOf('`', index + 1);
			if (closing > index + 1) {
				tokens.push({type: 'code', text: source.slice(index + 1, closing)});
				index = closing + 1;
				continue;
			}
		}

		const paired = [
			{marker: '**', type: 'bold' as const},
			{marker: '__', type: 'bold' as const},
			{marker: '~~', type: 'strike' as const},
		];
		let consumed = false;
		for (const pair of paired) {
			if (!source.startsWith(pair.marker, index)) continue;
			const closing = source.indexOf(pair.marker, index + pair.marker.length);
			if (closing <= index + pair.marker.length) continue;
			tokens.push({type: pair.type, text: source.slice(index + pair.marker.length, closing)});
			index = closing + pair.marker.length;
			consumed = true;
			break;
		}
		if (consumed) continue;

		if (source[index] === '[') {
			const match = source.slice(index).match(/^\[([^\]]+)]\(([^)\s]+)\)/);
			if (match) {
				tokens.push({type: 'link', text: match[1] ?? '', url: match[2] ?? ''});
				index += match[0].length;
				continue;
			}
		}

		if (source[index] === '*' || source[index] === '_') {
			const marker = source[index] ?? '';
			const closing = source.indexOf(marker, index + 1);
			if (closing > index + 1 && !/^\s/.test(source.slice(index + 1, closing))) {
				tokens.push({type: 'italic', text: source.slice(index + 1, closing)});
				index = closing + 1;
				continue;
			}
		}

		text(source[index] ?? '');
		index += 1;
	}

	return tokens;
}

export function stripTerminalControls(source: string): string {
	return source
		.replaceAll(/\u001B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '')
		.replaceAll(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '');
}

function MarkdownBlockView({block}: {block: MarkdownBlock}): React.ReactNode {
	if (block.type === 'space') return <Text> </Text>;
	if (block.type === 'rule') return <Text dimColor>{'─'.repeat(32)}</Text>;
	if (block.type === 'heading') {
		return (
			<Text bold underline={block.level <= 2} color={block.level <= 2 ? 'cyan' : undefined}>
				{renderInline(block.text)}
			</Text>
		);
	}
	if (block.type === 'code') {
		const highlighted = highlightCode(block.text, block.language);
		return (
			<Box flexDirection="column" borderStyle="round" borderColor="gray" paddingX={1} marginY={1}>
				{block.language && <Text dimColor>{block.language}</Text>}
				<Text>{highlighted || ' '}</Text>
			</Box>
		);
	}
	if (block.type === 'table') return <MarkdownTable block={block} />;
	if (block.type === 'quote') {
		return <Box><Text color="gray">│ </Text><Text italic>{renderInline(block.text)}</Text></Box>;
	}
	if (block.type === 'list') {
		return (
			<Box flexDirection="column">
				{block.items.map((item, index) => (
					<Box key={`${item.text}-${index}`} paddingLeft={Math.min(item.indent, 3) * 2}>
						<Text color="cyan">{block.ordered ? `${item.number ?? index + 1}. ` : '• '}</Text>
						<Text>{renderInline(item.text)}</Text>
					</Box>
				))}
			</Box>
		);
	}
	return <Text>{renderInline(block.text)}</Text>;
}

function MarkdownTable({block}: {block: Extract<MarkdownBlock, {type: 'table'}>}): React.ReactNode {
	const widths = block.headers.map((header, column) =>
		Math.max(
			3,
			stringWidth(inlineDisplayText(header)),
			...block.rows.map(row => stringWidth(inlineDisplayText(row[column] ?? ''))),
		),
	);
	const border = (left: string, middle: string, right: string, fill: string) =>
		left + widths.map(width => fill.repeat(width + 2)).join(middle) + right;
	return (
		<Box flexDirection="column" marginY={1}>
			<Text dimColor>{border('┌', '┬', '┐', '─')}</Text>
			<TableRow cells={block.headers} widths={widths} alignments={block.alignments} header />
			<Text dimColor>{border('├', '┼', '┤', '─')}</Text>
			{block.rows.map((row, index) => (
				<TableRow key={`row-${index}`} cells={row} widths={widths} alignments={block.alignments} />
			))}
			<Text dimColor>{border('└', '┴', '┘', '─')}</Text>
		</Box>
	);
}

function TableRow({cells, widths, alignments, header = false}: {
	cells: string[];
	widths: number[];
	alignments: TableAlignment[];
	header?: boolean;
}): React.ReactNode {
	return (
		<Text>
			<Text dimColor>│</Text>
			{widths.map((width, index) => {
				const cell = cells[index] ?? '';
				const remaining = Math.max(0, width - stringWidth(inlineDisplayText(cell)));
				const alignment = alignments[index] ?? 'left';
				const left = alignment === 'right' ? remaining : alignment === 'center' ? Math.floor(remaining / 2) : 0;
				const right = remaining - left;
				return (
					<React.Fragment key={`cell-${index}`}>
						<Text>{` ${' '.repeat(left)}`}</Text>
						<Text bold={header}>{renderInline(cell)}</Text>
						<Text>{`${' '.repeat(right)} `}</Text>
						<Text dimColor>│</Text>
					</React.Fragment>
				);
			})}
		</Text>
	);
}

function renderInline(source: string): React.ReactNode[] {
	return parseInline(source).map((token, index) => {
		const key = `${token.type}-${index}`;
		if (token.type === 'bold') return <Text key={key} bold>{token.text}</Text>;
		if (token.type === 'italic') return <Text key={key} italic>{token.text}</Text>;
		if (token.type === 'strike') return <Text key={key} strikethrough>{token.text}</Text>;
		if (token.type === 'code') return <Text key={key} color="yellow" inverse>{` ${token.text} `}</Text>;
		if (token.type === 'link') {
			return <Text key={key}><Text color="blue" underline>{token.text}</Text><Text dimColor>{` (${token.url})`}</Text></Text>;
		}
		return <Text key={key}>{token.text}</Text>;
	});
}

function parseListItem(line: string): {ordered: boolean; text: string; number?: number; indent: number} | undefined {
	const unordered = line.match(/^(\s*)[-+*]\s+(.+)$/);
	if (unordered) {
		return {ordered: false, text: unordered[2] ?? '', indent: Math.floor((unordered[1]?.length ?? 0) / 2)};
	}
	const ordered = line.match(/^(\s*)(\d+)[.)]\s+(.+)$/);
	if (ordered) {
		return {
			ordered: true,
			text: ordered[3] ?? '',
			number: Number(ordered[2]),
			indent: Math.floor((ordered[1]?.length ?? 0) / 2),
		};
	}
	return undefined;
}

function startsBlock(line: string): boolean {
	return (
		/^ {0,3}(```|~~~)/.test(line) ||
		/^ {0,3}#{1,6}\s+/.test(line) ||
		/^\s*>\s?/.test(line) ||
		/^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line) ||
		Boolean(parseListItem(line))
	);
}

function parseTable(
	lines: string[],
	index: number,
): {block: Extract<MarkdownBlock, {type: 'table'}>; nextIndex: number} | undefined {
	const headerLine = lines[index] ?? '';
	const delimiterLine = lines[index + 1] ?? '';
	if (!headerLine.includes('|') || !delimiterLine.includes('|')) return undefined;
	const headers = splitTableRow(headerLine);
	const delimiters = splitTableRow(delimiterLine);
	if (headers.length === 0 || headers.length !== delimiters.length) return undefined;
	if (!delimiters.every(cell => /^:?-{3,}:?$/.test(cell.replaceAll(/\s/g, '')))) return undefined;
	const alignments = delimiters.map((cell): TableAlignment => {
		const marker = cell.replaceAll(/\s/g, '');
		if (marker.startsWith(':') && marker.endsWith(':')) return 'center';
		if (marker.endsWith(':')) return 'right';
		return 'left';
	});
	const rows: string[][] = [];
	let nextIndex = index + 2;
	while (nextIndex < lines.length) {
		const line = lines[nextIndex] ?? '';
		if (!line.trim() || !line.includes('|')) break;
		const cells = splitTableRow(line);
		if (cells.length === 0) break;
		rows.push(headers.map((_, column) => cells[column] ?? ''));
		nextIndex += 1;
	}
	return {block: {type: 'table', headers, alignments, rows}, nextIndex};
}

function splitTableRow(line: string): string[] {
	const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
	const cells: string[] = [];
	let cell = '';
	let inCode = false;
	for (let index = 0; index < trimmed.length; index += 1) {
		const character = trimmed[index] ?? '';
		if (character === '\\') {
			const next = trimmed[index + 1];
			if (next === '|' || next === '\\') {
				cell += next;
				index += 1;
			} else {
				cell += character;
			}
			continue;
		}
		if (character === '`') inCode = !inCode;
		if (character === '|' && !inCode) {
			cells.push(cell.trim());
			cell = '';
			continue;
		}
		cell += character;
	}
	cells.push(cell.trim());
	return cells;
}

function inlineDisplayText(source: string): string {
	return parseInline(source)
		.map(token => token.type === 'link' ? `${token.text} (${token.url})` : token.text)
		.join('');
}

function escapeRegExp(value: string): string {
	return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
