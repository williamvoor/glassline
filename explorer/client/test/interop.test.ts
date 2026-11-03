// Interop: the real Python agent (stub model) ↔ @a2a-js/sdk, over HTTP + SSE. Needs `uv` on PATH.
import {spawn, type ChildProcess} from 'node:child_process';
import {createServer} from 'node:net';
import {resolve} from 'node:path';
import {afterAll, beforeAll, describe, expect, it} from 'vitest';
import type {A2uiMessage} from '@a2ui/web_core/v0_9';
import {connect, type AskEvent} from '../src/a2a.js';
import {createProcessor} from '../src/catalog.js';

function surfaceIdOf(messages: Record<string, unknown>[]): string | undefined {
  for (const m of messages) {
    const create = m['createSurface'] as {surfaceId?: string} | undefined;
    if (create?.surfaceId) return create.surfaceId;
  }
  return undefined;
}

const AGENT_DIR = resolve(process.cwd(), '../agent'); // tests run from explorer/client
const STATUS_TEXT = 'Running SQL: top suppliers (fixture)'; // explorer_agent.testing.stub_server

let server: ChildProcess | undefined;
let agentUrl = '';

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const s = createServer();
    s.unref();
    s.on('error', reject);
    s.listen(0, '127.0.0.1', () => {
      const port = (s.address() as {port: number}).port;
      s.close(() => resolve(port));
    });
  });
}

beforeAll(async () => {
  const port = await freePort();
  server = spawn('uv', ['run', 'python', '-m', 'explorer_agent.testing.stub_server', '--port', String(port),
    // Not a CORS test (Node does not enforce CORS; test_cors_preflight covers it).
    '--client-origin', window.location.origin], {
    cwd: AGENT_DIR,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true, // own process group, so uv's python child is killed with it
  });
  let stderr = '';
  server.stderr!.on('data', d => (stderr += d));
  agentUrl = await new Promise<string>((resolve, reject) => {
    server!.on('error', e => reject(new Error(`could not start uv (is it on PATH?): ${e.message}`)));
    server!.on('exit', code => reject(new Error(`stub server exited with ${code}:\n${stderr}`)));
    server!.stdout!.on('data', d => {
      const m = /READY (\S+)/.exec(String(d));
      if (m) resolve(m[1]!);
    });
  });
});

afterAll(() => {
  if (server?.pid) {
    try {
      process.kill(-server.pid, 'SIGTERM');
    } catch {
      // already gone
    }
  }
});

describe('interop with the stub agent', () => {
  it('streams a status update, then prose and A2UI the renderer accepts', async () => {
    const processor = createProcessor();
    const agent = await connect(agentUrl, processor.getRendererCapabilities({versions: ['v0.9']}));
    const events: AskEvent[] = [];
    for await (const e of agent.askQuestion('Which suppliers got the most?')) events.push(e);

    expect(events.filter(e => e.kind === 'status').map(e => (e as {text: string}).text)).toContain(STATUS_TEXT);
    const final = events.at(-1)!;
    expect(final.kind).toBe('final');
    if (final.kind !== 'final') return;
    expect(final.prose).toContain('Supplier A');
    expect(final.prose).not.toContain('{r1');
    expect(final.a2ui.length).toBeGreaterThan(0);
    const firstId = surfaceIdOf(final.a2ui)!;
    expect(firstId).toMatch(/^turn-1-1-[0-9a-f]{6}$/);
    for (const m of final.a2ui) {
      const body = Object.values(m).find(v => typeof v === 'object' && v && 'surfaceId' in v) as {surfaceId: string};
      expect(body.surfaceId).toBe(firstId);
    }
    processor.processMessages(final.a2ui as unknown as A2uiMessage[]);
    expect(processor.model.surfacesMap.get(firstId)).toBeDefined();

    // Same context → the next turn gets its own surface.
    const again: AskEvent[] = [];
    for await (const e of agent.askQuestion('And again?', final.contextId)) again.push(e);
    const second = again.at(-1)!;
    expect(second.kind === 'final' && second.contextId).toBe(final.contextId);
    if (second.kind !== 'final') return;
    const secondId = surfaceIdOf(second.a2ui)!;
    expect(secondId).toMatch(/^turn-2-1-[0-9a-f]{6}$/);
    processor.processMessages(second.a2ui as unknown as A2uiMessage[]);
    expect(processor.model.surfacesMap.get(secondId)).toBeDefined();
  });

  it('runs a Drill-down and a Follow-up Ask as A2UI actions (SPEC §15)', async () => {
    const processor = createProcessor();
    const agent = await connect(agentUrl, processor.getRendererCapabilities({versions: ['v0.9']}));
    let first: AskEvent | undefined;
    for await (const e of agent.askQuestion('Which suppliers got the most?')) first = e;
    if (first?.kind !== 'final') throw new Error('no answer');
    const surfaceId = surfaceIdOf(first.a2ui)!;
    const components = first.a2ui.flatMap(m => ((m['updateComponents'] as {components?: Record<string, unknown>[]})?.components ?? []));
    const chart = components.find(c => c['component'] === 'VegaChart')!;
    const action = chart['action'] as {source: string; rows: number[]; drillable: boolean};
    expect(action.drillable).toBe(true);

    const drill = {name: 'drill', surfaceId, sourceComponentId: String(chart['id']), timestamp: new Date().toISOString(),
                   context: {source: action.source, row: action.rows[0]}};
    let out: AskEvent | undefined;
    for await (const e of agent.act(drill, first.contextId)) out = e;
    if (out?.kind !== 'final') throw new Error('no drill-down');
    const level = out.metadata?.['explorerLevel'] as {turn: number; level: number; label: string; surfaceId: string};
    expect([level.turn, level.level, level.label]).toEqual([1, 2, 'Supplier A']);
    expect(out.prose).toBe('');
    processor.processMessages(out.a2ui as unknown as A2uiMessage[]);
    expect(processor.model.surfacesMap.get(level.surfaceId)).toBeDefined();

    const followUp = components.find(c => c['component'] === 'Button')!;
    const ask = {name: 'ask', surfaceId, sourceComponentId: String(followUp['id']), timestamp: new Date().toISOString(),
                 context: (followUp['action'] as {event: {context: Record<string, unknown>}}).event.context};
    let asked: AskEvent | undefined;
    for await (const e of agent.act(ask, first.contextId, [surfaceId])) asked = e;
    if (asked?.kind !== 'final') throw new Error('no ask');
    expect((asked.metadata?.['explorerLevel'] as {level: number}).level).toBe(3);
  });

  it('opens the Explorer Board, then answers a question in the same conversation (SPEC §18)', async () => {
    const processor = createProcessor();
    const agent = await connect(agentUrl, processor.getRendererCapabilities({versions: ['v0.9']}));
    const open = {name: 'open_board', surfaceId: '', sourceComponentId: '', timestamp: new Date().toISOString(),
                  context: {board: 'explorer'}};
    let board: AskEvent | undefined;
    for await (const e of agent.act(open)) board = e;
    if (board?.kind !== 'final') throw new Error('no board');
    expect(board.metadata?.['explorerActionFailed']).toBeUndefined();
    const level = board.metadata?.['explorerLevel'] as {turn: number; level: number; surfaceId: string};
    expect([level.turn, level.level]).toEqual([0, 1]);
    expect(board.metadata?.['explorerModel']).toBeNull();
    processor.processMessages(board.a2ui as unknown as A2uiMessage[]); // the renderer accepts it
    const surface = processor.model.surfacesMap.get(level.surfaceId)!;
    expect(surface.componentsModel.get('board_layout')).toBeDefined();
    expect([...surface.componentsModel.entries].some(([, c]) => c.type === 'VegaChart')).toBe(true);

    let answer: AskEvent | undefined;
    for await (const e of agent.askQuestion('Which suppliers got the most?', board.contextId)) answer = e;
    if (answer?.kind !== 'final') throw new Error('no answer');
    expect(answer.contextId).toBe(board.contextId);
    expect(answer.prose).toContain('Supplier A');
  });
});
