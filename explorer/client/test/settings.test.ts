// Settings (specs/workspaces/SPEC.md phase 2, S5 and D6): the bootstrap steps inside the explorer,
// with a confirmation before every model call.
import {afterEach, describe, expect, it} from 'vitest';
import {Api} from '../src/settings/api.js';
import '../src/settings/settings.js';
import {BOOTSTRAP_URL, type ExplorerSettings} from '../src/settings/settings.js';
import '../src/app.js';
import type {ExplorerApp} from '../src/app.js';
import type {WorkspaceApi, WorkspaceInfo} from '../src/workspaces.js';
import {REPORT, claim, fakeFetch, makeDraft, settle} from './settings/helpers.js';

const ESTIMATE = {model: 'meta/muse-wonder-1.0', input_tokens_est: 1000, first_call_usd: 0.01, run_ceiling_usd: 0.12, max_calls: 12};
const VALID = {errors: [], context_chars: 10};

function routes(over: Record<string, (body: any) => [number, unknown]> = {}) {
  const draft = makeDraft([claim('verified')]);
  return fakeFetch({
    'GET /api/workspaces': () => [200, {workspaces: [
      {name: 'aus', dsn_env: 'AUS_DATABASE_URL', kind: 'repo'},
      {name: 'mine', dsn_env: 'MINE_DATABASE_URL', kind: 'data'},
    ]}],
    'GET /api/connections': () => [200, {connections: ['VIC_DATABASE_URL'], current: null}],
    'GET /api/models': () => [200, {models: ['meta/muse-wonder-1.0', 'meta/muse-spark-1.3'], default: 'meta/muse-wonder-1.0'}],
    'POST /api/connect': () => [200, {host: 'h', name: 'vic', dsn_env: 'VIC_DATABASE_URL', schemas: [{name: 's', objects: 2}]}],
    'POST /api/report': () => [200, REPORT],
    'POST /api/estimate': () => [200, ESTIMATE],
    'POST /api/draft': () => [200, {run_id: 'd1'}],
    'GET /api/draft/d1/events': () => [200, {events: [], next: 0, done: true, draft, validation: VALID}],
    'POST /api/validate': () => [200, VALID],
    'POST /api/export': () => [200, {path: 'workspaces/vic/dataset', workspace: 'vic', overwrote: [], backup: null, reloaded: true}],
    'POST /api/workspaces/mine/delete': () => [200, {deleted: 'mine', reloaded: true, workspaces: []}],
    'POST /api/workspaces/aus/delete': () => [200, {deleted: 'aus', reloaded: true, workspaces: []}],
    'POST /api/workspaces/mine/regenerate': () => [200, REPORT],
    ...over,
  });
}

async function mountSettings(f: ReturnType<typeof fakeFetch>, confirm: (m: string) => boolean = () => true) {
  const el = document.createElement('explorer-settings') as ExplorerSettings;
  el.api = new Api(f.fn);
  el.pollMs = 0;
  el.confirmFn = confirm;
  document.body.appendChild(el);
  await settle(el);
  return el;
}

const paths = (f: ReturnType<typeof fakeFetch>) => f.calls.map(c => c.path);
const current = (el: Element) => el.querySelector('.steps li.current')?.textContent?.trim();
const click = async (el: Element, sel: string) => {
  const b = el.querySelector(sel) as HTMLButtonElement | null;
  expect(b, sel).not.toBeNull();
  b!.click();
  await settle(el, 15);
};

afterEach(() => {
  document.body.innerHTML = '';
  window.history.replaceState(null, '', window.location.pathname);
});

describe('S5: the Settings views', () => {
  it('lists Workspaces and the unused logins by name, and talks to the bootstrap server', async () => {
    const f = routes();
    const el = await mountSettings(f);
    expect(current(el)).toBe('Workspaces');
    const rows = [...el.querySelectorAll('.settings-workspaces tbody tr')].map(r => r.textContent!.replace(/\s+/g, ' ').trim());
    expect(rows[0]).toContain('aus AUS_DATABASE_URL in the repo');
    expect(rows[0]).not.toContain('Regenerate'); // repo Datasets: read-only
    expect(rows[1]).toContain('mine MINE_DATABASE_URL made in Settings');
    expect(el.querySelectorAll('.settings-workspaces tbody tr')[1]!.querySelector('.regenerate')).not.toBeNull();
    expect([...el.querySelectorAll('.connect-var')].map(b => b.textContent!.trim())).toEqual(['VIC_DATABASE_URL']);
    expect(f.calls[0]!.path).toBe('GET /api/workspaces');
    expect(BOOTSTRAP_URL).toBe('http://127.0.0.1:8765'); // the default server
  });

  it('covers connect, report, draft, review and export; the draft waits for a confirmation', async () => {
    const f = routes();
    let asked = '';
    let answer = false;
    const el = await mountSettings(f, m => ((asked = m), answer));
    await click(el, '.connect-var');
    expect(f.calls.find(c => c.path === 'POST /api/connect')?.body).toEqual({dsn_env: 'VIC_DATABASE_URL'});
    expect(current(el)).toBe('Connect');
    await el.runReport({schemas: ['s']}); // as bs-connect fires it
    await settle(el);
    expect(current(el)).toBe('Report');

    await el.startDraft('meta/muse-wonder-1.0', 'low'); // as bs-report fires it: refused
    await settle(el);
    expect(asked).toContain('meta/muse-wonder-1.0');
    expect(asked).toContain('$0.12');
    expect(paths(f)).not.toContain('POST /api/draft');
    expect(current(el)).toBe('Report');

    answer = true;
    await el.startDraft('meta/muse-wonder-1.0', 'low');
    await settle(el, 15);
    expect(paths(f)).toContain('POST /api/draft');
    expect(current(el)).toBe('Review');
    // Workspace export: a Workspace name, comments always go to comments.toml (no target choice).
    expect(el.querySelector('bs-review fieldset.target')).toBeNull();
    expect(el.querySelector('bs-review input.name')?.closest('label')?.textContent).toContain('Workspace name');
    let changed = 0;
    el.addEventListener('workspaces-changed', () => changed++);
    const name = el.querySelector('bs-review input.name') as HTMLInputElement;
    name.value = 'vic';
    name.dispatchEvent(new Event('input'));
    await settle(el, 15);
    await click(el, 'bs-review button.export');
    expect(f.calls.find(c => c.path === 'POST /api/export')!.body.name).toBe('vic');
    expect(el.querySelector('.notice-settings')?.textContent).toContain('vic is ready');
    expect(changed).toBe(1);
  });

  it('asks before a Boards draft too', async () => {
    const f = routes({
      'POST /api/boards/estimate': () => [200, ESTIMATE],
      'POST /api/boards/draft': () => [200, {run_id: 'b1'}],
    });
    const asked: string[] = [];
    const el = await mountSettings(f, m => (asked.push(m), false));
    await el.startBoards('meta/muse-spark-1.3', 'low');
    await settle(el);
    expect(asked).toHaveLength(1);
    expect(asked[0]).toContain('Draft the Boards with meta/muse-spark-1.3');
    expect(paths(f)).not.toContain('POST /api/boards/draft');
  });

  it('deletes after a confirmation that names what is deleted, and regenerates a data-directory Dataset', async () => {
    const f = routes();
    const asked: string[] = [];
    let ok = false;
    const el = await mountSettings(f, m => (asked.push(m), ok));
    const deleteButtons = () => [...el.querySelectorAll('.delete-workspace')] as HTMLButtonElement[];
    deleteButtons()[0]!.click(); // aus: repo
    await settle(el);
    expect(asked[0]).toContain("the repo Dataset's files are not touched");
    expect(paths(f)).not.toContain('POST /api/workspaces/aus/delete');
    ok = true;
    deleteButtons()[1]!.click(); // mine: data
    await settle(el, 15);
    expect(asked[1]).toContain('its Dataset and its saved Conversations');
    expect(paths(f)).toContain('POST /api/workspaces/mine/delete');
    expect(el.querySelector('.notice-settings')?.textContent).toContain('Deleted mine');

    const f2 = routes();
    const el2 = await mountSettings(f2);
    (el2.querySelectorAll('.regenerate')[0] as HTMLButtonElement).click();
    await settle(el2, 15);
    expect(paths(f2)).toContain('POST /api/workspaces/mine/regenerate');
    expect(current(el2)).toBe('Report');
    expect(el2.querySelector('.regenerating')?.textContent).toContain('mine');
  });

  it('re-runs a regenerate report as a regenerate, never with an earlier connection (refuter F3)', async () => {
    const f = routes();
    const el = await mountSettings(f);
    await click(el, '.connect-var');
    await el.runReport({schemas: ['s']});
    await settle(el);
    await click(el, '.to-workspaces');
    (el.querySelectorAll('.regenerate')[0] as HTMLButtonElement).click();
    await settle(el, 15);
    expect(el.selection).toBeNull();
    const before = paths(f).filter(p => p === 'POST /api/workspaces/mine/regenerate').length;
    await el.rerunReport();
    await settle(el, 15);
    expect(paths(f).filter(p => p === 'POST /api/workspaces/mine/regenerate').length).toBe(before + 1);
  });

  it('reloads the agent from Settings (refuter F5)', async () => {
    const f = routes({'POST /api/reload': () => [200, {reloaded: true, status: 200}]});
    const el = await mountSettings(f);
    let changed = 0;
    el.addEventListener('workspaces-changed', () => changed++);
    await click(el, '.reload-agent');
    expect(paths(f)).toContain('POST /api/reload');
    expect(el.querySelector('.notice-settings')?.textContent).toContain('reloaded');
    expect(changed).toBe(1);
  });

  it('shows a refused login as an error and stays on the Workspaces screen', async () => {
    const f = routes({'POST /api/connect': () => [403, {error: 'this login can write or administer the database; use a read-only role'}]});
    const el = await mountSettings(f);
    await click(el, '.connect-var');
    expect(el.querySelector('.error')?.textContent).toContain('read-only role');
    expect(current(el)).toBe('Workspaces');
  });
});

describe('D6: Settings in the explorer', () => {
  const AUS: WorkspaceInfo = {name: 'aus', description: '', status: 'available', url: 'http://agent/w/aus'};

  function api(list: WorkspaceInfo[]): WorkspaceApi & {lists: number} {
    const a = {
      lists: 0,
      list: async () => (a.lists++, list),
      reload: async () => list,
      conversations: async () => [],
      conversation: async () => undefined,
      level: async () => { throw new Error('none'); },
      remove: async () => {},
      removeAll: async () => {},
    };
    return a;
  }

  it('opens Settings from the header at #/settings, refreshes the picker after a change, and goes back', async () => {
    const app = document.createElement('explorer-app') as ExplorerApp;
    app.workspaceMode = true;
    const a = api([AUS]);
    app.workspaceApi = a;
    app.connector = async () => ({info: {description: '', examples: []}, async *askQuestion() {}, async *act() {}});
    document.body.appendChild(app);
    await settle(app);
    expect(app.querySelector('explorer-settings')).toBeNull();
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect(window.location.hash).toBe('#/settings');
    const settings = app.querySelector('explorer-settings')!;
    expect(settings).not.toBeNull();
    const before = a.lists;
    settings.dispatchEvent(new CustomEvent('workspaces-changed', {bubbles: true}));
    await settle(app);
    expect(a.lists).toBe(before + 1);
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect((app.querySelector('explorer-settings') as HTMLElement).hidden).toBe(true);
    expect(window.location.hash).toBe('#/w/aus');
  });

  it('keeps Settings and its draft when it closes and reopens (refuter F1)', async () => {
    const app = document.createElement('explorer-app') as ExplorerApp;
    app.workspaceMode = true;
    app.workspaceApi = api([AUS]);
    app.connector = async () => ({info: {description: '', examples: []}, async *askQuestion() {}, async *act() {}});
    document.body.appendChild(app);
    await settle(app);
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    const first = app.querySelector('explorer-settings') as ExplorerSettings;
    first.step = 'review';
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect((app.querySelector('explorer-settings') as HTMLElement).hidden).toBe(true);
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect(app.querySelector('explorer-settings')).toBe(first);
    expect(first.step).toBe('review');
  });

  it('opened at #/settings, closing it opens a Workspace (refuter F4)', async () => {
    window.history.replaceState(null, '', '#/settings');
    const app = document.createElement('explorer-app') as ExplorerApp;
    app.workspaceMode = true;
    app.workspaceApi = api([AUS]);
    let opened = 0;
    app.connector = async () => ({info: {description: '', examples: []}, async *askQuestion() {}, async *act() { opened++; }});
    document.body.appendChild(app);
    await settle(app);
    expect(app.querySelector('explorer-settings')).not.toBeNull();
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect(window.location.hash).toBe('#/w/aus');
    expect(opened).toBe(1);
  });

  it('shows no Settings button in single-Dataset mode', async () => {
    const app = document.createElement('explorer-app') as ExplorerApp;
    app.connector = async () => ({info: {description: '', examples: []}, async *askQuestion() {}, async *act() {}});
    document.body.appendChild(app);
    await settle(app);
    expect(app.querySelector('.settings-toggle')).toBeNull();
  });
});
