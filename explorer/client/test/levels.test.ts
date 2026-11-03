// Levels, Breadcrumb and Actions in the chat view (explorer SPEC §15: A14, A15), with one Turn on
// the Canvas and the Sidebar to switch (SPEC §16: L2–L4, L6).
import {afterEach, describe, expect, it} from 'vitest';
import type {AgentConnection, AskEvent, UiAction} from '../src/a2a.js';
import '../src/app.js';
import {ACTION_FAILED_KEY, LEVEL_KEY, MODEL_KEY, type ExplorerApp} from '../src/app.js';
import {EXPLORER_CATALOG_ID} from '../src/catalog.js';

const CTX = 'ctx-1';

/** A surface with a clickable DataGrid (rows "A", "B") and a Follow-up button. */
function surface(id: string, opts: {drillable?: boolean; followUp?: boolean} = {}): Record<string, unknown>[] {
  const children = ['grid', ...(opts.followUp ? ['more'] : [])];
  return [
    {version: 'v0.9', createSurface: {surfaceId: id, catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: id, components: [
      {id: 'root', component: 'Column', children},
      {id: 'grid', component: 'DataGrid', columns: [{key: 'name', label: 'Name'}], rows: [['A'], ['B']],
       action: {source: `v-${id}`, rows: [0, 1], drillable: opts.drillable ?? true}},
      ...(opts.followUp ? [
        {id: 'more', component: 'Button', child: 'more_t', action: {event: {name: 'ask', context: {follow_up: 'f1'}}}},
        {id: 'more_t', component: 'Text', text: 'By year'},
      ] : []),
    ]}},
  ];
}

const final = (sid: string, level: number, label: string, prose = '', opts = {}, model?: string | null): AskEvent => ({
  kind: 'final', contextId: CTX, prose, a2ui: surface(sid, opts),
  metadata: {[LEVEL_KEY]: {turn: 1, level, label, surfaceId: sid}, ...(model !== undefined ? {[MODEL_KEY]: model} : {})},
});

const failed = (message: string): AskEvent => ({kind: 'final', contextId: CTX, prose: message, a2ui: [], metadata: {[ACTION_FAILED_KEY]: true}});

interface Agent {
  questions: {text: string; viewing?: string[]}[];
  actions: {action: UiAction; viewing?: string[]}[];
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
// The Canvas shows one Turn (SPEC §16), so these read the selected one.
const rows = (app: ExplorerApp) => [...app.querySelector('.canvas .turn')!.querySelectorAll('tr.clickable')] as HTMLElement[];
const crumbs = (app: ExplorerApp) =>
  [...app.querySelector('.canvas .turn')!.querySelectorAll('.breadcrumb .level')].map(e => e.textContent?.trim());
const entries = (app: ExplorerApp) => [...app.querySelectorAll('.sidebar .turn-entry')] as HTMLButtonElement[];

afterEach(() => {
  document.body.innerHTML = '';
});

describe('Levels and Actions', () => {
  it('sends a Drill-down at once and shows the new Level in place, with no prose block', async () => {
    const agent = fakeAgent(() => [final('s1', 1, 'Overview', 'Intro')], () => [final('s2', 2, 'A')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    expect(agent.actions).toHaveLength(1);
    const {action} = agent.actions[0]!;
    expect(action).toMatchObject({name: 'drill', surfaceId: 's1', sourceComponentId: 'grid', context: {source: 'v-s1', row: 0}});
    expect(Object.keys(action).sort()).toEqual(['context', 'name', 'sourceComponentId', 'surfaceId', 'timestamp']);
    expect(crumbs(app)).toEqual(['Overview', 'A']);
    expect(app.querySelectorAll('a2ui-surface')).toHaveLength(1); // in place, not appended
    expect(q(app, '.turn .prose')).toBeNull(); // empty prose part: no prose block
  });

  it('opens a confirmation for an Ask; Cancel and Escape send nothing, Ask sends', async () => {
    const agent = fakeAgent(() => [final('s1', 1, 'Overview', '', {drillable: false})], () => [final('s2', 2, 'B', 'More')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    rows(app)[1]!.click();
    await settle(app);
    expect(q(app, '.confirm')!.textContent).toContain('Ask the agent: B');
    (q(app, '.confirm .cancel') as HTMLButtonElement).click();
    await settle(app);
    expect(q(app, '.confirm')).toBeNull();

    rows(app)[1]!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    await settle(app);
    q(app, '.confirm')!.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    await settle(app);
    expect(q(app, '.confirm')).toBeNull();
    expect(agent.actions).toHaveLength(0);

    rows(app)[1]!.click();
    await settle(app);
    (q(app, '.confirm .ok') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.actions.map(a => a.action.name)).toEqual(['ask']);
    expect(agent.actions[0]!.action.context).toEqual({source: 'v-s1', row: 1});
    expect(q(app, '.turn .prose')!.textContent).toContain('More');
  });

  it('labels a Follow-up confirmation with its button text', async () => {
    const agent = fakeAgent(() => [final('s1', 1, 'Overview', '', {followUp: true})], () => [final('s2', 2, 'By year')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    const button = [...app.querySelectorAll('a2ui-surface button')].find(b => b.textContent?.includes('By year')) as HTMLButtonElement;
    button.click();
    await settle(app);
    expect(q(app, '.confirm')!.textContent).toContain('Ask the agent: By year');
    (q(app, '.confirm .ok') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.actions[0]!.action.context).toEqual({follow_up: 'f1'});
  });

  it('goes back with the Breadcrumb from memory, and an Action from there drops the later Levels', async () => {
    const agent = fakeAgent(
      () => [final('s1', 1, 'Overview')],
      (_a, n) => [final(`s${n + 1}`, n + 1, n === 1 ? 'A' : 'B')],
    );
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    expect(crumbs(app)).toEqual(['Overview', 'A']);

    (app.querySelector('.breadcrumb .level') as HTMLButtonElement).click(); // back to Overview
    await settle(app);
    expect(agent.actions).toHaveLength(1); // no agent call
    expect(q(app, '.breadcrumb .level.current')!.textContent?.trim()).toBe('Overview');

    rows(app)[1]!.click();
    await settle(app);
    expect(crumbs(app)).toEqual(['Overview', 'B']); // "A" dropped
  });

  it('sends explorerViewing for a Level shown that is not its Turn\'s latest', async () => {
    const agent = fakeAgent(() => [final('s1', 1, 'Overview')], () => [final('s2', 2, 'A')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    (app.querySelector('.breadcrumb .level') as HTMLButtonElement).click();
    await settle(app);
    await app.ask('why?');
    await settle(app);
    expect(agent.questions[1]!.viewing).toEqual(['s1']);
  });

  it('disables clicks while an Action runs, and dims the current Level', async () => {
    let release!: () => void;
    const agent = fakeAgent(() => [final('s1', 1, 'Overview')], () => [final('s2', 2, 'A')]);
    agent.gate = new Promise(r => (release = r));
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    expect(q(app, '.level-view')!.classList.contains('inert')).toBe(true);
    rows(app)[1]!.click();
    await settle(app);
    expect(agent.actions).toHaveLength(1);
    release();
    await settle(app);
    expect(q(app, '.level-view')!.classList.contains('inert')).toBe(false);
  });

  it('shows a failed Action with Retry in place; earlier Levels stay', async () => {
    let fail = true;
    const agent = fakeAgent(() => [final('s1', 1, 'Overview')], () => [fail ? failed('The drill-down query failed (57014).') : final('s2', 2, 'A')]);
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    expect(q(app, '.action-error')!.textContent).toContain('57014');
    expect(rows(app)).toHaveLength(2); // the Overview is still shown and usable
    expect(q(app, '.action-error .ask-instead')).not.toBeNull();
    fail = false;
    (q(app, '.action-error .retry') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.actions).toHaveLength(2);
    expect(crumbs(app)).toEqual(['Overview', 'A']);
  });

  it('keeps older Turns live: an Action on Turn 1 after Turn 2', async () => {
    const agent = fakeAgent(n => [final(`t${n}`, 1, 'Overview')], () => [final('t1-2', 2, 'A')]);
    const app = await mount(agent);
    await app.ask('first');
    await settle(app);
    await app.ask('second');
    await settle(app);
    entries(app)[0]!.click(); // show Turn 1
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    expect(agent.actions[0]!.action.surfaceId).toBe('t1');
    expect(crumbs(app)).toEqual(['Overview', 'A']);
    entries(app)[1]!.click();
    await settle(app);
    expect(crumbs(app)).toEqual([]);
  });

  it('shows only the selected Turn; the Sidebar switches with no agent call; a new question is selected', async () => {
    const agent = fakeAgent(n => [final(`t${n}`, 1, 'Overview')], () => []);
    const app = await mount(agent);
    await app.ask('first');
    await settle(app);
    await app.ask('second');
    await settle(app);
    expect(app.querySelectorAll('.canvas .turn')).toHaveLength(1);
    expect(app.querySelectorAll('a2ui-surface')).toHaveLength(1);
    expect(q(app, '.canvas .question')!.textContent).toContain('second');
    expect(entries(app).map(e => e.querySelector('.entry-question')!.textContent)).toEqual(['first', 'second']);
    expect(entries(app).map(e => e.getAttribute('aria-current'))).toEqual(['false', 'page']);
    expect(q(app, '.sidebar .count')!.textContent).toContain('2');

    entries(app)[0]!.click();
    await settle(app);
    expect(q(app, '.canvas .question')!.textContent).toContain('first');
    expect(entries(app).map(e => e.getAttribute('aria-current'))).toEqual(['page', 'false']);
    expect(agent.questions).toHaveLength(2);
    expect(agent.actions).toHaveLength(0);
  });

  it('keeps a running Turn running, with its spinner, while another is selected', async () => {
    let release!: () => void;
    const agent = fakeAgent(n => [final(`t${n}`, 1, 'Overview')], () => [final('t1-2', 2, 'A')]);
    const app = await mount(agent);
    await app.ask('first');
    await settle(app);
    await app.ask('second');
    await settle(app);
    entries(app)[0]!.click();
    await settle(app);
    agent.gate = new Promise(r => (release = r));
    rows(app)[0]!.click();
    await settle(app);
    entries(app)[1]!.click(); // the Sidebar works while something runs
    await settle(app);
    expect(q(app, '.canvas .question')!.textContent).toContain('second');
    expect(entries(app)[0]!.querySelector('.spinner')).not.toBeNull();
    expect(entries(app)[1]!.querySelector('.spinner')).toBeNull();
    release();
    await settle(app);
    expect(entries(app)[0]!.querySelector('.spinner')).toBeNull();
    expect(entries(app)[0]!.querySelector('.entry-state')!.textContent).toContain('2 levels');
    entries(app)[0]!.click();
    await settle(app);
    expect(crumbs(app)).toEqual(['Overview', 'A']);
  });

  it('sends explorerViewing only for the selected Turn', async () => {
    const agent = fakeAgent(n => [final(`t${n}`, 1, 'Overview')], () => [final('t1-2', 2, 'A')]);
    const app = await mount(agent);
    await app.ask('first');
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    (app.querySelector('.breadcrumb .level') as HTMLButtonElement).click(); // Turn 1 back at its Overview
    await settle(app);
    await app.ask('second'); // Turn 1 was on screen
    await settle(app);
    expect(agent.questions[1]!.viewing).toEqual(['t1']);
    await app.ask('third'); // Turn 2 (at its latest) was on screen; Turn 1 is not
    await settle(app);
    expect(agent.questions[2]!.viewing).toEqual([]);
  });

  it('names the serving model per Level, or none for a Drill-down', async () => {
    const agent = fakeAgent(
      () => [final('s1', 1, 'Overview', 'Hi', {}, 'meta/muse-spark-1.3')],
      () => [final('s2', 2, 'A', '', {}, null)],
    );
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    const line = () => q(app, '.canvas .meta summary')!.textContent!.replace(/\s+/g, ' ').trim();
    expect(line()).toMatch(/^Answered in \d+\.\d s · meta\/muse-spark-1\.3$/);
    rows(app)[0]!.click();
    await settle(app);
    expect(line()).toMatch(/^Answered in \d+\.\d s · 1 step · No model · database only$/);
    (app.querySelector('.breadcrumb .level') as HTMLButtonElement).click();
    await settle(app);
    expect(line()).toContain('meta/muse-spark-1.3');
  });

  it('shows a real Drill-down\'s line with no steps and no model', async () => {
    const agent = fakeAgent(() => [final('s1', 1, 'Overview', '', {}, 'm')], () => [final('s2', 2, 'A', '', {}, null)],
      {actionStatus: false});
    const app = await mount(agent);
    await app.ask('q');
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    expect(q(app, '.canvas .meta summary')!.textContent!.replace(/\s+/g, ' ').trim())
      .toMatch(/^Answered in \d+\.\d s · No model · database only$/);
  });

  it('keeps the question\'s explorerViewing on Retry', async () => {
    const agent = fakeAgent(n => (n === 2 ? new Error('timed out') : [final(`t${n}`, 1, 'Overview')]), () => [final('t1-2', 2, 'A')]);
    const app = await mount(agent);
    await app.ask('first');
    await settle(app);
    rows(app)[0]!.click();
    await settle(app);
    (app.querySelector('.breadcrumb .level') as HTMLButtonElement).click(); // Turn 1 at its Overview
    await settle(app);
    await app.ask('why?');
    await settle(app);
    expect(q(app, '.turn .error')).not.toBeNull();
    (q(app, '.turn button.retry') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.questions.map(x => x.viewing)).toEqual([[], ['t1'], ['t1']]);
  });
});
