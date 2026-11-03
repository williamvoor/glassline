import {describe, expect, it} from 'vitest';
import {A2UI_EXTENSION_URI, connect} from '../src/a2a.js';
import {EXPLORER_CATALOG_ID, createProcessor} from '../src/catalog.js';

const AGENT = 'http://agent.test';
const card = {
  name: 'Explorer', description: 'x', url: AGENT, version: '0.1.0', protocolVersion: '0.3.0',
  capabilities: {streaming: true}, defaultInputModes: ['text'], defaultOutputModes: ['text'], skills: [],
};

function sse(id: unknown, events: unknown[]): string {
  return events.map(e => `data: ${JSON.stringify({jsonrpc: '2.0', id, result: e})}\n\n`).join('');
}

describe('a2a wrapper', () => {
  it('sends the extension header and client capabilities, and splits prose from A2UI', async () => {
    const seen: {url: string; headers: Headers; body?: string}[] = [];
    const fakeFetch: typeof fetch = async (input, init) => {
      const url = String(input);
      seen.push({url, headers: new Headers(init?.headers), body: init?.body as string | undefined});
      if (url.endsWith('/.well-known/agent-card.json')) return Response.json(card);
      const a2ui = {version: 'v0.9', createSurface: {surfaceId: 'turn-1', catalogId: EXPLORER_CATALOG_ID}};
      return new Response(
        sse(JSON.parse(init!.body as string).id, [
          {kind: 'status-update', taskId: 't', contextId: 'c', final: false,
           status: {state: 'working', message: {kind: 'message', role: 'agent', messageId: 'm1',
             parts: [{kind: 'text', text: 'Running SQL'}]}}},
          {kind: 'status-update', taskId: 't', contextId: 'c', final: true,
           status: {state: 'completed', message: {kind: 'message', role: 'agent', messageId: 'm2', parts: [
             {kind: 'text', text: 'Prose.'},
             {kind: 'data', data: a2ui, metadata: {mimeType: 'application/json+a2ui'}},
             {kind: 'data', data: {other: true}},
           ]}}},
        ]),
        {headers: {'Content-Type': 'text/event-stream'}},
      );
    };

    const caps = createProcessor().getRendererCapabilities({versions: ['v0.9']});
    const agent = await connect(AGENT, caps, fakeFetch);
    const events = [];
    for await (const e of agent.askQuestion('hello')) events.push(e);

    expect(seen.length).toBe(2);
    for (const r of seen) expect(r.headers.get('X-A2A-Extensions')).toBe(A2UI_EXTENSION_URI);
    const rpc = JSON.parse(seen[1]!.body!);
    expect(rpc.method).toBe('message/stream');
    expect(rpc.params.message.metadata.a2uiClientCapabilities).toEqual({
      'v0.9': {supportedCatalogIds: [EXPLORER_CATALOG_ID]},
    });

    expect(agent.info).toEqual({description: 'x', examples: []});
    expect(events[0]).toEqual({kind: 'status', text: 'Running SQL'});
    expect(events[1]).toMatchObject({kind: 'final', contextId: 'c', prose: 'Prose.'});
    expect((events[1] as {a2ui: unknown[]}).a2ui).toHaveLength(1);
  });

  it('reads the dataset description and example questions from the agent card', async () => {
    const withExamples = {...card, description: 'Demo contracts', skills: [
      {id: 'ask', name: 'Ask', description: 'd', tags: [], examples: ['Top suppliers?', 'Spend by year?']},
    ]};
    const fakeFetch: typeof fetch = async () => Response.json(withExamples);
    const agent = await connect(AGENT, {}, fakeFetch);
    expect(agent.info).toEqual({description: 'Demo contracts', examples: ['Top suppliers?', 'Spend by year?']});
  });
});
