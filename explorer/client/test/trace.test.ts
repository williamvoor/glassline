// Traces (explorer/specs/trace/SPEC.md T11): the switch, the request flag, live events and the
// plain-text pane.
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {render} from 'lit';
import {connect, type AgentConnection, type AskEvent} from '../src/a2a.js';
import '../src/app.js';
import {LEVEL_KEY, LOG_KEY, NO_BOARDS_KEY, ACTION_FAILED_KEY, OPEN_BOARD, TAB_KEY, type ExplorerApp} from '../src/app.js';
import {EXPLORER_CATALOG_ID} from '../src/catalog.js';
import {TRACE_EVENT_KEY, TRACE_KEY, TRACE_STORAGE_KEY, renderTrace, setTraceOn, traceOn} from '../src/trace.js';
import {oneWorkspace} from './one-workspace.js';

const AGENT = 'http://agent.test';
const card = {
  name: 'Explorer', description: 'x', url: AGENT, version: '0.1.0', protocolVersion: '0.3.0',
  capabilities: {streaming: true}, defaultInputModes: ['text'], defaultOutputModes: ['text'], skills: [],
};
const HOSTILE = '<img src=x onerror=alert(1)><b>bold</b>';
const SQL_EVENT = {kind: 'sql', seq: 2, source: 'run_sql', sql: `SELECT '${HOSTILE}'`, ok: true, row_count: 3, elapsed_ms: 4};
const MODEL_EVENT = {kind: 'model', seq: 1, model: 'm', prompt_hash: 'h1', messages: [{role: 'user', content: HOSTILE}],
  first_message: 0, messages_total: 1, reply: {role: 'assistant', content: 'ok'}, tokens: {prompt: 10}, latency_ms: 7};

function sse(id: unknown, events: unknown[]): string {
  return events.map(e => `data: ${JSON.stringify({jsonrpc: '2.0', id, result: e})}\n\n`).join('');
}

class MemoryStorage {
  private items = new Map<string, string>();
  getItem(k: string) { return this.items.get(k) ?? null; }
  setItem(k: string, v: string) { this.items.set(k, String(v)); }
  removeItem(k: string) { this.items.delete(k); }
}

beforeEach(() => {
  vi.stubGlobal('localStorage', new MemoryStorage());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('the Trace switch', () => {
  it('is remembered per browser and is off when storage throws', () => {
    expect(traceOn()).toBe(false);
    setTraceOn(true);
    expect(globalThis.localStorage.getItem(TRACE_STORAGE_KEY)).toBe('1');
    expect(traceOn()).toBe(true);
    vi.spyOn(MemoryStorage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(traceOn()).toBe(false);
  });
});

describe('the a2a wrapper', () => {
  async function ask(): Promise<{events: AskEvent[]; metadata: Record<string, unknown>}> {
    let body = '';
    const fakeFetch: typeof fetch = async (input, init) => {
      if (String(input).endsWith('/.well-known/agent-card.json')) return Response.json(card);
      body = init!.body as string;
      return new Response(sse(JSON.parse(body).id, [
        {kind: 'status-update', taskId: 't', contextId: 'c', final: false,
         status: {state: 'working', message: {kind: 'message', role: 'agent', messageId: 'm1',
           parts: [{kind: 'text', text: ''}], metadata: {[TRACE_EVENT_KEY]: SQL_EVENT}}}},
        {kind: 'status-update', taskId: 't', contextId: 'c', final: true,
         status: {state: 'completed', message: {kind: 'message', role: 'agent', messageId: 'm2', parts: [{kind: 'text', text: 'P'}]}}},
      ]), {headers: {'Content-Type': 'text/event-stream'}});
    };
    const agent = await connect(AGENT, {}, fakeFetch);
    const events: AskEvent[] = [];
    for await (const e of agent.askQuestion('q')) events.push(e);
    return {events, metadata: JSON.parse(body).params.message.metadata};
  }

  it('sends explorerTrace only while the switch is on, and yields live Trace events', async () => {
    expect((await ask()).metadata[TRACE_KEY]).toBeUndefined();
    setTraceOn(true);
    const {events, metadata} = await ask();
    expect(metadata[TRACE_KEY]).toBe(true);
    expect(events[0]).toEqual({kind: 'trace', event: SQL_EVENT});
    expect(events[1]).toMatchObject({kind: 'final'});
  });
});

describe('the Trace pane', () => {
  it('renders prompts, messages and SQL as plain text', () => {
    const el = document.createElement('div');
    render(renderTrace([MODEL_EVENT, SQL_EVENT], {h1: `SYSTEM ${HOSTILE}`}), el);
    expect(el.querySelector('img')).toBeNull();
    expect(el.querySelector('pre b')).toBeNull();
    expect(el.textContent).toContain(HOSTILE);
    expect(el.textContent).toContain('SYSTEM');
    expect(el.querySelector('summary')!.textContent).toContain('1 model call · 1 query');
  });

  it('shows nothing without model or SQL events', () => {
    const el = document.createElement('div');
    render(renderTrace([{kind: 'prompt', hash: 'h', text: 'x'}], {}), el);
    expect(el.textContent).toBe('');
  });
});

describe('the app', () => {
  const connection: AgentConnection = {
    info: {description: 'Demo', examples: []},
    async *askQuestion() {
      yield {kind: 'trace', event: {kind: 'prompt', hash: 'h1', text: 'THE SYSTEM PROMPT'}};
      yield {kind: 'trace', event: MODEL_EVENT};
      yield {kind: 'trace', event: SQL_EVENT};
      yield {
        kind: 'final', contextId: 'ctx', prose: 'Answer', a2ui: [
          {version: 'v0.9', createSurface: {surfaceId: 'turn-1-1-aaaaaa', catalogId: EXPLORER_CATALOG_ID}},
        ],
        metadata: {[LEVEL_KEY]: {turn: 1, level: 1, label: 'q', surfaceId: 'turn-1-1-aaaaaa'},
                   [TAB_KEY]: {id: 't1', kind: 'question', title: 'q'}, [LOG_KEY]: {kind: 'question', text: 'q'},
                   [TRACE_KEY]: {level: '1-1'}},
      };
    },
    async *act(action) {
      if (action.name === OPEN_BOARD) {
        yield {kind: 'final', contextId: 'ctx', prose: '', a2ui: [], metadata: {[ACTION_FAILED_KEY]: true, [NO_BOARDS_KEY]: true}};
      }
    },
  };

  async function settle(app: ExplorerApp) {
    for (let i = 0; i < 12; i++) {
      await new Promise(r => setTimeout(r, 5));
      await app.updateComplete;
    }
  }

  it('toggles from the Canvas header and shows the tab\'s Trace with its system prompt', async () => {
    window.history.replaceState(null, '', window.location.pathname);
    const app = document.createElement('explorer-app') as ExplorerApp;
    app.workspaceApi = oneWorkspace();
    app.connector = async () => connection;
    document.body.appendChild(app);
    await settle(app);
    const toggle = app.querySelector('.head-actions .trace-toggle') as HTMLButtonElement;
    expect(toggle.getAttribute('aria-pressed')).toBe('false');

    const input = app.querySelector('textarea') as HTMLTextAreaElement;
    input.value = 'q';
    input.dispatchEvent(new Event('input'));
    (app.querySelector('form') as HTMLFormElement).requestSubmit();
    await settle(app);
    expect(app.querySelector('.trace')).toBeNull(); // off: recorded events stay hidden

    toggle.click();
    await settle(app);
    expect(globalThis.localStorage.getItem(TRACE_STORAGE_KEY)).toBe('1');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    const pane = app.querySelector('.canvas .trace') as HTMLElement;
    expect(pane.textContent).toContain('THE SYSTEM PROMPT');
    expect(pane.textContent).toContain(SQL_EVENT.sql);
    expect(pane.querySelector('img')).toBeNull();
  });
});
