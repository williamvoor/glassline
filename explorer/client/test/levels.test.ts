// Actions, tabs and the investigation log (explorer SPEC §18 slice 1b, B12). §15's A14 and A15 and
// §16's L2–L4 and L6 carry over: each Level is a tab now, and the Breadcrumb is gone.
import {afterEach, describe, expect, it} from 'vitest';
import type {AgentConnection, AskEvent, UiAction} from '../src/a2a.js';
import '../src/app.js';
import {ACTION_FAILED_KEY, LEVEL_KEY, LOG_KEY, MODEL_KEY, NO_BOARDS_KEY, OPEN_BOARD, TAB_KEY, type ExplorerApp} from '../src/app.js';
import {MAX_TABS} from '../src/tabs.js';
import {EXPLORER_CATALOG_ID} from '../src/catalog.js';

const CTX = 'ctx-1';

/** A surface with a clickable DataGrid (rows "A", "B") and a Follow-up button. */
function surface(id: string, opts: {drillable?: boolean; followUp?: boolean; boards?: number[]} = {}): Record<string, unknown>[] {
  const children = ['grid', ...(opts.followUp ? ['more'] : [])];
  return [
    {version: 'v0.9', createSurface: {surfaceId: id, catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: id, components: [
      {id: 'root', component: 'Column', children},
      {id: 'grid', component: 'DataGrid', columns: [{key: 'name', label: 'Name'}], rows: [['A'], ['B']],
       action: {source: `v-${id}`, rows: [0, 1], drillable: opts.drillable ?? true, ...(opts.boards ? {boards: opts.boards} : {})}},
      ...(opts.followUp ? [
        {id: 'more', component: 'Button', child: 'more_t', action: {event: {name: 'ask', context: {follow_up: 'f1'}}}},
        {id: 'more_t', component: 'Text', text: 'By year'},
      ] : []),
    ]}},
  ];
}

/** A reply that adds a Level: its surface `sid` and tab `tab` (the server's t<n>). */
const final = (sid: string, tab: string, title: string, prose = '', opts = {}, model?: string | null): AskEvent => ({
  kind: 'final', contextId: CTX, prose, a2ui: surface(sid, opts),
  metadata: {
    [LEVEL_KEY]: {turn: 1, level: 1, label: title, surfaceId: sid}, ...(model !== undefined ? {[MODEL_KEY]: model} : {}),
    [TAB_KEY]: {id: tab, kind: 'question', title}, [LOG_KEY]: {kind: 'question', tag: `NEW TAB · ${tab}`, text: `log ${title}`},
  },
});

const failed = (message: string): AskEvent => ({kind: 'final', contextId: CTX, prose: message, a2ui: [], metadata: {[ACTION_FAILED_KEY]: true}});

interface Agent {
  questions: {text: string; viewing?: string}[];
  actions: {action: UiAction; viewing?: string}[];
  connector: () => Promise<AgentConnection>;
  gate?: Promise<void>;
}

function fakeAgent(
  onQuestion: (n: number) => AskEvent[] | Error,
  onAction: (a: UiAction, n: number) => AskEvent[],
  opts: {actionStatus?: boolean} = {},
): Agent {
  const agent: Agent = {questions: [], actions: [], connector: async () => connection};
  const connection: AgentConnection = {
    info: {description: 'Demo', examples: []},
    async *askQuestion(text, _contextId, viewing) {
      agent.questions.push({text, viewing});
      const events = onQuestion(agent.questions.length);
      if (events instanceof Error) throw events;
      yield* events;
    },
    async *act(action, _contextId, viewing) {
      if (action.name === OPEN_BOARD) {
        // These tests cover tabs from questions and Actions; the Dataset has no Boards (app.test.ts has them).
        yield {kind: 'final', contextId: CTX, prose: 'No Boards', a2ui: [], metadata: {[ACTION_FAILED_KEY]: true, [NO_BOARDS_KEY]: true}};
        return;
      }
      agent.actions.push({action, viewing});
      if (agent.gate) await agent.gate;
      if (opts.actionStatus ?? true) yield {kind: 'status', text: 'Running the drill-down'};
      yield* onAction(action, agent.actions.length);
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

async function mount(agent: Agent): Promise<ExplorerApp> {
  const app = document.createElement('explorer-app') as ExplorerApp;
  app.connector = agent.connector;
  document.body.appendChild(app);
  await settle(app);
  return app;
}

const q = (app: ExplorerApp, sel: string) => app.querySelector(sel) as HTMLElement | null;
// The Canvas shows the selected tab, so these read that one.
const rows = (app: ExplorerApp) => [...app.querySelector('.canvas .tab-view')!.querySelectorAll('tr.clickable')] as HTMLElement[];
const tabs = (app: ExplorerApp) => [...app.querySelectorAll('.tab-strip .tab')] as HTMLElement[];
const titles = (app: ExplorerApp) => tabs(app).map(t => t.querySelector('.tab-title')!.textContent?.trim());
const selectedTitle = (app: ExplorerApp) => app.querySelector('.tab-strip .tab.selected .tab-title')?.textContent?.trim();
const tabButton = (app: ExplorerApp, title: string) =>
  tabs(app).find(t => t.querySelector('.tab-title')!.textContent?.trim() === title)!.querySelector('.tab-select') as HTMLButtonElement;
const entries = (app: ExplorerApp) => [...app.querySelectorAll('.sidebar .log-entry')] as HTMLButtonElement[];
const entryTexts = (app: ExplorerApp) => entries(app).map(e => e.querySelector('.log-text')!.textContent?.trim());

async function click(app: ExplorerApp, el: HTMLElement) {
  el.click();
  await settle(app);
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('Actions, tabs and the investigation log', () => {
  it('sends a Drill-down at once and opens its tab after the one clicked, with no prose block', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q', 'Intro')], () => [final('s2', 't2', 'A')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    expect(agent.actions).toHaveLength(1);
    const {action, viewing} = agent.actions[0]!;
    expect(action).toMatchObject({name: 'drill', surfaceId: 's1', sourceComponentId: 'grid', context: {source: 'v-s1', row: 0}});
    expect(Object.keys(action).sort()).toEqual(['context', 'name', 'sourceComponentId', 'surfaceId', 'timestamp']);
    expect(viewing).toBe('t1'); // the tab it was clicked on
    expect(titles(app)).toEqual(['q', 'A']);
    expect(selectedTitle(app)).toBe('A');
    expect(app.querySelectorAll('a2ui-surface')).toHaveLength(1); // one tab on the Canvas
    expect(q(app, '.tab-view .prose')).toBeNull(); // empty prose part: no prose block
    expect(entryTexts(app)).toEqual(['log q', 'log A']); // the server's explorerLogEntry text
  });

  it('opens a confirmation for an Ask on the clicked tab; Cancel and Escape send nothing, Ask opens a tab', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q', '', {drillable: false})], () => [final('s2', 't2', 'B', 'More')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[1]!);
    expect(q(app, '.confirm')!.textContent).toContain('Ask the agent: B');
    await click(app, q(app, '.confirm .cancel')!);
    expect(q(app, '.confirm')).toBeNull();

    rows(app)[1]!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    await settle(app);
    q(app, '.confirm')!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    await settle(app);
    expect(q(app, '.confirm')).toBeNull();
    expect(agent.actions).toHaveLength(0);

    await click(app, rows(app)[1]!);
    await click(app, q(app, '.confirm .ok')!);
    expect(agent.actions.map(a => a.action.name)).toEqual(['ask']);
    expect(agent.actions[0]!.action.context).toEqual({source: 'v-s1', row: 1});
    expect(titles(app)).toEqual(['q', 'B']);
    expect(q(app, '.tab-view .question')!.textContent).toContain('B'); // an Ask tab is headed by its title
    expect(q(app, '.tab-view .prose')!.textContent).toContain('More');
  });

  // SPEC §20: a row whose click opens an Entity Board asks which view first.
  it('shows the Board choice on a boards row and sends nothing until a button is pressed (E1, E3)', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q', '', {drillable: false, boards: [1]})], () => [final('s2', 't2', 'B')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[1]!);
    const dialog = q(app, '.confirm.board-choice')!;
    expect(dialog.textContent).toContain('B: open its Board (all its data, not only this view\'s filters)');
    expect(dialog.textContent).toContain('ask the agent within this view\'s filters (takes a few seconds)');
    expect([...dialog.querySelectorAll('button')].map(b => b.textContent?.trim())).toEqual(['Open Board', 'Ask within filters', 'Cancel']);
    expect(agent.actions).toHaveLength(0);
    // E3: Open Board has focus, so a browser's Enter activates it; it is a native button.
    const ok = q(app, '.confirm .ok')!;
    expect(document.activeElement).toBe(ok);
    expect(ok.tagName).toBe('BUTTON');
    expect(ok.textContent?.trim()).toBe('Open Board');
  });

  it('Open Board sends drill, Ask within filters sends ask with the same context, Cancel and Escape send nothing (E2)', async () => {
    const agent = fakeAgent(
      () => [final('s1', 't1', 'q', '', {boards: [0, 1]})],
      (a, n) => [final(`s${n + 1}`, `t${n + 1}`, a.name === 'drill' ? 'Board' : 'Asked')],
    );
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    await click(app, q(app, '.confirm .cancel')!);
    expect(q(app, '.confirm')).toBeNull();
    await click(app, rows(app)[0]!);
    q(app, '.confirm')!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    await settle(app);
    expect(q(app, '.confirm')).toBeNull();
    expect(agent.actions).toHaveLength(0);

    await click(app, rows(app)[0]!);
    await click(app, q(app, '.confirm .ok')!);
    expect(agent.actions).toHaveLength(1);
    const drill = agent.actions[0]!.action;
    expect(drill).toMatchObject({name: 'drill', surfaceId: 's1', sourceComponentId: 'grid', context: {source: 'v-s1', row: 0}});

    await click(app, tabButton(app, 'q')!);
    await click(app, rows(app)[1]!);
    await click(app, q(app, '.confirm .ask-within')!);
    expect(agent.actions).toHaveLength(2);
    const ask = agent.actions[1]!.action;
    expect(ask).toMatchObject({name: 'ask', surfaceId: 's1', sourceComponentId: 'grid', context: {source: 'v-s1', row: 1}});
    expect(Object.keys(ask).sort()).toEqual(['context', 'name', 'sourceComponentId', 'surfaceId', 'timestamp']);
    expect(agent.actions[1]!.viewing).toBe('t1');
  });

  it('a row outside boards behaves as before: a Drill-down at once, an Ask confirmed (E4)', async () => {
    const drills = fakeAgent(() => [final('s1', 't1', 'q', '', {boards: [1]})], () => [final('s2', 't2', 'A')]);
    let app = await mount(drills);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    expect(q(app, '.confirm')).toBeNull();
    expect(drills.actions.map(a => a.action.name)).toEqual(['drill']);
    document.body.innerHTML = '';

    const asks = fakeAgent(() => [final('s1', 't1', 'q', '', {drillable: false, boards: [1]})], () => [final('s2', 't2', 'A')]);
    app = await mount(asks);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    expect(q(app, '.confirm.board-choice')).toBeNull();
    expect(q(app, '.confirm')!.textContent).toContain('Ask the agent: A');
    await click(app, q(app, '.confirm .ok')!);
    expect(asks.actions.map(a => a.action.name)).toEqual(['ask']);
  });

  it('labels a Follow-up confirmation with its button text', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q', '', {followUp: true})], () => [final('s2', 't2', 'By year')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    const button = [...app.querySelectorAll('a2ui-surface button')].find(b => b.textContent?.includes('By year')) as HTMLButtonElement;
    await click(app, button);
    expect(q(app, '.confirm')!.textContent).toContain('Ask the agent: By year');
    await click(app, q(app, '.confirm .ok')!);
    expect(agent.actions[0]!.action.context).toEqual({follow_up: 'f1'});
  });

  it('switches tabs from memory, and opens a new tab right after the current one', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q')], (_a, n) => [final(`s${n + 1}`, `t${n + 1}`, n === 1 ? 'A' : 'B')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    await click(app, tabButton(app, 'q'));
    expect(agent.actions).toHaveLength(1); // no agent call
    expect(selectedTitle(app)).toBe('q');
    await click(app, rows(app)[1]!);
    expect(titles(app)).toEqual(['q', 'B', 'A']); // after q, not at the end
    expect(selectedTitle(app)).toBe('B');
    expect(entryTexts(app)).toEqual(['log q', 'log A', 'log B']); // the log keeps the order they opened in
  });

  it('sends the selected tab\'s server ID as explorerViewing', async () => {
    const agent = fakeAgent(n => [final(`s${n}`, `t${n + 1}`, `q${n}`)], () => [final('d', 't2', 'A')]);
    const app = await mount(agent);
    await app.ask('first'); // t2 (t1 would be a Board)
    await settle(app);
    await app.ask('second'); // t3
    await settle(app);
    await click(app, tabButton(app, 'q1'));
    await app.ask('third');
    await settle(app);
    expect(agent.questions.map(x => x.viewing)).toEqual([undefined, 't2', 't2']);
  });

  it('disables clicks while an Action runs; tabs still switch; a running tab shows its spinner and can\'t close', async () => {
    let release!: () => void;
    const agent = fakeAgent(() => [final('s1', 't1', 'q')], () => [final('s2', 't2', 'A')]);
    agent.gate = new Promise(r => (release = r));
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    expect(selectedTitle(app)).toBe('A');
    expect(q(app, '.tab-strip .tab.selected .spinner')).not.toBeNull();
    expect((q(app, '.tab-strip .tab.selected .tab-close') as HTMLButtonElement).disabled).toBe(true);
    expect(entries(app)[1]!.querySelector('.spinner')).not.toBeNull();
    expect(entryTexts(app)[1]).toBe('A'); // the clicked label until the reply
    await click(app, tabButton(app, 'q')); // tabs work while something runs
    expect(selectedTitle(app)).toBe('q');
    expect(tabs(app)[1]!.querySelector('.spinner')).not.toBeNull(); // the running tab keeps running unselected
    expect(entries(app)[1]!.querySelector('.spinner')).not.toBeNull();
    expect(q(app, '.level-view')!.classList.contains('inert')).toBe(true);
    await click(app, rows(app)[1]!);
    expect(agent.actions).toHaveLength(1);
    release();
    await settle(app);
    expect(q(app, '.level-view')!.classList.contains('inert')).toBe(false);
    expect(q(app, '.tab-strip .spinner')).toBeNull();
    expect(entryTexts(app)[1]).toBe('log A');
  });

  it('shows a failed Drill-down in its tab with Retry and Ask instead; the log says Failed', async () => {
    let fail = true;
    const agent = fakeAgent(() => [final('s1', 't1', 'q')],
      () => [fail ? failed('The drill-down query failed (57014).') : final('s2', 't2', 'A')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    expect(selectedTitle(app)).toBe('A');
    expect(q(app, '.tab-view .error')!.textContent).toContain('57014');
    expect(q(app, '.tab-view .error .ask-instead')).not.toBeNull();
    expect(entries(app)[1]!.querySelector('.entry-state')!.textContent).toContain('Failed');
    await click(app, tabButton(app, 'q'));
    expect(rows(app)).toHaveLength(2); // the answer is still there and usable
    await click(app, tabButton(app, 'A'));
    fail = false;
    await click(app, q(app, '.tab-view .error .retry')!);
    expect(agent.actions).toHaveLength(2);
    expect(titles(app)).toEqual(['q', 'A']); // retried in the same tab
    expect(q(app, '.tab-view .error')).toBeNull();
  });

  it('asks the agent instead after a failed Drill-down, confirmed, in a new tab', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q')],
      a => [a.name === 'drill' ? failed('The drill-down query failed (57014).') : final('s2', 't2', 'A', 'Explained')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    await click(app, q(app, '.tab-view .error .ask-instead')!);
    expect(q(app, '.confirm')!.textContent).toContain('Ask the agent: A');
    await click(app, q(app, '.confirm .ok')!);
    expect(agent.actions.map(a => [a.action.name, a.action.context])).toEqual([
      ['drill', {source: 'v-s1', row: 0}], ['ask', {source: 'v-s1', row: 0}],
    ]);
    expect(q(app, '.tab-view .prose')!.textContent).toContain('Explained');
  });

  it('closes a tab: the one to its left is selected, the log keeps it, and the log reopens it from memory', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q')], (_a, n) => [final(`s${n + 1}`, `t${n + 1}`, n === 1 ? 'A' : 'B')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    await click(app, q(app, '.tab-strip .tab.selected .tab-close')!);
    expect(titles(app)).toEqual(['q']);
    expect(selectedTitle(app)).toBe('q');
    expect(entryTexts(app)).toEqual(['log q', 'log A']);
    await click(app, entries(app)[1]!);
    expect(titles(app)).toEqual(['q', 'A']);
    expect(selectedTitle(app)).toBe('A');
    expect(app.querySelector('.tab-view a2ui-surface')).not.toBeNull();
    expect(agent.actions).toHaveLength(1); // reopened with no agent call
  });

  it(`keeps at most ${MAX_TABS} tabs open, closing the oldest unselected one`, async () => {
    const agent = fakeAgent(n => [final(`s${n}`, `t${n}`, `q${n}`)], () => []);
    const app = await mount(agent);
    for (let i = 1; i <= MAX_TABS + 1; i++) {
      await app.ask(`q${i}`);
      await settle(app);
    }
    expect(titles(app)).toEqual(Array.from({length: MAX_TABS}, (_, i) => `q${i + 2}`));
    expect(entries(app)).toHaveLength(MAX_TABS + 1); // q1 is still in the log
  });

  it('marks a new tab NEW until it is selected away from', async () => {
    const agent = fakeAgent(n => [final(`s${n}`, `t${n}`, `q${n}`)], () => []);
    const app = await mount(agent);
    await app.ask('q1');
    await settle(app);
    const isNew = () => tabs(app).map(t => !!t.querySelector('.new-tag'));
    expect(isNew()).toEqual([true]);
    await app.ask('q2');
    await settle(app);
    expect(isNew()).toEqual([false, true]);
    await click(app, tabButton(app, 'q1'));
    expect(isNew()).toEqual([false, false]);
  });

  it('renders titles and log text as plain text', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', '<b>x</b>')], () => []);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    expect(q(app, '.tab-strip b')).toBeNull();
    expect(titles(app)).toEqual(['<b>x</b>']);
    expect(entryTexts(app)).toEqual(['log <b>x</b>']);
  });

  it('names the serving model per tab, or none for a Drill-down', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q', 'Hi', {}, 'meta/muse-spark-1.3')], () => [final('s2', 't2', 'A', '', {}, null)]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    const line = () => q(app, '.canvas .meta summary')!.textContent!.replace(/\s+/g, ' ').trim();
    expect(line()).toMatch(/^Answered in \d+\.\d s · meta\/muse-spark-1\.3$/);
    await click(app, rows(app)[0]!);
    expect(line()).toMatch(/^Answered in \d+\.\d s · 1 step · No model · database only$/);
    await click(app, tabButton(app, 'q'));
    expect(line()).toContain('meta/muse-spark-1.3');
  });

  it('shows a real Drill-down\'s line with no steps and no model', async () => {
    const agent = fakeAgent(() => [final('s1', 't1', 'q', '', {}, 'm')], () => [final('s2', 't2', 'A', '', {}, null)],
      {actionStatus: false});
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    await click(app, rows(app)[0]!);
    expect(q(app, '.canvas .meta summary')!.textContent!.replace(/\s+/g, ' ').trim())
      .toMatch(/^Answered in \d+\.\d s · No model · database only$/);
  });

  it('keeps the question\'s explorerViewing on Retry', async () => {
    const agent = fakeAgent(n => (n === 2 ? new Error('timed out') : [final(`s${n}`, `t${n}`, 'first')]), () => []);
    const app = await mount(agent);
    await app.ask('first');
    await settle(app);
    await app.ask('why?');
    await settle(app);
    expect(q(app, '.tab-view .error')).not.toBeNull();
    expect(q(app, '.tab-view .meta summary')!.textContent!.replace(/\s+/g, ' ').trim()).toMatch(/^Failed after \d+\.\d s$/);
    await click(app, tabButton(app, 'first')); // the selection changes; Retry still sends what was sent
    await click(app, tabButton(app, 'why?'));
    await click(app, q(app, '.tab-view button.retry')!);
    expect(agent.questions.map(x => x.viewing)).toEqual([undefined, 't1', 't1']);
  });
});
