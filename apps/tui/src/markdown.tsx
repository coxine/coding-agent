import React, {useMemo} from 'react';
import {Text, useWindowSize} from 'ink';
import {highlight, supportsLanguage, type Theme as HighlightTheme} from 'cli-highlight';
import {render, type RenderOptions, type Theme} from 'markdansi';
import sliceAnsi from 'slice-ansi';
import stringWidth from 'string-width';

const MIN_RENDER_WIDTH = 20;
const HORIZONTAL_CHROME = 4;
const MAX_HIGHLIGHT_CHARS = 100_000;

const ansi = (open: number, close = 39) => (value: string): string => `\u001B[${open}m${value}\u001B[${close}m`;

const HIGHLIGHT_THEME: HighlightTheme = {
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

const MARKDOWN_THEME: Theme = {
	heading: {color: 'cyan', bold: true, underline: true},
	strong: {bold: true},
	emph: {italic: true},
	inlineCode: {color: 'yellow'},
	blockCode: {color: 'yellow'},
	link: {color: 'blue', underline: true},
	quote: {color: 'gray', italic: true},
	hr: {color: 'gray', dim: true},
	listMarker: {color: 'cyan'},
	tableHeader: {color: 'cyan', bold: true},
};

export function MarkdownText({children}: {children: string}): React.ReactNode {
	const {columns} = useWindowSize();
	const width = Math.max(MIN_RENDER_WIDTH, columns - HORIZONTAL_CHROME);
	const rendered = useMemo(() => renderMarkdownForTerminal(children, width), [children, width]);
	return <Text>{rendered || ' '}</Text>;
}

export function renderMarkdownForTerminal(source: string, width: number): string {
	const safeSource = stripTerminalControls(source);
	const options: RenderOptions = {
		width: Math.max(MIN_RENDER_WIDTH, width),
		wrap: true,
		color: true,
		hyperlinks: false,
		theme: MARKDOWN_THEME,
		tableBorder: 'unicode',
		tablePadding: 1,
		tableTruncate: false,
		codeBox: true,
		codeGutter: false,
		codeWrap: true,
		highlighter: highlightCode,
	};
	return alignWrappedTableRows(render(safeSource, options)).replace(/^\n+|\n+$/g, '');
}

function alignWrappedTableRows(rendered: string): string {
	const output: string[] = [];
	let columnWidths: number[] | undefined;

	for (const line of rendered.split('\n')) {
		const plain = stripTerminalControls(line);
		if (plain.startsWith('┌') && plain.endsWith('┐')) {
			columnWidths = plain.slice(1, -1).split('┬').map(part => stringWidth(part));
			output.push(line);
			continue;
		}

		if (columnWidths && plain.startsWith('│') && plain.endsWith('│')) {
			const widths = columnWidths;
			const cells = line.slice(1, -1).split('│');
			if (cells.length === widths.length) {
				const wrapped = cells.map((cell, index) => wrapTableCell(cell, widths[index] ?? 1));
				const height = Math.max(...wrapped.map(lines => lines.length));
				for (let row = 0; row < height; row += 1) {
					const parts = wrapped.map((lines, index) => padAnsi(lines[row] ?? '', widths[index] ?? 1));
					output.push(`│${parts.join('│')}│`);
				}
				continue;
			}
		}

		output.push(line);
		if (columnWidths && plain.startsWith('└') && plain.endsWith('┘')) columnWidths = undefined;
	}

	return output.join('\n');
}

function wrapTableCell(value: string, width: number): string[] {
	if (visibleWidth(value) <= width) return [value];
	const padding = Math.min(1, Math.floor((width - 1) / 2));
	const contentWidth = Math.max(1, width - padding * 2);
	const content = sliceAnsi(value, padding, Math.max(padding, visibleWidth(value) - padding));
	return hardWrapAnsi(content, contentWidth).map(line => {
		return `${' '.repeat(padding)}${padAnsi(line, contentWidth)}${' '.repeat(padding)}`;
	});
}

function hardWrapAnsi(value: string, width: number): string[] {
	const totalWidth = visibleWidth(value);
	if (totalWidth <= width) return [value];

	const lines: string[] = [];
	let start = 0;
	while (start < totalWidth) {
		let end = Math.min(totalWidth, start + width);
		let part = sliceAnsi(value, start, end);
		let partWidth = visibleWidth(part);
		while (partWidth === 0 && end < totalWidth) {
			end += 1;
			part = sliceAnsi(value, start, end);
			partWidth = visibleWidth(part);
		}
		if (partWidth === 0) break;
		lines.push(part);
		start += partWidth;
	}
	return lines.length > 0 ? lines : [''];
}

function padAnsi(value: string, width: number): string {
	return `${value}${' '.repeat(Math.max(0, width - visibleWidth(value)))}`;
}

function visibleWidth(value: string): number {
	return stringWidth(stripTerminalControls(value));
}

export function highlightCode(source: string, language?: string): string {
	if (!source || source.length > MAX_HIGHLIGHT_CHARS) return source;
	const normalized = language?.trim().toLowerCase();
	if (!normalized || !supportsLanguage(normalized)) return source;
	try {
		return highlight(source, {
			language: normalized,
			ignoreIllegals: true,
			theme: HIGHLIGHT_THEME,
		});
	} catch {
		return source;
	}
}

export function stripTerminalControls(source: string): string {
	return source
		.replaceAll(/\u001B\][^\u0007]*?(?:\u0007|\u001B\\|$)/g, '')
		.replaceAll(/\u001B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '')
		.replaceAll(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/g, '');
}
