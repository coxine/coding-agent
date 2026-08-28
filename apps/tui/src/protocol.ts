import {randomUUID} from 'node:crypto';

export const PROTOCOL_VERSION = 1;

export type CoreEvent = {
	protocolVersion: number;
	type: string;
	messageId: string;
	timestamp: string;
	sessionId?: string;
	turnId?: string;
	toolCallId?: string;
	payload: Record<string, unknown>;
};

export type ClientMessage = CoreEvent;

export function message(
	type: string,
	payload: Record<string, unknown> = {},
	ids: {sessionId?: string; turnId?: string; toolCallId?: string} = {},
): ClientMessage {
	return {
		protocolVersion: PROTOCOL_VERSION,
		type,
		messageId: `msg_${randomUUID().replaceAll('-', '')}`,
		timestamp: new Date().toISOString(),
		...ids,
		payload,
	};
}

export function isCoreEvent(value: unknown): value is CoreEvent {
	if (!value || typeof value !== 'object') return false;
	const event = value as Partial<CoreEvent>;
	return (
		event.protocolVersion === PROTOCOL_VERSION &&
		typeof event.type === 'string' &&
		typeof event.messageId === 'string' &&
		!!event.payload &&
		typeof event.payload === 'object'
	);
}

