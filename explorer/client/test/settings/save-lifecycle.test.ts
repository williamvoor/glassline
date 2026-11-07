import {afterEach, expect, it, vi} from 'vitest';
import {Api} from '../../src/settings/api.js';
import '../../src/settings/wizard.js';
import type {ExplorerWizard} from '../../src/settings/wizard.js';
import type {BsBoards} from '../../src/settings/boards.js';
import {fakeFetch, makeDraft} from './helpers.js';

afterEach(() => { document.body.innerHTML = ''; vi.useRealTimers(); });

const state = () => ({boards: [{name: 'explorer', toml: 'old', why: 'Why'}], problems: {}, set_problems: [], preview: {}, usage: null});

async function mount() {
  vi.useFakeTimers();
  let saved = 'old', exported = '';
  const f = fakeFetch({
    'POST /api/validate': () => [200, {errors: [], context_chars: 1}],
    'POST /api/boards/estimate': () => [200, {}],
    'POST /api/boards/validate': b => { saved = b.boards[0].toml; return [200, state()]; },
    'POST /api/export': () => { exported = saved; return [200, {workspace: 'demo', reloaded: true}]; },
  });
  const wizard = document.createElement('explorer-wizard') as ExplorerWizard;
  wizard.api = new Api(f.fn);
  Object.assign(wizard, {draft: makeDraft([]), boards: state(), validation: {errors: [], context_chars: 1}, step: 'boards', reached: 3, name: 'demo'});
  document.body.appendChild(wizard);
  await wizard.updateComplete;
  const editor = wizard.querySelector('bs-boards') as BsBoards;
  await editor.updateComplete;
  const edit = (text: string) => {
    const area = editor.querySelector('textarea')!;
    area.value = text;
    area.dispatchEvent(new Event('input'));
  };
  return {wizard, editor, edit, f, saved: () => saved, exported: () => exported};
}

it.each(['review', 'export'] as const)('flushes once before leaving Boards for %s', async step => {
  const {wizard, edit, f, saved, exported} = await mount();
  edit('latest');
  await vi.advanceTimersByTimeAsync(399);
  expect(saved()).toBe('old');
  await wizard.goTo(step);
  await wizard.updateComplete;
  expect(saved()).toBe('latest');
  if (step === 'export') {
    await wizard.exportNow();
    expect(exported()).toBe('latest');
  }
  await vi.advanceTimersByTimeAsync(400);
  expect(f.calls.filter(c => c.path === 'POST /api/boards/validate')).toHaveLength(1);
});

it('waits for a running save, then writes and flushes the latest edit', async () => {
  const {wizard, editor, edit} = await mount();
  const requests: string[] = [];
  let resolve!: (value: ReturnType<typeof state>) => void;
  editor.api.validateBoards = async boards => {
    requests.push(boards[0].toml);
    if (requests.length === 1) return new Promise(ok => {resolve = ok;});
    return state();
  };
  edit('first');
  await vi.advanceTimersByTimeAsync(400);
  edit('latest');
  const navigation = wizard.goTo('export');
  await Promise.resolve();
  expect(requests).toEqual(['first']);
  expect(editor.isConnected).toBe(true);
  resolve(state());
  await navigation;
  await wizard.updateComplete;
  expect(requests).toEqual(['first', 'latest']);
  expect(editor.isConnected).toBe(false);
});

it('keeps the editor and its error when flushing fails', async () => {
  const {wizard, editor, edit} = await mount();
  editor.api.validateBoards = async () => { throw new Error('save failed'); };
  edit('latest');
  await wizard.goTo('export');
  await editor.updateComplete;
  expect(editor.isConnected).toBe(true);
  expect(editor.querySelector('[role=alert]')!.textContent).toContain('save failed');
});

it('cancels a pending save when the editor disconnects', async () => {
  const {wizard, edit, f} = await mount();
  edit('latest');
  wizard.remove();
  await vi.advanceTimersByTimeAsync(400);
  expect(f.calls.filter(c => c.path === 'POST /api/boards/validate')).toHaveLength(0);
});

it('stays on Boards when flushing discovers invalid edits', async () => {
  const {wizard, editor, edit} = await mount();
  editor.api.validateBoards = async () => ({...state(), problems: {explorer: ['invalid format']}});
  edit('invalid');
  await wizard.goTo('export');
  await wizard.updateComplete;
  expect(editor.isConnected).toBe(true);
});

async function enterBoardsWithRecheck() {
  const {wizard} = await mount();
  await wizard.goTo('review');
  await wizard.updateComplete;
  const requests: string[] = [];
  let saved = 'old';
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const recheck = new Promise<void>((ok, fail) => { resolve = ok; reject = fail; });
  wizard.api.validateBoards = async boards => {
    const toml = boards[0].toml;
    requests.push(toml);
    if (requests.length === 1) await recheck;
    saved = toml;
    return {...state(), boards};
  };
  const entry = wizard.goTo('boards');
  await vi.advanceTimersByTimeAsync(0);
  const editor = wizard.querySelector('bs-boards') as BsBoards;
  await editor.updateComplete;
  expect(requests).toEqual(['old']);
  const area = editor.querySelector('textarea')!;
  const edit = (toml: string) => {
    area.value = toml;
    area.dispatchEvent(new Event('input'));
  };
  return {wizard, editor, area, edit, requests, resolve, reject, entry, saved: () => saved};
}

it.each([false, true])('preserves edits made during the Board entry recheck (debounce finishes first=%s)', async debounceFirst => {
  const {wizard, editor, area, edit, requests, resolve, entry, saved} = await enterBoardsWithRecheck();
  edit('latest');
  if (debounceFirst) await vi.advanceTimersByTimeAsync(400);
  expect(requests).toEqual(['old']); // the recheck and edit share one save queue
  resolve();
  await entry;
  await wizard.updateComplete;
  await editor.updateComplete;
  expect(area.value).toBe('latest');
  await vi.advanceTimersByTimeAsync(400);
  expect(requests).toEqual(['old', 'latest']);
  expect(saved()).toBe('latest');
  expect(wizard.boards!.boards[0].toml).toBe('latest');
  expect(area.value).toBe('latest');
});

it('preserves edits and permits retry after a Board entry recheck fails', async () => {
  const {wizard, editor, area, edit, reject, entry, saved} = await enterBoardsWithRecheck();
  reject(new Error('recheck failed'));
  await entry;
  await editor.updateComplete;
  expect(area.value).toBe('old');
  expect(editor.querySelector('[role=alert]')!.textContent).toContain('recheck failed');
  edit('latest');
  await vi.advanceTimersByTimeAsync(400);
  await wizard.updateComplete;
  await editor.updateComplete;
  expect(saved()).toBe('latest');
  expect(area.value).toBe('latest');
  expect(editor.querySelector('[role=alert]')).toBeNull();
});

it('flushes edits before Export while a Board entry recheck runs', async () => {
  const {wizard, editor, edit, requests, resolve, entry, saved} = await enterBoardsWithRecheck();
  edit('latest');
  const leaving = wizard.goTo('export');
  await vi.advanceTimersByTimeAsync(0);
  expect(editor.isConnected).toBe(true);
  expect(requests).toEqual(['old']);
  resolve();
  await entry;
  await leaving;
  await wizard.updateComplete;
  expect(requests).toEqual(['old', 'latest']);
  expect(saved()).toBe('latest');
  expect(wizard.step).toBe('export');
  expect(editor.isConnected).toBe(false);
});

it('leaves Boards when a later edit supersedes the save queued by leaving', async () => {
  const {wizard, editor, edit} = await mount();
  const requests: string[] = [];
  let resolve!: (value: ReturnType<typeof state>) => void;
  editor.api.validateBoards = async boards => {
    requests.push(boards[0].toml);
    if (requests.length === 1) return new Promise(ok => {resolve = ok;});
    return state();
  };
  edit('first');
  await vi.advanceTimersByTimeAsync(400); // a save is running
  edit('queued');
  const navigation = wizard.goTo('review'); // queues 'queued' behind it
  await Promise.resolve();
  edit('newest'); // supersedes the queued save before it starts
  resolve(state());
  await navigation;
  await wizard.updateComplete;
  expect(requests).toEqual(['first', 'newest']);
  expect(wizard.step).toBe('review');
});

it.each([
  ['before any Boards are drafted', {boards: null}],
  ['while Boards are being drafted', {boardsRunning: true}],
])('Back leaves the Boards step %s without saving', async (_label, over) => {
  const {wizard, f} = await mount();
  f.calls.length = 0;
  Object.assign(wizard, over);
  await wizard.updateComplete;
  wizard.api.validateBoards = async () => { throw new Error('no Boards yet'); }; // what the server says (404/409)
  await wizard.goTo('review');
  await wizard.updateComplete;
  expect(wizard.step).toBe('review');
});
