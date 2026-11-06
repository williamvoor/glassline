import { afterEach, describe, expect, it } from 'vitest';

import { Api } from '../../src/settings/api.js';
import '../../src/settings/settings.js';
import '../../src/settings/boards.js';
import type { ExplorerSettings } from '../../src/settings/settings.js';
import type { BsBoards } from '../../src/settings/boards.js';
import type { BoardsState } from '../../src/settings/types.js';
import { boardProblems } from '../../src/settings/types.js';
import { claim, fakeFetch, makeDraft, mount, settle } from './helpers.js';

afterEach(() => (document.body.innerHTML = ''));

const ESTIMATE = { model: 'meta/muse-wonder-1.0', prompt_chars: 30000, input_tokens_est: 10000, max_calls: 12, first_call_usd: 0.1, run_ceiling_usd: 1.2 };

function state(extra: Partial<BoardsState> = {}): BoardsState {
  return {
    boards: [
      { name: 'explorer', toml: 'title = "Explorer"\n', why: 'The Dataset at a glance.' },
      { name: 'buyer', toml: 'title = "Buyer"\n', why: 'Buyers key most rows.' },
    ],
    problems: { explorer: [], buyer: [] },
    set_problems: [],
    preview: {
      explorer: {
        target_s: 1.5, key: null, seconds: 0.42, verdict: 'ok',
        queries: {
          totals: { ms: 12, rows: 1, truncated: false, columns: ['n', 'value'], first_rows: [['7', '1500']] },
          by_year: { ms: 30, error: 'SQLSTATE 22012: division by zero' },
        },
        visuals: [
          { id: 'total', macro: 'Metric', query: 'totals', verdict: 'drawn' },
          { id: 'trend', macro: 'LineChart', query: 'by_year', verdict: 'failed' },
          { id: 'top', macro: 'DataTable', query: 'top', verdict: 'no_rows' },
        ],
      },
      buyer: { target_s: 1.5, key: null, skipped: 'no sample key: no reported column is named in key_columns' },
    },
    usage: null,
    ...extra,
  };
}

function setup(routes: Parameters<typeof fakeFetch>[0] = {}, props: Partial<BsBoards> = {}) {
  const f = fakeFetch(routes);
  const el = mount<BsBoards>('bs-boards', { api: new Api(f.fn), validateDelay: 0, model: 'meta/muse-wonder-1.0', ...props });
  return { el, calls: f.calls };
}

const tabs = (el: Element) => [...el.querySelectorAll('[role=tab]')].map((t) => t.textContent!.trim());

describe('bs-boards (U7)', () => {
  it('asks for the estimate when shown, then drafts with the model', async () => {
    const seen: string[] = [];
    const { el } = setup();
    el.addEventListener('draft-boards', (e) => seen.push((e as CustomEvent).detail.model));
    el.estimate = ESTIMATE;
    await settle(el);
    expect(el.querySelector('.estimate')!.textContent).toContain('$0.10 for the first call');
    // Workspaces SPEC N4: the button carries the run ceiling, and one click starts the run.
    expect(el.querySelector('button.draft-boards')!.textContent).toContain('Draft Boards (at most $1.20)');
    (el.querySelector('button.draft-boards') as HTMLButtonElement).click();
    expect(seen).toEqual(['meta/muse-wonder-1.0']);
  });

  it('asks on a card before drafting over existing Boards (N7)', async () => {
    const seen: string[] = [];
    const { el } = setup({}, { boards: state() });
    el.addEventListener('draft-boards', (e) => seen.push((e as CustomEvent).detail.model));
    el.estimate = ESTIMATE;
    await settle(el);
    (el.querySelector('button.draft-boards') as HTMLButtonElement).click();
    await settle(el);
    expect(seen).toEqual([]);
    expect(el.querySelector('.confirm-card')!.textContent).toContain('replaces the Boards');
    (el.querySelector('.card-cancel') as HTMLButtonElement).click();
    await settle(el);
    expect(el.querySelector('.confirm-card')).toBeNull();
    expect(seen).toEqual([]);
    (el.querySelector('button.draft-boards') as HTMLButtonElement).click();
    await settle(el);
    (el.querySelector('.card-confirm') as HTMLButtonElement).click();
    expect(seen).toEqual(['meta/muse-wonder-1.0']);
  });

  it('fires estimate on connect', async () => {
    const seen: string[] = [];
    const el = document.createElement('bs-boards') as BsBoards;
    el.model = 'meta/muse-spark-1.3';
    el.addEventListener('estimate', (e) => seen.push((e as CustomEvent).detail.model));
    document.body.appendChild(el);
    expect(seen).toEqual(['meta/muse-spark-1.3']);
  });

  it('shows progress while drafting', async () => {
    const { el } = setup({}, {
      running: true,
      events: [
        { type: 'sql', purpose: 'distinct buyers', sql: 'SELECT 1' },
        { type: 'call', usage: { prompt_tokens: 100, cached_tokens: 0, completion_tokens: 10, cost_usd: 0.02 } },
        { type: 'invalid_boards', problems: ['a', 'b'] },
      ],
    });
    await settle(el);
    expect((el.querySelector('button.draft-boards') as HTMLButtonElement).disabled).toBe(true);
    const card = el.querySelector('.run-card')!;
    expect(card.querySelector('.model-chip.model-wonder.live')).not.toBeNull();
    expect(card.querySelector('li.sql')!.textContent).toContain('distinct buyers');
    expect(card.querySelector('li.sql .model-chip.model-sql')).not.toBeNull();
    expect(card.querySelector('li.rejected')!.textContent).toContain('Rejected (2 problems)');
    expect(card.querySelector('.run-calls')!.textContent!.replace(/\s+/g, ' ').trim()).toBe('1 call');
  });

  it('has a tab per Board with its why line and preview', async () => {
    const { el } = setup({}, { boards: state() });
    await settle(el);
    expect(tabs(el)).toEqual(['explorer.toml', 'buyer.toml']);
    expect(el.querySelector('.why')!.textContent).toBe('The Dataset at a glance.');
    expect((el.querySelector('textarea.board-toml') as HTMLTextAreaElement).value).toBe('title = "Explorer"\n');
    expect(el.querySelector('.cold')!.textContent).toContain('0.42 s (target under 1.5 s)');
    const verdicts = [...el.querySelectorAll('table.visuals tbody tr')].map((r) => r.lastElementChild!.textContent);
    expect(verdicts).toEqual(['drawn', 'failed', 'No rows']);
    expect(el.querySelector('table.rows th')!.textContent).toBe('n');
    expect(el.querySelector('table.rows td')!.textContent).toBe('7');
    expect(el.querySelector('.query-error')!.textContent).toContain('22012');
    (el.querySelectorAll('[role=tab]')[1] as HTMLButtonElement).click();
    await settle(el);
    expect(el.querySelector('[role=tab][aria-selected=true]')!.textContent).toContain('buyer.toml');
    expect(el.querySelector('.preview-skipped')!.textContent).toContain('no sample key');
  });

  it('shows problems instead of the preview, and set problems above the tabs', async () => {
    const s = state({ problems: { explorer: ['boards/explorer.toml: layout: amont'], buyer: [] }, set_problems: ['boards: at most 1 Entity Boards'] });
    const { el } = setup({}, { boards: s });
    await settle(el);
    expect(tabs(el)[0]).toBe('explorer.toml (problems)');
    expect(el.querySelector('.board-problems')!.textContent).toContain('amont');
    expect(el.querySelector('.preview')).toBeNull();
    expect(el.querySelector('.set-problems')!.textContent).toContain('at most 1');
  });

  it('serializes Board saves and ignores validation for older edits', async () => {
    const replies: ((s: BoardsState) => void)[] = [];
    const sent: string[] = [];
    const f = fakeFetch({});
    const api = new Api(f.fn);
    api.validateBoards = (boards) => {
      sent.push(boards[0].toml);
      return new Promise((ok) => replies.push(ok));
    };
    const changed: BoardsState[] = [];
    const el = mount<BsBoards>('bs-boards', { api, validateDelay: 0, boards: state() });
    el.addEventListener('boards-changed', (e) => changed.push((e as CustomEvent).detail));
    await settle(el);
    const area = el.querySelector('textarea.board-toml') as HTMLTextAreaElement;
    area.value = 'first edit';
    area.dispatchEvent(new Event('input'));
    await settle(el);
    area.value = 'second edit';
    area.dispatchEvent(new Event('input'));
    await settle(el);
    expect(sent).toEqual(['first edit']);
    replies[0](state({ problems: { explorer: ['boards/explorer.toml: older'], buyer: [] } }));
    await settle(el);
    expect(sent).toEqual(['first edit', 'second edit']);
    expect(changed).toEqual([]);
    const newer = state({ problems: { explorer: ['boards/explorer.toml: newer'], buyer: [] } });
    replies[1](newer);
    await settle(el);
    expect(el.querySelector('.board-problems')!.textContent).toContain('newer');
    expect(changed).toEqual([newer]);
    expect((el.querySelector('textarea.board-toml') as HTMLTextAreaElement).value).toBe('second edit');
  });
});

describe('bs-boards re-draft (refuter round 1)', () => {
  it('shows the new text when a re-draft returns Boards with the same names', async () => {
    const { el } = setup({}, { boards: state() });
    await settle(el);
    const redrafted = state();
    redrafted.boards = redrafted.boards.map((b) => ({ ...b, toml: `${b.toml}# redrafted\n`, why: 'New why.' }));
    el.boards = redrafted;
    await settle(el);
    expect((el.querySelector('textarea.board-toml') as HTMLTextAreaElement).value).toBe('title = "Explorer"\n# redrafted\n');
    expect(el.querySelector('.why')!.textContent).toBe('New why.');
  });
});

describe('boardProblems (U7)', () => {
  it('lists set problems first, then each Board’s', () => {
    expect(boardProblems(null)).toEqual([]);
    expect(boardProblems(state())).toEqual([]);
    expect(boardProblems(state({ set_problems: ['s'], problems: { explorer: ['e'], buyer: ['b'] } }))).toEqual(['s', 'e', 'b']);
  });
});
