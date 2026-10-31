// A2A client wrapper: one streamed task per question (explorer SPEC §2, ADR 0007).
import {A2AClient} from '@a2a-js/sdk/client';
import type {Message, Part} from '@a2a-js/sdk';

export const A2UI_EXTENSION_URI = 'https://a2ui.org/a2a-extension/a2ui/v0.9';
// v0.9 parts carry the deprecated type; newer agents send the spec's one. Accept both.
export const A2UI_MIME_TYPES = new Set(['application/a2ui+json', 'application/json+a2ui']);

export type AskEvent =
  | {kind: 'status'; text: string}
  | {kind: 'final'; contextId: string; prose: string; a2ui: Record<string, unknown>[]};

export interface AgentConnection {
  ask(text: string, contextId?: string): AsyncGenerator<AskEvent, void, undefined>;
}

export function withExtensionHeader(fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis)): typeof fetch {
  return (input, init) => {
    const headers = new Headers(init?.headers);
    headers.set('X-A2A-Extensions', A2UI_EXTENSION_URI);
    return fetchImpl(input, {...init, headers});
  };
}

function textOf(parts: Part[] | undefined): string {
  return (parts ?? [])
    .filter((p): p is Extract<Part, {kind: 'text'}> => p.kind === 'text')
    .map(p => p.text)
    .join('\n\n');
}

function a2uiOf(parts: Part[] | undefined): Record<string, unknown>[] {
  return (parts ?? [])
    .filter((p): p is Extract<Part, {kind: 'data'}> => p.kind === 'data')
    .filter(p => A2UI_MIME_TYPES.has(String(p.metadata?.['mimeType'] ?? '')))
    .map(p => p.data as Record<string, unknown>);
}

/**
 * @param capabilities the renderer's `getRendererCapabilities({versions: ['v0.9']})`, sent as
 *   `a2uiClientCapabilities` in each message's metadata.
 */
export async function connect(
  agentUrl: string,
  capabilities: unknown,
  fetchImpl?: typeof fetch,
): Promise<AgentConnection> {
  const client = await A2AClient.fromCardUrl(`${agentUrl.replace(/\/$/, '')}/.well-known/agent-card.json`, {
    fetchImpl: withExtensionHeader(fetchImpl),
  });

  return {
    async *ask(text, contextId) {
      const message: Message = {
        kind: 'message',
        role: 'user',
        messageId: crypto.randomUUID(),
        parts: [{kind: 'text', text}],
        metadata: {a2uiClientCapabilities: capabilities},
        ...(contextId ? {contextId} : {}),
      };
      for await (const event of client.sendMessageStream({message})) {
        if (event.kind !== 'status-update') continue;
        const parts = event.status.message?.parts;
        if (event.final) {
          yield {kind: 'final', contextId: event.contextId, prose: textOf(parts), a2ui: a2uiOf(parts)};
          return;
        }
        const statusText = textOf(parts);
        if (statusText) yield {kind: 'status', text: statusText};
      }
      throw new Error('The agent closed the stream without a final answer.');
    },
  };
}
