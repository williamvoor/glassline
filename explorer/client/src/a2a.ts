// A2A client wrapper: one streamed task per question or Action (explorer SPEC §2, §15, ADR 0007).
import {A2AClient} from '@a2a-js/sdk/client';
import type {Message, Part} from '@a2a-js/sdk';
import {TRACE_EVENT_KEY, TRACE_KEY, traceOn, type TraceEvent} from './trace.js';

export const A2UI_EXTENSION_URI = 'https://a2ui.org/a2a-extension/a2ui/v0.9';
// v0.9 parts carry the deprecated type; newer agents send the spec's one. Accept both.
export const A2UI_MIME_TYPES = new Set(['application/a2ui+json', 'application/json+a2ui']);

export type AskEvent =
  | {kind: 'status'; text: string}
  | {kind: 'trace'; event: TraceEvent}
  | {kind: 'final'; contextId: string; prose: string; a2ui: Record<string, unknown>[]; metadata?: Record<string, unknown>};

/** An A2UI v0.9 action as dispatched by a surface (references only, SPEC §15). */
export interface UiAction {
  name: string;
  surfaceId: string;
  sourceComponentId: string;
  timestamp: string;
  context: Record<string, unknown>;
}

/** What the client shows about the agent's dataset (from the agent card). */
export interface AgentInfo {
  description: string;
  examples: string[];
}

export interface AgentConnection {
  info: AgentInfo;
  /** A typed question. `viewing`: the selected tab's ID (SPEC §18). */
  askQuestion(text: string, contextId?: string, viewing?: string): AsyncGenerator<AskEvent, void, undefined>;
  /** An Action: a Drill-down, a confirmed Ask, or `open_board`, which may start the conversation
   * (no contextId yet, SPEC §18). */
  act(action: UiAction, contextId?: string, viewing?: string): AsyncGenerator<AskEvent, void, undefined>;
}

export const VIEWING_KEY = 'explorerViewing';
/** Workspace mode: the closed and selected tabs, saved with the Conversation (Workspaces B12). */
export const VIEW_KEY = 'explorerView';
export type ViewState = {closed: string[]; selected?: string};

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
 * @param view Workspace mode: the view sent as `explorerView` with each message.
 */
export async function connect(
  agentUrl: string,
  capabilities: unknown,
  fetchImpl?: typeof fetch,
  view?: () => ViewState | undefined,
): Promise<AgentConnection> {
  const client = await A2AClient.fromCardUrl(`${agentUrl.replace(/\/$/, '')}/.well-known/agent-card.json`, {
    fetchImpl: withExtensionHeader(fetchImpl),
  });

  const card = await client.getAgentCard();
  const info: AgentInfo = {
    description: card.description ?? '',
    examples: (card.skills ?? []).flatMap(skill => skill.examples ?? []),
  };

  async function* send(parts: Part[], contextId: string | undefined, viewing: string | undefined) {
    const message: Message = {
      kind: 'message',
      role: 'user',
      messageId: crypto.randomUUID(),
      parts,
      metadata: {
        a2uiClientCapabilities: capabilities,
        ...(viewing ? {[VIEWING_KEY]: viewing} : {}),
        ...(view?.() ? {[VIEW_KEY]: view()} : {}),
        ...(traceOn() ? {[TRACE_KEY]: true} : {}),
      },
      ...(contextId ? {contextId} : {}),
    };
    for await (const event of client.sendMessageStream({message})) {
      if (event.kind !== 'status-update') continue;
      const parts = event.status.message?.parts;
      if (event.final) {
        const metadata = event.status.message?.metadata as Record<string, unknown> | undefined;
        yield {kind: 'final', contextId: event.contextId, prose: textOf(parts), a2ui: a2uiOf(parts), metadata} as AskEvent;
        return;
      }
      const traced = (event.status.message?.metadata as Record<string, unknown> | undefined)?.[TRACE_EVENT_KEY];
      if (traced && typeof traced === 'object') {
        yield {kind: 'trace', event: traced as TraceEvent} as AskEvent;
        continue;
      }
      const statusText = textOf(parts);
      if (statusText) yield {kind: 'status', text: statusText} as AskEvent;
    }
    throw new Error('The agent closed the stream without a final answer.');
  }

  return {
    info,
    askQuestion: (text, contextId, viewing) => send([{kind: 'text', text}], contextId, viewing),
    act: (action, contextId, viewing) =>
      send(
        [{kind: 'data', data: {version: 'v0.9', action: {...action}}, metadata: {mimeType: 'application/json+a2ui'}}],
        contextId,
        viewing,
      ),
  };
}
