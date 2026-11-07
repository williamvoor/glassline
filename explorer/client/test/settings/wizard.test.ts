// The New Workspace wizard (specs/workspaces/SPEC.md phase 3, ADR 0044): N2–N10, N13, N14.
import { afterEach, describe, expect, it } from 'vitest';

import { Api } from '../../src/settings/api.js';
import '../../src/settings/wizard.js';
import type { ExplorerWizard } from '../../src/settings/wizard.js';
import type { BoardsState, Session } from '../../src/settings/types.js';
import { REPORT, claim, fakeFetch, makeDraft, settle } from './helpers.js';

afterEach(() => (document.body.innerHTML = ''));

const ESTIMATE = { model: 'meta/muse-wonder-1.0', prompt_chars: 3, input_tokens_est: 1000, first_call_usd: 0.01, run_ceiling_usd: 0.12, max_calls: 12 };
const VALID = { errors: [], context_chars: 10 };
const EMPTY: Session = { connection: null, report: null, regenerating: null, draft: false, boards: false, run: null };
const CALL = { type: 'call', usage: { prompt_tokens: 1000, cached_tokens: 600, completion_tokens: 50, cost_usd: 0.02 } };

function boardsState(extra: Partial<BoardsState> = {}): BoardsState {
  return {
    boards: [{ name: 'explorer', toml: 'title = "Explorer"\n', why: 'At a glance.' }],
    problems: { explorer: [] }, set_problems: [], preview: {}, usage: null, model: 'meta/muse-spark-1.3', ...extra,
  };
}

type Routes = Parameters<typeof fakeFetch>[0];

function choice(name: string, label: string, over: Record<string, unknown> = {}) {
  return { name, label, url_env: `${name.toUpperCase()}_DATABASE_URL`, listed: true, ready: true, ...over };
}

function routes(over: Routes = {}) {
  const draft = makeDraft([claim('verified')]);
  let polls = 0;
  return fakeFetch({
    'GET /api/models': () => [200, { models: ['meta/muse-wonder-1.0', 'meta/muse-spark-1.3'], default: 'meta/muse-wonder-1.0' }],
    'GET /api/session': () => [200, EMPTY],
    'GET /api/connections': () => [200, { connections: [choice('vic', 'Victoria'), choice('other', 'Other')], current: null }],
    'POST /api/connect': (b) => [200, { host: 'h', name: 'vic', connection: b.connection, connection_label: b.connection,
      schemas: [{ name: 's', objects: 2 }] }],
    'POST /api/report': () => [200, REPORT],
    'POST /api/estimate': () => [200, ESTIMATE],
    'POST /api/draft': () => [200, { run_id: 'd1' }],
    'GET /api/draft/d1/events': () =>
      ++polls === 1
        ? [200, { events: [{ type: 'sql', purpose: 'count rows', sql: 'SELECT 1' }, CALL], next: 2, done: false }]
        : [200, { events: [], next: 2, done: true, draft, validation: VALID }],
    'POST /api/validate': () => [200, VALID],
    'POST /api/boards/estimate': () => [200, ESTIMATE],
    'POST /api/boards/draft': () => [200, { run_id: 'b1' }],
    'GET /api/draft/b1/events': () => [200, { events: [CALL], next: 1, done: true, boards: boardsState() }],
    'POST /api/export': () => [200, { path: 'workspaces/vic/dataset', workspace: 'vic', overwrote: [], backup: null, reloaded: true }],
    'POST /api/discard': () => [200, { discarded: true }],
    ...over,
  });
}

async function mountWizard(f = routes()) {
  const el = document.createElement('explorer-wizard') as ExplorerWizard;
  el.api = new Api(f.fn);
  el.pollMs = 0;
  document.body.appendChild(el);
  await el.resume();
  await settle(el);
  return { el, f };
}

const paths = (f: ReturnType<typeof fakeFetch>) => f.calls.map((c) => c.path);
const current = (el: Element) => el.querySelector('.wizard-steps li.current .step-name')?.textContent?.trim();
const click = async (el: Element, sel: string) => {
  const b = el.querySelector(sel) as HTMLButtonElement | null;
  expect(b, sel).not.toBeNull();
  expect(b!.disabled, `${sel} disabled`).toBe(false);
  b!.click();
  await settle(el, 15);
};

/** Database → report → the Draft step. */
async function toDraftStep(el: ExplorerWizard) {
  await click(el, '.connect-var');
  (el.querySelector('bs-connect input[name=schema]') as HTMLInputElement).click();
  await settle(el);
  await click(el, 'bs-connect button.run-report');
}

/** … → a finished Draft on the Review step. */
async function toReview(el: ExplorerWizard) {
  await toDraftStep(el);
  await click(el, 'bs-report button.draft');
  await settle(el, 20);
}

describe('N2, N13: the step bar', () => {
  it('has five numbered steps, each tagged SQL or with its model; only visited steps are links', async () => {
    const { el } = await mountWizard();
    const items = [...el.querySelectorAll('.wizard-steps li')];
    expect(items.map((li) => li.querySelector('.step-name')!.textContent)).toEqual(['Database', 'Draft', 'Review', 'Boards', 'Export']);
    expect(items.map((li) => li.querySelector('.model-chip')!.textContent!.trim()))
      .toEqual(['SQL', 'Muse Wonder', 'SQL', 'Muse Wonder', 'SQL']);
    expect(current(el)).toBe('Database');
    expect(el.querySelectorAll('.wizard-steps button.step-link')).toHaveLength(0); // nothing visited yet

    await toReview(el);
    expect(current(el)).toBe('Review');
    const links = [...el.querySelectorAll('.wizard-steps button.step-link')].map((b) => b.getAttribute('data-step'));
    expect(links).toEqual(['database', 'draft']); // Boards and Export not reached yet
    await click(el, '.wizard-steps button[data-step=draft]');
    expect(current(el)).toBe('Draft');
  });
});

describe('N3, N4: Database, then a Draft that only a Draft button starts', () => {
  it('runs the report on Next with no model call, then drafts with one click and lands on Review', async () => {
    const { el, f } = await mountWizard();
    await toDraftStep(el);
    expect(current(el)).toBe('Draft');
    expect(paths(f)).toContain('POST /api/report');
    expect(paths(f)).not.toContain('POST /api/draft'); // Next never calls a model
    expect((el.querySelector('.wizard-footer button.next') as HTMLButtonElement).disabled).toBe(true); // no Draft yet
    expect(el.querySelector('bs-report button.draft')!.textContent!.trim()).toBe('Draft (at most $0.12)');
    await click(el, 'bs-report button.draft');
    await settle(el, 20);
    expect(f.calls.find((c) => c.path === 'POST /api/draft')!.body).toEqual({ model: 'meta/muse-wonder-1.0', effort: 'low' });
    expect(current(el)).toBe('Review'); // a finished Draft moves on by itself
  });

  it('with no Connection and no variable, says how to add one', async () => {
    const none = await mountWizard(routes({ 'GET /api/connections': () => [200, { connections: [], current: null }] }));
    const noneText = none.el.querySelector('section.database p.muted')!.textContent!.replace(/\s+/g, ' ');
    expect(noneText).toContain('No Connection and no <NAME>_DATABASE_URL variable is set');
  });

  it('C5: shows Connections by label, marks bare variables as new, and disables an unset one', async () => {
    const { el, f } = await mountWizard(routes({ 'GET /api/connections': () => [200, { connections: [
      choice('vic', 'Victoria'), choice('gone', 'Gone', { ready: false }), choice('aus', 'aus', { listed: false }),
    ], current: null }] }));
    const buttons = [...el.querySelectorAll<HTMLButtonElement>('.connect-var')];
    expect(buttons.map((b) => b.textContent!.replace(/\s+/g, ' ').trim())).toEqual([
      'Victoria', 'Gone (not set)', 'aus (new)']); // never the .env variable's name
    expect(buttons[1]!.disabled).toBe(true);
    expect(buttons[1]!.title).toBe('GONE_DATABASE_URL is not set in .env');
    buttons[2]!.click();
    await settle(el);
    expect(f.calls.find((c) => c.path === 'POST /api/connect')!.body).toEqual({ connection: 'aus' });
  });
});

describe('N14: the run card', () => {
  it('counts calls and spend against the ceiling while running, then a summary stays on the step', async () => {
    let release = () => {};
    const gate = new Promise<void>((ok) => (release = ok));
    let polls = 0;
    const draft = makeDraft([claim('verified')]);
    const f = routes({
      'GET /api/draft/d1/events': () =>
        ++polls === 1
          ? [200, { events: [{ type: 'sql', purpose: 'count rows', sql: 'SELECT 1' }, CALL, CALL], next: 3, done: false }]
          : [200, { events: [], next: 3, done: true, draft, validation: VALID }],
    });
    const slow = new Api(async (input, init) => {
      if (input.startsWith('/api/draft/d1/events') && polls >= 1) await gate;
      return f.fn(input, init);
    });
    const { el } = await mountWizard(f);
    el.api = slow;
    await toDraftStep(el);
    (el.querySelector('bs-report button.draft') as HTMLButtonElement).click();
    await settle(el, 10);
    const card = el.querySelector('.run-card')!;
    expect(card.querySelector('.model-chip.model-wonder.live')).not.toBeNull();
    expect(card.querySelector('.run-calls')!.textContent!.replace(/\s+/g, ' ').trim()).toBe('2 calls');
    expect(card.querySelector('.meter-text')!.textContent!.replace(/\s+/g, ' ').trim()).toBe('$0.040 of at most $0.12');
    expect((el.querySelector('.discard') as HTMLButtonElement).disabled).toBe(true); // no discarding mid-run
    release();
    await settle(el, 20);
    expect(current(el)).toBe('Review');
    await click(el, '.wizard-steps button[data-step=draft]');
    expect(el.querySelector('.run-summary .model-chip.model-wonder')).not.toBeNull();
    expect(el.querySelector('.run-summary > span:not(.model-chip)')!.textContent).toMatch(/^1 call · /);
  });
});

describe('N5: Review gates Next with its own blockers', () => {
  it('blocks on a failed claim, and saves edits before leaving', async () => {
    const draft = makeDraft([claim('failed')]);
    const { el, f } = await mountWizard(routes({
      'GET /api/draft/d1/events': () => [200, { events: [], next: 0, done: true, draft, validation: VALID }],
    }));
    await toReview(el);
    expect((el.querySelector('.wizard-footer button.next') as HTMLButtonElement).disabled).toBe(true);
    expect(el.querySelector('.wizard-footer .blockers')!.textContent).toContain('Claim 1 is failed');
    await click(el, 'bs-review button.dismiss');
    const before = paths(f).filter((p) => p === 'POST /api/validate').length;
    await click(el, '.wizard-footer button.next');
    expect(paths(f).filter((p) => p === 'POST /api/validate').length).toBe(before + 1);
    expect(current(el)).toBe('Boards');
  });

  it('stays on Review when saving the edits fails', async () => {
    const { el } = await mountWizard(routes({ 'POST /api/validate': () => [500, { error: 'unexpected OSError' }] }));
    await toReview(el);
    await click(el, '.wizard-footer button.next');
    expect(current(el)).toBe('Review');
    expect(el.querySelector('bs-review .error')!.textContent).toContain('unexpected OSError');
  });
});

describe('N6: Boards — draft, skip, or keep in a Regenerate', () => {
  it('skips to Export with no Boards; Export sends the review payload and shows the ready card (N9)', async () => {
    const { el, f } = await mountWizard();
    await toReview(el);
    await click(el, '.wizard-footer button.next');
    expect(el.querySelector('.wizard-footer button.next')).toBeNull();
    await click(el, '.wizard-footer button.skip-boards');
    expect(current(el)).toBe('Export');
    expect((el.querySelector('input.name') as HTMLInputElement).value).toBe('demo'); // from dataset.toml
    const name = el.querySelector('input.name') as HTMLInputElement;
    name.value = 'Bad-Name';
    name.dispatchEvent(new Event('input'));
    await settle(el);
    expect((el.querySelector('button.export') as HTMLButtonElement).disabled).toBe(true);
    name.value = 'vic';
    name.dispatchEvent(new Event('input'));
    await settle(el);
    let changed = 0;
    el.addEventListener('workspaces-changed', () => changed++);
    await click(el, 'button.export');
    const sent = f.calls.find((c) => c.path === 'POST /api/export')!.body;
    expect(sent).toMatchObject({ name: 'vic', target: 'toml', force: false, dismissed: [] });
    expect(sent.files.dataset_toml).toBe('name = "demo"\n');
    expect(el.querySelector('.done-card h2')!.textContent).toContain('vic is ready');
    expect(changed).toBe(1);
    let opened = '';
    el.addEventListener('open-workspace', (e) => (opened = (e as CustomEvent).detail));
    await click(el, 'button.open-workspace');
    expect(opened).toBe('vic');
    expect(paths(f)).toContain('POST /api/discard'); // the server forgets the finished flow
    expect(current(el)).toBe('Database');
  });

  it('drafts Boards, stays on the step, and Board problems block Next', async () => {
    const broken = boardsState({ problems: { explorer: ['boards/explorer.toml: bad'] } });
    const { el, f } = await mountWizard(routes({
      'GET /api/draft/b1/events': () => [200, { events: [CALL], next: 1, done: true, boards: broken }],
    }));
    await toReview(el);
    await click(el, '.wizard-footer button.next');
    expect(el.querySelector('bs-boards button.draft-boards')!.textContent).toContain('at most $0.12');
    await click(el, 'bs-boards button.draft-boards');
    await settle(el, 20);
    expect(f.calls.find((c) => c.path === 'POST /api/boards/draft')!.body.model).toBe('meta/muse-wonder-1.0');
    expect(current(el)).toBe('Boards');
    expect((el.querySelector('.wizard-footer button.next') as HTMLButtonElement).disabled).toBe(true);
    expect(el.querySelector('.wizard-footer .blockers')!.textContent).toContain('A Board has problems');
    expect(el.querySelector('.wizard-steps li:nth-child(4) .model-chip')!.textContent!.trim()).toBe('Muse Spark'); // who drafted them
  });

  it('in a Regenerate, offers Keep current Boards, which checks them, and their problems block Next', async () => {
    const draft = makeDraft([claim('verified')]);
    const kept = boardsState({ model: null, problems: { explorer: ['stale column'] } });
    const { el, f } = await mountWizard(routes({
      'POST /api/workspaces/mine/regenerate': () => [200, REPORT],
      'GET /api/draft/d1/events': () => [200, { events: [], next: 0, done: true, draft, validation: VALID }],
      'POST /api/boards/keep': () => [200, kept],
    }));
    await el.regenerate('mine');
    await settle(el);
    await click(el, 'bs-report button.draft');
    await settle(el, 20);
    await click(el, '.wizard-footer button.next');
    expect(el.querySelector('.wizard-footer .skip-boards')).toBeNull();
    await click(el, '.wizard-footer button.keep-boards');
    expect(paths(f)).toContain('POST /api/boards/keep');
    expect((el.querySelector('.wizard-footer button.next') as HTMLButtonElement).disabled).toBe(true);
    expect(el.querySelector('bs-boards .board-problems')!.textContent).toContain('stale column');
  });

  it('in a Regenerate with no Boards to keep, goes on to Export without them', async () => {
    const draft = makeDraft([claim('verified')]);
    const { el } = await mountWizard(routes({
      'POST /api/workspaces/mine/regenerate': () => [200, REPORT],
      'GET /api/draft/d1/events': () => [200, { events: [], next: 0, done: true, draft, validation: VALID }],
      'POST /api/boards/keep': () => [404, { error: 'there are no current Boards to keep' }],
    }));
    await el.regenerate('mine');
    await settle(el);
    await click(el, 'bs-report button.draft');
    await settle(el, 20);
    await click(el, '.wizard-footer button.next');
    await click(el, '.wizard-footer button.keep-boards');
    expect(current(el)).toBe('Export');
    expect(el.querySelector('.name-fixed')!.textContent).toContain('Regenerating mine');
    expect(el.querySelector('input.name')).toBeNull();
  });
});

describe('N7: a change that discards later work asks on a card first', () => {
  it('another login, or the report again, over a Draft: Cancel changes nothing', async () => {
    const { el, f } = await mountWizard();
    await toReview(el);
    await click(el, '.wizard-steps button[data-step=database]');
    const other = [...el.querySelectorAll('.connect-var')].find((b) => b.textContent!.trim() === 'Other') as HTMLButtonElement;
    other.click();
    await settle(el);
    expect(el.querySelector('.confirm-card')!.textContent).toContain('replaces the Draft and its Boards');
    await click(el, '.card-cancel');
    expect(f.calls.filter((c) => c.path === 'POST /api/connect')).toHaveLength(1);
    expect(el.draft).not.toBeNull();

    (el.querySelector('bs-connect input[name=schema]') as HTMLInputElement).click();
    await settle(el);
    await click(el, 'bs-connect button.run-report');
    expect(el.querySelector('.confirm-card')).not.toBeNull();
    expect(f.calls.filter((c) => c.path === 'POST /api/report')).toHaveLength(1);
    await click(el, '.card-confirm');
    expect(f.calls.filter((c) => c.path === 'POST /api/report')).toHaveLength(2);
    expect(el.draft).toBeNull();
    expect(current(el)).toBe('Draft');
  });

  it('Discard Draft asks on a card, then clears the server and closes', async () => {
    const { el, f } = await mountWizard();
    await toReview(el);
    let closed = 0;
    el.addEventListener('wizard-close', () => closed++);
    await click(el, 'button.discard');
    expect(el.querySelector('.confirm-card')!.textContent).toContain('Discard the Draft');
    expect(paths(f)).not.toContain('POST /api/discard');
    await click(el, '.card-confirm');
    expect(paths(f)).toContain('POST /api/discard');
    expect(closed).toBe(1);
    expect(el.draft).toBeNull();
  });
});

describe('N10: resuming from the server', () => {
  it('rebuilds at the furthest step the server holds after a page reload', async () => {
    const draft = makeDraft([claim('verified')]);
    const { el } = await mountWizard(routes({
      'GET /api/session': () => [200, { ...EMPTY, connection: 'vic', report: REPORT, draft: true, boards: true }],
      'GET /api/connection': () => [200, { host: 'h', name: 'vic', schemas: [] }],
      'GET /api/draft': () => [200, { draft, validation: VALID }],
      'GET /api/boards': () => [200, boardsState()],
    }));
    expect(current(el)).toBe('Boards');
    expect(el.connection!.connection).toBe('vic');
    expect(el.querySelectorAll('bs-boards [role=tab]')).toHaveLength(1);
    expect((el.querySelector('.wizard-footer button.next') as HTMLButtonElement).disabled).toBe(false);
  });

  it('after a server restart, starts again at step 1 with a note', async () => {
    let session: Session = EMPTY;
    const { el } = await mountWizard(routes({ 'GET /api/session': () => [200, session] }));
    await toReview(el);
    session = { ...EMPTY }; // the server forgot everything
    await el.resume();
    await settle(el);
    expect(current(el)).toBe('Database');
    expect(el.querySelector('.notice-wizard')!.textContent).toContain('The server restarted, so the Draft is gone.');
  });
});

/** … → Export with Boards skipped, name "vic", exported. */
async function exported(el: ExplorerWizard) {
  await toReview(el);
  await click(el, '.wizard-footer button.next');
  await click(el, '.wizard-footer button.skip-boards');
  const name = el.querySelector('input.name') as HTMLInputElement;
  name.value = 'vic';
  name.dispatchEvent(new Event('input'));
  await settle(el);
  await click(el, 'button.export');
}

describe('N9: after Export (refuter)', () => {
  it('a failed reload says so, offers Reload the agent, and Open waits for it', async () => {
    const { el, f } = await mountWizard(routes({
      'POST /api/export': () => [200, { path: 'workspaces/vic/dataset', workspace: 'vic', overwrote: [], backup: null, reloaded: false }],
      'POST /api/reload': () => [200, { reloaded: true, status: 200 }],
    }));
    await exported(el);
    expect(el.querySelector('.done-card .warn-text')!.textContent).toContain("didn't pick it up");
    expect((el.querySelector('button.open-workspace') as HTMLButtonElement).disabled).toBe(true);
    let changed = 0;
    el.addEventListener('workspaces-changed', () => changed++);
    await click(el, '.done-card button.reload-agent');
    expect(paths(f)).toContain('POST /api/reload');
    expect(changed).toBe(1);
    expect(el.querySelector('.done-card .warn-text')).toBeNull();
    expect((el.querySelector('button.open-workspace') as HTMLButtonElement).disabled).toBe(false);
  });

  it('a reload that fails again shows why', async () => {
    const { el } = await mountWizard(routes({
      'POST /api/export': () => [200, { path: 'p', workspace: 'vic', overwrote: [], backup: null, reloaded: false }],
      'POST /api/reload': () => [200, { reloaded: false, status: 0 }],
    }));
    await exported(el);
    await click(el, '.done-card button.reload-agent');
    expect(el.querySelector('.error')!.textContent).toContain("didn't reload: it is not running");
    expect((el.querySelector('button.open-workspace') as HTMLButtonElement).disabled).toBe(true);
  });

  it('Back to Workspaces clears the server and closes, ready for a fresh start', async () => {
    const { el, f } = await mountWizard();
    await exported(el);
    let closed = 0;
    el.addEventListener('wizard-close', () => closed++);
    await click(el, '.done-card button.back-to-list');
    expect(paths(f)).toContain('POST /api/discard');
    expect(closed).toBe(1);
    expect(el.exported).toBeNull();
    expect(el.inProgress).toBe(false);
    expect(current(el)).toBe('Database');
  });
});

describe('error paths (refuter)', () => {
  it('a refused login stays on the Database step with the reason', async () => {
    const { el } = await mountWizard(routes({
      'POST /api/connect': () => [403, { error: 'this login can write or administer the database; use a read-only role' }],
    }));
    await click(el, '.connect-var');
    expect(el.querySelector('.error')!.textContent).toContain('read-only role');
    expect(current(el)).toBe('Database');
    expect(el.querySelector('bs-connect')).toBeNull();
  });

  it('shows the server refusal reasons on Export and stays there', async () => {
    const { el } = await mountWizard(routes({ 'POST /api/export': () => [422, { reasons: ['a Board has problems'] }] }));
    await exported(el);
    expect(el.querySelector('.refused')!.textContent).toContain('a Board has problems');
    expect(el.exported).toBeNull();
    expect(current(el)).toBe('Export');
  });

  it('Cancel with only a report goes straight back, with no card (spec: a card only with a Draft)', async () => {
    const { el, f } = await mountWizard();
    await toDraftStep(el);
    expect(el.querySelector('button.discard')!.textContent!.trim()).toBe('Cancel');
    let closed = 0;
    el.addEventListener('wizard-close', () => closed++);
    await click(el, 'button.discard');
    expect(el.querySelector('.confirm-card')).toBeNull();
    expect(closed).toBe(1);
    expect(paths(f)).toContain('POST /api/discard');
  });

  it('a page reload during a Boards run resumes on the Boards step and follows the run', async () => {
    const draft = makeDraft([claim('verified')]);
    const { el } = await mountWizard(routes({
      'GET /api/session': () => [200, { ...EMPTY, connection: 'vic', report: REPORT, draft: true, run: { id: 'b1', kind: 'boards' } }],
      'GET /api/connection': () => [200, { host: 'h', name: 'vic', schemas: [] }],
      'GET /api/draft': () => [200, { draft, validation: VALID }],
    }));
    await settle(el, 15);
    expect(current(el)).toBe('Boards');
    expect(el.boards).not.toBeNull(); // the followed run finished with its Boards
  });
});

describe('N19: Review edits recheck the Boards (E15)', () => {
  async function withBoardsThenBackToReview() {
    const broken = boardsState({ problems: { explorer: ['boards/explorer.toml: no column "region" after the edit'] } });
    const { el, f } = await mountWizard(routes({ 'POST /api/boards/validate': () => [200, broken] }));
    await toReview(el);
    await click(el, '.wizard-footer button.next');
    await click(el, 'bs-boards button.draft-boards');
    await settle(el, 20);
    expect((el.querySelector('.wizard-footer button.next') as HTMLButtonElement).disabled).toBe(false); // clean so far
    await click(el, '.wizard-steps button[data-step=review]');
    const file = el.querySelector('bs-review textarea.file') as HTMLTextAreaElement;
    file.value = 'name = "vic"\n# region dropped\n';
    file.dispatchEvent(new Event('input'));
    return { el, f };
  }

  it('by Next: saves the edits, then rechecks the Boards, whose new problems block Next', async () => {
    const { el, f } = await withBoardsThenBackToReview();
    await click(el, '.wizard-footer button.next');
    const order = paths(f).filter((p) => p === 'POST /api/validate' || p === 'POST /api/boards/validate');
    expect(order.slice(-2)).toEqual(['POST /api/validate', 'POST /api/boards/validate']); // saved first, then rechecked
    expect(f.calls.filter((c) => c.path === 'POST /api/validate').at(-1)!.body.files.dataset_toml).toContain('region dropped');
    expect(f.calls.find((c) => c.path === 'POST /api/boards/validate')!.body.boards[0].name).toBe('explorer');
    expect(current(el)).toBe('Boards');
    expect((el.querySelector('.wizard-footer button.next') as HTMLButtonElement).disabled).toBe(true);
    expect(el.querySelector('bs-boards .board-problems')!.textContent).toContain('after the edit');
  });

  it('by the step bar too', async () => {
    const { el, f } = await withBoardsThenBackToReview();
    await click(el, '.wizard-steps button[data-step=boards]');
    expect(paths(f)).toContain('POST /api/boards/validate');
    expect(current(el)).toBe('Boards');
    expect((el.querySelector('.wizard-footer button.next') as HTMLButtonElement).disabled).toBe(true);
  });

  it('no Boards held: nothing to recheck', async () => {
    const { el, f } = await mountWizard();
    await toReview(el);
    await click(el, '.wizard-footer button.next');
    expect(paths(f)).not.toContain('POST /api/boards/validate');
  });
});
