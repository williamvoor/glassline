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
