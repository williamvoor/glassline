// Workspaces and saved Conversations in the client (specs/workspaces/SPEC.md phase 1, C1 and C2).
import {afterEach, beforeEach, describe, expect, it} from 'vitest';
import type {AgentConnection, AskEvent, UiAction} from '../src/a2a.js';
import '../src/app.js';
import {LEVEL_KEY, LOG_KEY, OPEN_BOARD, TAB_KEY, type ExplorerApp} from '../src/app.js';
import {EXPLORER_CATALOG_ID} from '../src/catalog.js';
import {
  BOARD_CHANGED, DATA_CHANGED, parseRoute, pickWorkspace, routeHash,
  type SavedLevel, type SavedSummary, type SavedView, type WorkspaceApi, type WorkspaceInfo,
} from '../src/workspaces.js';

const AUS: WorkspaceInfo = {name: 'aus', description: 'Contracts', status: 'available', url: 'http://agent/w/aus'};
const VIC: WorkspaceInfo = {name: 'vic', description: 'Property', status: 'available', url: 'http://agent/w/vic'};
const DOWN: WorkspaceInfo = {name: 'down', description: '', status: 'unavailable', reason: 'database unreachable', url: null};

function surface(id: string, text: string): Record<string, unknown>[] {
  return [
    {version: 'v0.9', createSurface: {surfaceId: id, catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: id, components: [
      {id: 'root', component: 'Column', children: ['t']},
      {id: 't', component: 'Text', text},
    ]}},
  ];
}

const level = (sid: string, text: string, extra: Partial<SavedLevel> = {}): SavedLevel => ({
  prose: '', a2ui: surface(sid, text), changed: false, failed_results: [], board_changed: false, board: null,
  tab: {id: 't1', kind: 'question', title: 'q'}, ...extra,
});

const VIEW: SavedView = {
  id: 'c1', title: 'Top suppliers?', selected: 't2', controls: {}, levels: ['0-1', '1-1', '2-1'],
  tabs: [
    {id: 't1', kind: 'board', title: 'Explorer', level: '0-1', closed: false, log: {kind: 'board', tag: 'NEW TAB · NO MODEL', text: 'Opened Explorer'}},
    {id: 't2', kind: 'question', title: 'Top suppliers?', level: '1-1', closed: false, log: {kind: 'question', tag: 'NEW TAB', text: 'Top suppliers?'}},
    {id: 't3', kind: 'question', title: 'By year?', level: '2-1', closed: true, log: {kind: 'question', tag: 'NEW TAB', text: 'By year?'}},
  ],
  log: [
    {tab: 't1', kind: 'board', text: 'Opened Explorer'},
    {in_place: 'b1', on_tab: 't1', kind: 'controls', tag: 'IN PLACE · NO MODEL', text: 'Year set to 2024-25'},
    {tab: 't2', kind: 'question', text: 'Top suppliers?'},
    {tab: 't3', kind: 'question', text: 'By year?'},
  ],
};

class FakeApi implements WorkspaceApi {
  list_ = [AUS, DOWN, VIC];
  saved: Record<string, SavedSummary[]> = {aus: [{id: 'c1', title: 'Top suppliers?', updated_at: '2026-11-04T10:00:00Z', turns: 2}], vic: []};
  views: Record<string, SavedView> = {c1: VIEW};
  levels: Record<string, SavedLevel> = {
    '0-1': level('turn-0-1-x', 'explorer board'),
    '1-1': level('turn-1-1-x', 'top answer', {changed: true}),
    '2-1': level('turn-2-1-x', 'by year answer'),
  };
  calls: string[] = [];
  async list() { this.calls.push('list'); return this.list_; }
  async reload() { this.calls.push('reload'); this.list_ = [AUS, {...DOWN, status: 'available' as const, url: 'http://agent/w/down', reason: undefined}, VIC]; return this.list_; }
  async conversations(ws: WorkspaceInfo) { this.calls.push(`conversations ${ws.name}`); return this.saved[ws.name] ?? []; }
  async conversation(ws: WorkspaceInfo, id: string) { this.calls.push(`conversation ${ws.name} ${id}`); return this.views[id]; }
  async level(ws: WorkspaceInfo, id: string, lid: string, defaults?: boolean) {
    this.calls.push(`level ${ws.name} ${id} ${lid}${defaults ? ' defaults' : ''}`);
    return this.levels[defaults ? `${lid}:defaults` : lid]!;
  }
  async remove(ws: WorkspaceInfo, id: string) { this.calls.push(`remove ${ws.name} ${id}`); this.saved[ws.name] = (this.saved[ws.name] ?? []).filter(c => c.id !== id); }
  async removeAll(ws: WorkspaceInfo) { this.calls.push(`removeAll ${ws.name}`); this.saved[ws.name] = []; }
}

interface Agent {
  connector: () => Promise<AgentConnection>;
  opened: string[]; // the Workspace each open_board went to
  questions: {text: string; contextId?: string}[];
}

function fakeAgent(app: () => ExplorerApp): Agent {
  const agent: Agent = {connector: async () => connection, opened: [], questions: []};
  const wsName = () => (app() as unknown as {workspace?: WorkspaceInfo}).workspace?.name ?? '';
  const connection: AgentConnection = {
    info: {description: 'Demo', examples: []},
    async *askQuestion(text, contextId) {
      agent.questions.push({text, contextId});
      const sid = `turn-9-1-${wsName()}`;
      yield {kind: 'final', contextId: contextId ?? `new-${wsName()}`, prose: 'Answer', a2ui: surface(sid, 'answer'), metadata: {
        [LEVEL_KEY]: {turn: 9, level: 1, label: 'Overview', surfaceId: sid}, [TAB_KEY]: {id: 't9', kind: 'question', title: text},
        [LOG_KEY]: {kind: 'question', tag: 'NEW TAB', text},
      }} as AskEvent;
    },
    async *act(action: UiAction, contextId) {
      if (action.name !== OPEN_BOARD) return;
      agent.opened.push(wsName());
      const sid = `turn-0-1-${wsName()}`;
      yield {kind: 'final', contextId: contextId ?? `ctx-${wsName()}`, prose: '', a2ui: surface(sid, `board ${wsName()}`), metadata: {
        [LEVEL_KEY]: {turn: 0, level: 1, label: 'Explorer', surfaceId: sid}, [TAB_KEY]: {id: 't1', kind: 'board', title: 'Explorer'},
        [LOG_KEY]: {kind: 'board', tag: 'NEW TAB · NO MODEL', text: 'Opened Explorer'},
      }} as AskEvent;
    },
  };
  return agent;
}

async function settle(app: ExplorerApp) {
  for (let i = 0; i < 12; i++) {
    await new Promise(r => setTimeout(r, 5));
    await app.updateComplete;
  }
}

async function mount(api: FakeApi, hash = ''): Promise<{app: ExplorerApp; agent: Agent}> {
  window.history.replaceState(null, '', hash || window.location.pathname);
  const app = document.createElement('explorer-app') as ExplorerApp;
  const agent = fakeAgent(() => app);
  app.workspaceMode = true;
  app.workspaceApi = api;
  app.connector = agent.connector;
  app.confirmFn = () => true;
  document.body.appendChild(app);
  await settle(app);
  return {app, agent};
}

const q = (app: ExplorerApp, sel: string) => app.querySelector(sel) as HTMLElement | null;
const all = (app: ExplorerApp, sel: string) => [...app.querySelectorAll(sel)] as HTMLElement[];
const text = (el: Element | null | undefined) => el?.textContent?.replace(/\s+/g, ' ').trim();
const titles = (app: ExplorerApp) => all(app, '.tab-strip .tab .tab-title').map(text);
const canvas = (app: ExplorerApp) => text(q(app, '.canvas .tab-view'));

async function click(app: ExplorerApp, el: HTMLElement | null) {
  expect(el).not.toBeNull();
  el!.click();
  await settle(app);
}

beforeEach(() => window.history.replaceState(null, '', window.location.pathname));
afterEach(() => {
  document.body.innerHTML = '';
});

describe('routes', () => {
  it('parses and writes the hash routes', () => {
    expect(parseRoute('#/w/aus')).toEqual({workspace: 'aus'});
    expect(parseRoute('#/w/aus/c/abc-1')).toEqual({workspace: 'aus', conversation: 'abc-1'});
    expect(parseRoute('#/settings')).toEqual({});
    expect(parseRoute('#/w/Bad')).toEqual({});
    expect(routeHash({workspace: 'aus', conversation: 'a b'})).toBe('#/w/aus/c/a%20b');
    expect(pickWorkspace([DOWN, VIC, AUS], 'aus')).toBe(AUS);
    expect(pickWorkspace([DOWN, VIC, AUS], 'down')).toBe(VIC); // unavailable: the first available
  });
});

describe('C1: the picker, unavailable Workspaces, switching and the Conversation list', () => {
  it('shows the picker with unavailable entries greyed out with their reason and Retry', async () => {
    const api = new FakeApi();
    const {app, agent} = await mount(api);
    expect(all(app, '.workspaces .workspace').map(text)).toEqual(['aus', 'down database unreachable Retry', 'vic']);
    expect(q(app, '.workspace.selected')?.textContent?.trim()).toBe('aus');
    expect(q(app, '.workspace.unavailable button.workspace')).toBeNull(); // not selectable
    expect(agent.opened).toEqual(['aus']);
    expect(window.location.hash).toBe('#/w/aus');
    await click(app, q(app, '.retry-workspace'));
    expect(api.calls).toContain('reload');
    expect(all(app, '.workspaces button.workspace').map(text)).toEqual(['aus', 'down', 'vic']);
  });

  it('switches Workspace like New conversation in the target one', async () => {
    const api = new FakeApi();
    const {app, agent} = await mount(api);
    await app.ask('q?');
    await settle(app);
    expect(titles(app)).toEqual(['Explorer', 'q?']);
    await click(app, all(app, '.workspaces button.workspace').find(b => text(b) === 'vic')!);
    expect(agent.opened).toEqual(['aus', 'vic']);
    expect(titles(app)).toEqual(['Explorer']);
    expect(canvas(app)).toContain('board vic');
    expect(window.location.hash).toBe('#/w/vic');
  });

  it('opens a Workspace from its hash route, and a question puts the Conversation in the hash', async () => {
    const api = new FakeApi();
    const {app, agent} = await mount(api, '#/w/vic');
    expect(agent.opened).toEqual(['vic']);
    await app.ask('q?');
    await settle(app);
    expect(window.location.hash).toBe('#/w/vic/c/ctx-vic');
  });

  it('lists, opens and deletes saved Conversations, and deletes all after a confirmation', async () => {
    const api = new FakeApi();
    api.saved['aus'] = [{id: 'c1', title: 'Top suppliers?', updated_at: '2026-11-04T10:00:00Z', turns: 2},
                        {id: 'c2', title: 'Older', updated_at: '2026-11-03T10:00:00Z', turns: 1}];
    const {app} = await mount(api);
    await click(app, q(app, '.saved-toggle'));
    expect(all(app, '.saved-list .open-saved').map(text)).toEqual(['Top suppliers?', 'Older']);
    await click(app, all(app, '.saved-list .delete-saved')[1]!);
    expect(api.calls).toContain('remove aus c2');
    expect(all(app, '.saved-list .open-saved').map(text)).toEqual(['Top suppliers?']);
    let asked = '';
    app.confirmFn = m => ((asked = m), false);
    await click(app, q(app, '.saved-list .delete-all'));
    expect(asked).toContain('Delete all saved conversations in aus');
    expect(api.calls).not.toContain('removeAll aus');
    app.confirmFn = () => true;
    await click(app, q(app, '.saved-list .delete-all'));
    expect(api.calls).toContain('removeAll aus');
    expect(text(q(app, '.saved-list'))).toContain('No saved conversations yet.');
  });

  it('shows no picker or list in single-Dataset mode', async () => {
    window.history.replaceState(null, '', window.location.pathname);
    const app = document.createElement('explorer-app') as ExplorerApp;
    const agent = fakeAgent(() => app);
    app.connector = agent.connector;
    document.body.appendChild(app);
    await settle(app);
    expect(q(app, '.workspaces')).toBeNull();
    expect(q(app, '.saved-toggle')).toBeNull();
    expect(window.location.hash).toBe('');
  });
});

describe('C2: reopening a saved Conversation', () => {
  it('rebuilds Tabs and Log entries, fetches only the selected Level, and others on first show', async () => {
    const api = new FakeApi();
    const {app, agent} = await mount(api, '#/w/aus/c/c1');
    expect(agent.opened).toEqual([]); // no agent work to reopen
    expect(titles(app)).toEqual(['Explorer', 'Top suppliers?']); // t3 was closed
    expect(all(app, '.sidebar .log-entry .log-text').map(text)).toEqual(['Opened Explorer', 'Year set to 2024-25', 'Top suppliers?', 'By year?']);
    expect(text(q(app, '.tab-strip .tab.selected .tab-title'))).toBe('Top suppliers?');
    expect(api.calls.filter(c => c.startsWith('level'))).toEqual(['level aus c1 1-1']);
    expect(canvas(app)).toContain('top answer');
    expect(text(q(app, '.notice.data-changed'))).toBe(DATA_CHANGED);

    await click(app, all(app, '.tab-strip .tab-select')[0]!);
    expect(api.calls.filter(c => c.startsWith('level'))).toEqual(['level aus c1 1-1', 'level aus c1 0-1']);
    expect(canvas(app)).toContain('explorer board');
    expect(q(app, '.notice.data-changed')).toBeNull();
    await click(app, all(app, '.tab-strip .tab-select')[1]!); // already loaded: not fetched again
    expect(api.calls.filter(c => c.startsWith('level'))).toHaveLength(2);
    await click(app, all(app, '.sidebar .log-entry').at(-1)!); // the closed tab reopens from the log
    expect(api.calls.filter(c => c.startsWith('level')).at(-1)).toBe('level aus c1 2-1');
    expect(canvas(app)).toContain('by year answer');
  });

  it('asks on in the saved Conversation', async () => {
    const api = new FakeApi();
    const {app, agent} = await mount(api, '#/w/aus/c/c1');
    await app.ask('more?');
    await settle(app);
    expect(agent.questions).toEqual([{text: 'more?', contextId: 'c1'}]);
    expect(window.location.hash).toBe('#/w/aus/c/c1');
  });

  it('shows "This Board has changed since." with a button that opens the current Board', async () => {
    const api = new FakeApi();
    api.views['c1'] = {...VIEW, selected: 't1'};
    api.levels['0-1'] = {...level('turn-0-1-x', 'old'), a2ui: [], board_changed: true};
    api.levels['0-1:defaults'] = level('turn-0-1-x', 'current board');
    const {app} = await mount(api, '#/w/aus/c/c1');
    expect(text(q(app, '.notice.board-changed'))).toContain(BOARD_CHANGED);
    expect(canvas(app)).not.toContain('old');
    await click(app, q(app, '.notice.board-changed .open-current'));
    expect(api.calls).toContain('level aus c1 0-1 defaults');
    expect(q(app, '.notice.board-changed')).toBeNull();
    expect(canvas(app)).toContain('current board');
  });

  it('opens a saved Conversation from the list, and an unknown one starts a new Conversation', async () => {
    const api = new FakeApi();
    const {app, agent} = await mount(api, '#/w/aus/c/gone');
    expect(agent.opened).toEqual(['aus']);
    expect(window.location.hash).toBe('#/w/aus');
    await click(app, q(app, '.saved-toggle'));
    await click(app, q(app, '.saved-list .open-saved'));
    expect(titles(app)).toEqual(['Explorer', 'Top suppliers?']);
    expect(window.location.hash).toBe('#/w/aus/c/c1');
  });
});
