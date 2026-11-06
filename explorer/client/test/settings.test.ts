// Settings (specs/workspaces/SPEC.md phases 2 and 3, D6, N1, N9, N11): the Workspace list, the New
// Workspace wizard behind it, and the app's routes. No pop-ups: Delete asks on a card in its row.
import {afterEach, describe, expect, it} from 'vitest';
import {Api} from '../src/settings/api.js';
import '../src/settings/settings.js';
import {BOOTSTRAP_URL, type ExplorerSettings} from '../src/settings/settings.js';
import '../src/app.js';
import type {ExplorerApp} from '../src/app.js';
import type {WorkspaceApi, WorkspaceInfo} from '../src/workspaces.js';
import {REPORT, fakeFetch, settle} from './settings/helpers.js';

const ESTIMATE = {model: 'meta/muse-wonder-1.0', prompt_chars: 4000, input_tokens_est: 1000, first_call_usd: 0.01, run_ceiling_usd: 0.12, max_calls: 12};
const EMPTY_SESSION = {dsn_env: null, report: null, regenerating: null, draft: false, boards: false, run: null};

function routes(over: Record<string, (body: any) => [number, unknown]> = {}) {
  return fakeFetch({
    'GET /api/workspaces': () => [200, {workspaces: [
      {name: 'aus', dsn_env: 'AUS_DATABASE_URL', kind: 'repo'},
      {name: 'mine', dsn_env: 'MINE_DATABASE_URL', kind: 'data'},
    ]}],
    'GET /api/connections': () => [200, {connections: ['VIC_DATABASE_URL'], current: null}],
    'GET /api/models': () => [200, {models: ['meta/muse-wonder-1.0', 'meta/muse-spark-1.3'], default: 'meta/muse-wonder-1.0'}],
    'GET /api/session': () => [200, EMPTY_SESSION],
    'POST /api/estimate': () => [200, ESTIMATE],
    'POST /api/workspaces/mine/delete': () => [200, {deleted: 'mine', reloaded: true, workspaces: []}],
    'POST /api/workspaces/aus/delete': () => [200, {deleted: 'aus', reloaded: true, workspaces: []}],
    'POST /api/workspaces/mine/regenerate': () => [200, REPORT],
    ...over,
  });
}

async function mountSettings(f: ReturnType<typeof fakeFetch>) {
  const el = document.createElement('explorer-settings') as ExplorerSettings;
  el.api = new Api(f.fn);
  el.pollMs = 0;
  document.body.appendChild(el);
  await settle(el);
  return el;
}

const paths = (f: ReturnType<typeof fakeFetch>) => f.calls.map(c => c.path);
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

describe('N1: Settings is the Workspace list; New Workspace opens the wizard', () => {
  it('lists Workspaces, talks to the bootstrap server, and opens the wizard', async () => {
    const f = routes();
    const el = await mountSettings(f);
    const rows = [...el.querySelectorAll('.settings-workspaces tbody tr')].map(r => r.textContent!.replace(/\s+/g, ' ').trim());
    expect(rows[0]).toContain('aus AUS_DATABASE_URL in the repo');
    expect(rows[0]).not.toContain('Regenerate'); // repo Datasets: read-only
    expect(rows[1]).toContain('mine MINE_DATABASE_URL made in Settings');
    expect(el.querySelector('.settings-workspaces .connect-var')).toBeNull(); // logins are the wizard's now
    expect(f.calls[0]!.path).toBe('GET /api/workspaces');
    expect(BOOTSTRAP_URL).toBe('http://127.0.0.1:8765');

    const views: string[] = [];
    el.addEventListener('settings-view', e => views.push((e as CustomEvent).detail));
    await click(el, 'button.new-workspace');
    expect(views).toEqual(['wizard']);
    expect((el.querySelector('.settings-workspaces') as HTMLElement).hidden).toBe(true);
    expect((el.querySelector('explorer-wizard') as HTMLElement).hidden).toBe(false);
    expect(paths(f)).toContain('GET /api/session'); // the wizard matches the server's state
    expect([...el.querySelectorAll('explorer-wizard .connect-var')].map(b => b.textContent!.trim())).toEqual(['VIC_DATABASE_URL']);
  });

  it('deletes after a card that names what is deleted; Cancel deletes nothing (N11)', async () => {
    const f = routes();
    const el = await mountSettings(f);
    const deleteButtons = () => [...el.querySelectorAll('.delete-workspace')] as HTMLButtonElement[];
    deleteButtons()[0]!.click(); // aus: repo
    await settle(el);
    expect(el.querySelector('.delete-row .confirm-card')!.textContent).toContain("the repo Dataset's files are not touched");
    await click(el, '.delete-row .card-cancel');
    expect(el.querySelector('.confirm-card')).toBeNull();
    expect(paths(f)).not.toContain('POST /api/workspaces/aus/delete');
    deleteButtons()[1]!.click(); // mine: data
    await settle(el);
    expect(el.querySelector('.delete-row .confirm-card')!.textContent).toContain('its Dataset and its saved Conversations');
    await click(el, '.delete-row .card-confirm');
    expect(paths(f)).toContain('POST /api/workspaces/mine/delete');
    expect(el.querySelector('.notice-settings')?.textContent).toContain('Deleted mine');
  });

  it('regenerates a data-directory Dataset in the wizard, at the Draft step (N8)', async () => {
    const f = routes();
    const el = await mountSettings(f);
    await click(el, '.regenerate');
    expect(paths(f)).toContain('POST /api/workspaces/mine/regenerate');
    expect(el.view).toBe('wizard');
    const wizard = el.querySelector('explorer-wizard')!;
    expect(wizard.querySelector('.wizard-steps li.current')!.textContent).toContain('Draft');
    expect(wizard.querySelector('.wizard-steps li.locked')!.textContent).toContain('Database');
    expect(wizard.querySelector('.wizard-steps li.locked button')).toBeNull(); // step 1 can't be opened
    expect(wizard.querySelector('.wizard-head h1')!.textContent).toContain('Regenerate mine');
  });

  it('disables Regenerate while a New Workspace is in progress, and offers to continue it (N18, E13)', async () => {
    const f = routes({
      'POST /api/connect': () => [200, {host: 'h', name: 'vic', dsn_env: 'VIC_DATABASE_URL', schemas: [{name: 's', objects: 2}]}],
    });
    const el = await mountSettings(f);
    await click(el, 'button.new-workspace');
    await click(el, 'explorer-wizard .connect-var'); // a login held: work in progress
    await click(el, 'explorer-wizard button.discard'); // Cancel (no Draft): back to the list …
    expect(el.view).toBe('list');
    expect((el.querySelector('.regenerate') as HTMLButtonElement).disabled).toBe(false); // … with nothing held

    await click(el, 'button.new-workspace');
    await click(el, 'explorer-wizard .connect-var');
    el.show('list');
    await settle(el, 15);
    const regenerate = el.querySelector('.regenerate') as HTMLButtonElement;
    expect(regenerate.disabled).toBe(true);
    expect(regenerate.title).toContain('Finish or discard the New Workspace first');
    expect(el.querySelector('button.new-workspace')!.textContent!.trim()).toBe('Continue the New Workspace');
    expect(paths(f)).not.toContain('POST /api/workspaces/mine/regenerate');
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
});

describe('D6: Settings in the explorer', () => {
  const AUS: WorkspaceInfo = {name: 'aus', description: '', status: 'available', url: 'http://agent/w/aus'};
  const VIC: WorkspaceInfo = {name: 'vic', description: '', status: 'available', url: 'http://agent/w/vic'};

  function api(list: WorkspaceInfo[]): WorkspaceApi & {lists: number; current: WorkspaceInfo[]} {
    const a = {
      lists: 0,
      current: list,
      list: async () => (a.lists++, a.current),
      reload: async () => a.current,
      conversations: async () => [],
      conversation: async () => undefined,
      level: async () => { throw new Error('none'); },
      remove: async () => {},
      removeAll: async () => {},
    };
    return a;
  }

  function mountApp(a: WorkspaceApi, opened?: () => void) {
    const app = document.createElement('explorer-app') as ExplorerApp;
    app.workspaceApi = a;
    app.connector = async () => ({info: {description: '', examples: []}, async *askQuestion() {}, async *act() { opened?.(); }});
    document.body.appendChild(app);
    return app;
  }

  it('opens Settings from the header at #/settings, refreshes the picker after a change, and goes back', async () => {
    const a = api([AUS]);
    const app = mountApp(a);
    await settle(app);
    expect(app.querySelector('explorer-settings')).toBeNull();
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect(window.location.hash).toBe('#/settings');
    const settings = app.querySelector('explorer-settings')!;
    const before = a.lists;
    settings.dispatchEvent(new CustomEvent('workspaces-changed', {bubbles: true}));
    await settle(app);
    expect(a.lists).toBe(before + 1);
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect((app.querySelector('explorer-settings') as HTMLElement).hidden).toBe(true);
    expect(window.location.hash).toBe('#/w/aus');
  });

  it('writes #/settings/new for the wizard, and opens it from that hash (E1)', async () => {
    const app = mountApp(api([AUS]));
    await settle(app);
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    const settings = app.querySelector('explorer-settings') as ExplorerSettings;
    settings.show('wizard');
    await settle(app);
    expect(window.location.hash).toBe('#/settings/new');
    settings.show('list');
    await settle(app);
    expect(window.location.hash).toBe('#/settings');

    document.body.innerHTML = '';
    window.history.replaceState(null, '', '#/settings/new');
    const second = mountApp(api([AUS]));
    await settle(second);
    expect((second.querySelector('explorer-settings') as ExplorerSettings).view).toBe('wizard');
  });

  it('keeps Settings and its wizard when it closes and reopens (refuter F1)', async () => {
    const app = mountApp(api([AUS]));
    await settle(app);
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    const first = app.querySelector('explorer-settings') as ExplorerSettings;
    const wizard = first.wizard!;
    wizard.step = 'review';
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect((app.querySelector('explorer-settings') as HTMLElement).hidden).toBe(true);
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect(app.querySelector('explorer-settings')).toBe(first);
    expect(first.wizard).toBe(wizard);
    expect(wizard.step).toBe('review');
  });

  it('opened at #/settings, closing it opens a Workspace (refuter F4)', async () => {
    window.history.replaceState(null, '', '#/settings');
    let opened = 0;
    const app = mountApp(api([AUS]), () => opened++);
    await settle(app);
    expect(app.querySelector('explorer-settings')).not.toBeNull();
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    expect(window.location.hash).toBe('#/w/aus');
    expect(opened).toBe(1);
  });

  it('"Open <name>" after an export switches to the new Workspace (N9)', async () => {
    const a = api([AUS]);
    const app = mountApp(a);
    await settle(app);
    (app.querySelector('.settings-toggle') as HTMLButtonElement).click();
    await settle(app);
    a.current = [AUS, VIC]; // the export added it and the agent reloaded
    app.querySelector('explorer-settings')!.dispatchEvent(new CustomEvent('open-workspace', {detail: 'vic', bubbles: true}));
    await settle(app, 15);
    expect(window.location.hash).toBe('#/w/vic');
    expect((app.querySelector('explorer-settings') as HTMLElement).hidden).toBe(true);
  });
});
