import {afterEach, describe, expect, it} from 'vitest';
import type {AgentConnection, AskEvent} from '../src/a2a.js';
import '../src/app.js';
import type {ExplorerApp} from '../src/app.js';

interface Call {
  text: string;
  contextId?: string;
}

/** A fake agent: each ask yields `script(text)`, or throws when the script returns an Error. */
function fakeAgent(script: (text: string) => AskEvent[] | Error, examples = ['Top suppliers?', 'Spend by year?']) {
  const calls: Call[] = [];
  const connection: AgentConnection = {
    info: {description: 'Demo contracts', examples},
    async *askQuestion(text, contextId) {
      calls.push({text, contextId});
      const result = script(text);
      if (result instanceof Error) throw result;
      yield* result;
    },
    // eslint-disable-next-line require-yield
    async *act() {
      throw new Error('no actions in these tests');
    },
  };
  return {calls, connector: async () => connection};
}

const answer = (prose: string, contextId = 'ctx-1'): AskEvent[] => [
  {kind: 'status', text: 'Running SQL: top suppliers'},
  {kind: 'final', contextId, prose, a2ui: []},
];

async function mount(connector: () => Promise<AgentConnection>): Promise<ExplorerApp> {
  window.history.replaceState(null, '', window.location.pathname);
  const app = document.createElement('explorer-app') as ExplorerApp;
  app.workspaceApi = oneWorkspace();
  app.connector = connector;
  document.body.appendChild(app);
  await settle(app);
  return app;
}

async function settle(app: ExplorerApp) {
  for (let i = 0; i < 5; i++) {
    await new Promise(r => setTimeout(r, 0));
    await app.updateComplete;
  }
}

const q = (app: ExplorerApp, sel: string) => app.querySelector(sel);
const texts = (app: ExplorerApp, sel: string) => [...app.querySelectorAll(sel)].map(e => e.textContent?.trim());

afterEach(() => {
  document.body.innerHTML = '';
});

describe('explorer-app', () => {
  it('shows the dataset description and example questions before the first question', async () => {
    const app = await mount(fakeAgent(() => answer('x')).connector);
    expect(q(app, '.sidebar .workspaces .dataset')!.textContent).toContain('Demo contracts'); // SPEC §24: under the picker
    expect(q(app, '.canvas .examples')).not.toBeNull();
    expect(texts(app, '.examples button')).toEqual(['Top suppliers?', 'Spend by year?']);
  });

  it('shows the Glassline mark and name at the top of the Sidebar', async () => {
    const app = await mount(fakeAgent(() => answer('x')).connector);
    const mark = q(app, '.sidebar .brand img') as HTMLImageElement;
    expect(mark.getAttribute('src')).toBe('/glassline-mark.png');
    expect(mark.getAttribute('alt')).toBe(''); // decorative: the name beside it is the text
    expect(q(app, '.sidebar .brand h1')!.textContent!.trim()).toBe('Glassline');
  });

  it('asks an example question when it is clicked, and hides the examples', async () => {
    const agent = fakeAgent(() => answer('**Supplier A** received the most.'));
    const app = await mount(agent.connector);
    (q(app, '.examples button') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.calls).toEqual([{text: 'Top suppliers?', contextId: undefined}]);
    expect(q(app, '.examples')).toBeNull();
    expect(q(app, '.tab-view .question')!.textContent).toContain('Top suppliers?');
    expect(q(app, '.tab-view .prose strong')!.textContent).toBe('Supplier A');
    expect(q(app, '.tab-view .elapsed')!.textContent).toMatch(/^Answered in \d+\.\d s$/);
    // No explorerModel from the agent: the status line names no model.
    expect(q(app, '.tab-view .meta summary')!.textContent!.replace(/\s+/g, ' ').trim()).toMatch(/^Answered in \d+\.\d s · 1 step$/);
  });

  it('sends on Enter but not on Shift+Enter, and keeps the conversation', async () => {
    const agent = fakeAgent(() => answer('ok'));
    const app = await mount(agent.connector);
    const box = q(app, 'textarea') as HTMLTextAreaElement;

    box.value = 'first';
    box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', shiftKey: true, bubbles: true}));
    await settle(app);
    expect(agent.calls).toEqual([]);

    box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    await settle(app);
    box.value = 'second';
    box.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    await settle(app);

    expect(agent.calls).toEqual([{text: 'first', contextId: undefined}, {text: 'second', contextId: 'ctx-1'}]);
    expect(box.value).toBe('');
    expect(box.closest('.sidebar')).not.toBeNull(); // the composer sits in the Sidebar (SPEC §16)
  });

  it('starts a new conversation: clears the turns and forgets the context', async () => {
    const agent = fakeAgent(() => answer('ok'));
    const app = await mount(agent.connector);
    await app.ask('first');
    await settle(app);
    (q(app, 'button.new-conversation') as HTMLButtonElement).click();
    await settle(app);
    expect(app.querySelectorAll('.tab-view')).toHaveLength(0);
    expect(q(app, '.examples')).not.toBeNull();
    await app.ask('again');
    expect(agent.calls.at(-1)).toEqual({text: 'again', contextId: undefined});
  });

  it('shows an error with Retry, which asks the same question again', async () => {
    let fail = true;
    const agent = fakeAgent(() => (fail ? new Error('Turn timed out') : answer('recovered')));
    const app = await mount(agent.connector);
    await app.ask('slow one');
    await settle(app);
    expect(q(app, '.tab-view .error')!.textContent).toContain('Turn timed out');
    expect(q(app, '.sidebar .log-entry .entry-state')!.textContent).toContain('Failed');
    expect(q(app, '.tab-view .meta summary')!.textContent!.replace(/\s+/g, ' ').trim()).toMatch(/^Failed after \d+\.\d s$/);

    fail = false;
    (q(app, '.tab-view button.retry') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.calls.map(c => c.text)).toEqual(['slow one', 'slow one']);
    expect(app.querySelectorAll('.tab-view')).toHaveLength(1);
    expect(q(app, '.tab-view .error')).toBeNull();
    expect(q(app, '.tab-view .prose')!.textContent).toContain('recovered');
  });

  it('says when it cannot reach the agent, and reconnects on Retry', async () => {
    let up = false;
    const agent = fakeAgent(() => answer('ok'));
    const app = await mount(async () => {
      if (!up) throw new Error('fetch failed');
      return agent.connector();
    });
    expect(q(app, '.banner')!.textContent).toContain('Cannot reach the agent');

    up = true;
    (q(app, '.banner button') as HTMLButtonElement).click();
    await settle(app);
    expect(q(app, '.banner')).toBeNull();
    expect(texts(app, '.examples button')).toHaveLength(2);
  });
});

// --- The Explorer Board on load (SPEC §18, slices 1a and 1b) -----------------------------------------------

import {EXPLORER_CATALOG_ID} from '../src/catalog.js';
import {ACTION_FAILED_KEY, LEVEL_KEY, LOG_KEY, NO_BOARDS_KEY, OPEN_BOARD, TAB_KEY} from '../src/app.js';
import type {UiAction} from '../src/a2a.js';

const BOARD_SID = 'turn-0-1-abc';
const boardReply = (contextId = 'ctx-board'): AskEvent => ({
  kind: 'final', contextId, prose: '',
  a2ui: [
    {version: 'v0.9', createSurface: {surfaceId: BOARD_SID, catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: BOARD_SID, components: [
      {id: 'root', component: 'Column', children: ['board_title']},
      {id: 'board_title', component: 'Text', text: 'Explorer', variant: 'h2'},
    ]}},
  ],
  metadata: {
    [LEVEL_KEY]: {turn: 0, level: 1, label: 'Explorer', surfaceId: BOARD_SID},
    [TAB_KEY]: {id: 't1', kind: 'board', title: 'Explorer'},
    [LOG_KEY]: {kind: 'board', tag: 'NEW TAB · NO MODEL', text: 'Opened Explorer'},
  },
});

function boardAgent(reply: () => AskEvent, gate?: Promise<void>) {
  const calls: Call[] = [];
  const acts: {action: UiAction; contextId?: string}[] = [];
  const connection: AgentConnection = {
    info: {description: 'Demo contracts', examples: ['Top suppliers?']},
    async *askQuestion(text, contextId) {
      calls.push({text, contextId});
      yield* answer('ok', contextId ?? 'ctx-new');
    },
    async *act(action, contextId) {
      acts.push({action, contextId});
      if (gate) await gate;
      yield reply();
    },
  };
  return {calls, acts, connector: async () => connection};
}

describe('explorer-app Explorer Board', () => {
  it('opens the Explorer on load as the first tab, above the examples; the first question joins its conversation', async () => {
    const agent = boardAgent(() => boardReply());
    const app = await mount(agent.connector);
    expect(agent.acts.map(a => [a.action.name, a.action.context, a.contextId])).toEqual([[OPEN_BOARD, {board: 'explorer'}, undefined]]);
    const board = q(app, '.canvas .tab-view a2ui-surface');
    expect(board).not.toBeNull();
    expect(board!.textContent).toContain('Explorer');
    // The examples sit below it until the first question.
    expect(board!.closest('.tab-view')!.compareDocumentPosition(q(app, '.canvas .examples')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(texts(app, '.tab-strip .tab-title')).toEqual(['Explorer']);
    expect(q(app, '.tab-strip .tab-close')).toBeNull(); // the Explorer can't be closed
    expect(q(app, '.tab-strip .new-tag')).toBeNull();
    expect(texts(app, '.sidebar .log-entry .log-text')).toEqual(['Opened Explorer']);
    expect(texts(app, '.sidebar .log-entry .log-tag')).toEqual(['NEW TAB · NO MODEL']);
    (q(app, '.examples button') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.calls).toEqual([{text: 'Top suppliers?', contextId: 'ctx-board'}]);
    expect(texts(app, '.tab-strip .tab-title')).toEqual(['Explorer', 'Top suppliers?']);
    expect(q(app, '.canvas .tab-view .question')!.textContent).toContain('Top suppliers?');
    expect(q(app, '.canvas .examples')).toBeNull();
    expect(q(app, '.canvas .tab-view')!.classList.contains('board')).toBe(false); // SPEC §24: Board styling is for Boards only
    (q(app, '.tab-strip .tab-select') as HTMLButtonElement).click();
    await settle(app);
    expect(q(app, '.canvas .tab-view')!.classList.contains('board')).toBe(true);
  });

  it('a question asked before the Board arrives shows its tab at once, then sends in the Board\'s conversation', async () => {
    let open!: () => void;
    const agent = boardAgent(() => boardReply(), new Promise<void>(r => (open = r)));
    const app = await mount(agent.connector);
    void app.ask('Early?');
    await settle(app);
    expect(agent.calls).toEqual([]);
    expect(q(app, '.canvas .tab-view .question')!.textContent).toContain('Early?');
    expect(q(app, '.canvas .tab-view .status .spinner')).not.toBeNull(); // feedback while it waits
    open();
    await settle(app);
    expect(agent.calls).toEqual([{text: 'Early?', contextId: 'ctx-board'}]);
    expect(texts(app, '.tab-strip .tab-title')).toEqual(['Explorer', 'Early?']); // the Explorer goes first
    expect(q(app, '.canvas .tab-view .question')!.textContent).toContain('Early?'); // the question stays selected
  });

  it('without Boards shows the empty state as before', async () => {
    const agent = boardAgent(() => ({kind: 'final', contextId: 'ctx-x', prose: 'none', a2ui: [],
      metadata: {[ACTION_FAILED_KEY]: true, [NO_BOARDS_KEY]: true}}));
    const app = await mount(agent.connector);
    expect(q(app, '.tab-strip')).toBeNull(); // no Explorer tab
    expect(texts(app, '.examples button')).toEqual(['Top suppliers?']);
    (q(app, '.examples button') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.calls[0]!.contextId).toBeUndefined(); // a rejected open_board started no conversation for us
  });

  it('a new conversation opens the Explorer again', async () => {
    const agent = boardAgent(() => boardReply());
    const app = await mount(agent.connector);
    (q(app, '.examples button') as HTMLButtonElement).click();
    await settle(app);
    (q(app, '.new-conversation') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.acts.map(a => [a.action.name, a.contextId])).toEqual([[OPEN_BOARD, undefined], [OPEN_BOARD, undefined]]);
    expect(q(app, '.canvas .tab-view a2ui-surface')).not.toBeNull();
    expect(texts(app, '.tab-strip .tab-title')).toEqual(['Explorer']);
  });
});

// --- Controls on the Explorer Board (SPEC §18 slice 2) ---------------------------------------------------

import {BOARD_KEY, CONTROLS, IN_PLACE_KEY, SUPERSEDED_KEY} from '../src/app.js';

const ctrlBoardReply = (): AskEvent => ({
  kind: 'final', contextId: 'ctx-board', prose: '',
  a2ui: [
    {version: 'v0.9', createSurface: {surfaceId: BOARD_SID, catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: BOARD_SID, components: [
      {id: 'root', component: 'Column', children: ['board_heading', 'board_controls', 'board_layout']},
      {id: 'board_heading', component: 'Text', text: 'Explorer', variant: 'h2'},
      {id: 'board_controls', component: 'Text', text: 'controls'},
      {id: 'board_layout', component: 'Column', children: ['by_year']},
      {id: 'by_year', component: 'Text', text: 'Spend 10'},
    ]}},
    {version: 'v0.9', updateDataModel: {surfaceId: BOARD_SID, path: '/controls', value: {buyer: ['all']}}},
  ],
  metadata: {
    [LEVEL_KEY]: {turn: 0, level: 1, label: 'Explorer', surfaceId: BOARD_SID},
    [TAB_KEY]: {id: 't1', kind: 'board', title: 'Explorer'},
    [LOG_KEY]: {kind: 'board', tag: 'NEW TAB · NO MODEL', text: 'Opened Explorer'},
    [BOARD_KEY]: {id: 'b1', controls: [{name: 'buyer', kind: 'choice'}]},
  },
});

const inPlaceReply = (seq: number, spend: string, text: string): AskEvent => ({
  kind: 'final', contextId: 'ctx-board', prose: '',
  a2ui: [{version: 'v0.9', updateComponents: {surfaceId: BOARD_SID, components: [{id: 'by_year', component: 'Text', text: spend}]}}],
  metadata: {[IN_PLACE_KEY]: {board: 'b1', seq}, [LOG_KEY]: {kind: 'controls', tag: 'IN PLACE · NO MODEL', text}},
});

const failedReply = (prose: string): AskEvent => ({kind: 'final', contextId: 'ctx-board', prose, a2ui: [], metadata: {[ACTION_FAILED_KEY]: true}});

/** A Board agent whose `controls` replies come from `onControls`, each released by its own gate. */
function controlsAgent(onControls: (seq: number) => {reply: AskEvent | Error; gate?: Promise<void>}, board = ctrlBoardReply) {
  const sent: {seq: number; values: unknown; surfaceId: string; contextId?: string}[] = [];
  const connection: AgentConnection = {
    info: {description: 'Demo contracts', examples: ['Top suppliers?']},
    async *askQuestion(_text, contextId) {
      yield* answer('ok', contextId ?? 'ctx-new');
    },
    async *act(action, contextId) {
      if (action.name === OPEN_BOARD) {
        yield board();
        return;
      }
      const ctx = action.context as {board: string; seq: number; values: unknown};
      expect(action.name).toBe(CONTROLS);
      sent.push({seq: ctx.seq, values: ctx.values, surfaceId: action.surfaceId, contextId});
      const {reply, gate} = onControls(ctx.seq);
      if (gate) await gate;
      if (reply instanceof Error) throw reply;
      yield reply;
    },
  };
  return {sent, connector: async () => connection};
}

function gate() {
  let open!: () => void;
  const promise = new Promise<void>(r => (open = r));
  return {promise, open};
}

// The processor is private; tests write the data model as a ChoicePicker would.
const dataModel = (app: ExplorerApp) =>
  (app as unknown as {processor: MessageProcessor<ComponentApi>}).processor.model.surfacesMap.get(BOARD_SID)!.dataModel;
const surfaceText = (app: ExplorerApp) => q(app, '.canvas a2ui-surface')!.textContent!;
const logTexts = (app: ExplorerApp) => texts(app, '.sidebar .log-entry .log-text');

import type {ComponentApi, MessageProcessor} from '@a2ui/web_core/v0_9';
import {oneWorkspace} from './one-workspace.js';

describe('explorer-app Controls', () => {
  it('sends a change, dims the layout while pending, then replaces the panel and logs it in place', async () => {
    const g = gate();
    const agent = controlsAgent(seq => ({reply: inPlaceReply(seq, 'Spend 20', 'Buyer set to Dept A'), gate: g.promise}));
    const app = await mount(agent.connector);
    expect(surfaceText(app)).toContain('Spend 10');

    dataModel(app).set('/controls/buyer', ['0']);
    await settle(app);
    expect(agent.sent).toEqual([{seq: 1, values: {buyer: 0}, surfaceId: BOARD_SID, contextId: 'ctx-board'}]);
    expect(q(app, '.canvas .level-view.requery')).not.toBeNull();
    expect(q(app, '.canvas .level-view.inert')).toBeNull(); // not busy: the composer stays enabled
    expect((q(app, '.composer button') as HTMLButtonElement).disabled).toBe(false);

    g.open();
    await settle(app);
    expect(q(app, '.canvas .level-view.requery')).toBeNull();
    expect(surfaceText(app)).toContain('Spend 20');
    expect(texts(app, '.tab-strip .tab-title')).toEqual(['Explorer']); // in place: no new tab
    expect(logTexts(app)).toEqual(['Opened Explorer', 'Buyer set to Dept A']);
    expect(texts(app, '.sidebar .log-entry.in-place .log-tag')).toEqual(['IN PLACE · NO MODEL']);
  });

  it('merges consecutive changes into one log entry, but not across another tab', async () => {
    const labels = ['Buyer set to Dept A', 'Buyer set to Dept B', 'Controls reset'];
    const agent = controlsAgent(seq => ({reply: inPlaceReply(seq, `Spend ${seq}`, labels[seq - 1]!)}));
    const app = await mount(agent.connector);
    dataModel(app).set('/controls/buyer', ['0']);
    await settle(app);
    dataModel(app).set('/controls/buyer', ['1']);
    await settle(app);
    expect(logTexts(app)).toEqual(['Opened Explorer', 'Buyer set to Dept B']);

    await app.ask('Top suppliers?');
    await settle(app);
    (q(app, '.sidebar .log-entry.in-place') as HTMLButtonElement).click(); // selects its tab
    await settle(app);
    expect(q(app, '.canvas a2ui-surface')).not.toBeNull();
    dataModel(app).set('/controls/buyer', ['all']);
    await settle(app);
    expect(logTexts(app)).toEqual(['Opened Explorer', 'Buyer set to Dept B', 'Top suppliers?', 'Controls reset']);
  });

  it('ignores a superseded reply and an older in-place reply that arrives after a newer one', async () => {
    const gates = [gate(), gate()];
    const agent = controlsAgent(seq => seq === 1
      ? {reply: inPlaceReply(1, 'Spend old', 'Buyer set to Dept A'), gate: gates[0]!.promise}
      : {reply: inPlaceReply(2, 'Spend new', 'Buyer set to Dept B'), gate: gates[1]!.promise});
    const app = await mount(agent.connector);
    dataModel(app).set('/controls/buyer', ['0']);
    dataModel(app).set('/controls/buyer', ['1']);
    await settle(app);
    expect(agent.sent.map(s => s.seq)).toEqual([1, 2]);
    gates[1]!.open();
    await settle(app);
    expect(q(app, '.canvas .level-view.requery')).not.toBeNull(); // seq 1 still pending
    gates[0]!.open();
    await settle(app);
    expect(surfaceText(app)).toContain('Spend new'); // the older reply was not applied
    expect(logTexts(app)).toEqual(['Opened Explorer', 'Buyer set to Dept B']);
    expect(dataModel(app).get('/controls/buyer')).toEqual(['1']); // not reverted
    expect(q(app, '.canvas .level-view.requery')).toBeNull();

    const sup = controlsAgent(() => ({reply: {kind: 'final', contextId: 'ctx-board', prose: '', a2ui: [], metadata: {[SUPERSEDED_KEY]: true}}}));
    const app2 = await mount(sup.connector);
    dataModel(app2).set('/controls/buyer', ['0']);
    await settle(app2);
    expect(dataModel(app2).get('/controls/buyer')).toEqual(['0']); // superseded: not reverted
    expect(surfaceText(app2)).toContain('Spend 10');
    expect(logTexts(app2)).toEqual(['Opened Explorer']);
  });

  it('ignores a drill from the Board while its re-query is pending, and accepts it after', async () => {
    const g = gate();
    const drills: string[] = [];
    const base = controlsAgent(seq => ({reply: inPlaceReply(seq, 'Spend 20', 'Buyer set to Dept A'), gate: g.promise}));
    const connector = async () => {
      const c = await base.connector();
      return {...c, async *act(action: UiAction, contextId?: string, viewing?: string) {
        if (action.name === 'drill') {
          drills.push(action.sourceComponentId);
          yield {kind: 'final' as const, contextId: 'ctx-board', prose: '', a2ui: [], metadata: {[ACTION_FAILED_KEY]: true}};
          return;
        }
        yield* c.act(action, contextId, viewing);
      }};
    };
    const app = await mount(connector);
    const surface = (app as unknown as {processor: MessageProcessor<ComponentApi>}).processor.model.surfacesMap.get(BOARD_SID)!;
    dataModel(app).set('/controls/buyer', ['0']);
    await settle(app);
    await surface.dispatchAction({event: {name: 'drill', context: {source: 'v1', row: 0}}}, 'by_year');
    await settle(app);
    expect(drills).toEqual([]);
    g.open();
    await settle(app);
    await surface.dispatchAction({event: {name: 'drill', context: {source: 'v2', row: 0}}}, 'by_year');
    await settle(app);
    expect(drills).toEqual(['by_year']);
  });

  it('disables the Board, Controls included, while a model run is going (B13)', async () => {
    const g = gate();
    const base = controlsAgent(seq => ({reply: inPlaceReply(seq, 'Spend 20', 'x')}));
    const connector = async () => {
      const c = await base.connector();
      return {...c, async *askQuestion(text: string, contextId?: string) {
        await g.promise;
        yield* answer('ok', contextId ?? 'ctx-board');
      }};
    };
    const app = await mount(connector);
    void app.ask('Slow?');
    await settle(app);
    (q(app, '.tab-strip .tab-select') as HTMLButtonElement).click(); // back to the Explorer
    await settle(app);
    expect(q(app, '.canvas a2ui-surface')!.closest('.level-view')!.classList.contains('inert')).toBe(true);
    g.open();
    await settle(app);
    (q(app, '.tab-strip .tab-select') as HTMLButtonElement).click();
    await settle(app);
    expect(q(app, '.canvas a2ui-surface')!.closest('.level-view')!.classList.contains('inert')).toBe(false);
  });

  it('reverts the Controls on "busy", a rejection or a network failure, without sending again', async () => {
    for (const reply of [failedReply('The agent is busy answering; try again when it has finished.'), failedReply('bad value'), new Error('fetch failed')]) {
      document.body.innerHTML = '';
      const agent = controlsAgent(() => ({reply}));
      const app = await mount(agent.connector);
      dataModel(app).set('/controls/buyer', ['0']);
      await settle(app);
      expect(dataModel(app).get('/controls/buyer')).toEqual(['all']);
      expect(agent.sent).toHaveLength(1); // the revert's write sent nothing
      expect(surfaceText(app)).toContain('Spend 10');
      expect(logTexts(app)).toEqual(['Opened Explorer']);
      expect(q(app, '.canvas .level-view.requery')).toBeNull();
    }
  });

  it('keeps the focused Control element, and its focus, across an in-place reply (B13)', async () => {
    // The real Controls Card shape (`boards.controls_components`): a Card over a Column of inputs.
    const withInputs = (): AskEvent => {
      const r = ctrlBoardReply() as Extract<AskEvent, {kind: 'final'}>;
      const comps = (r.a2ui[1] as {updateComponents: {components: Record<string, unknown>[]}}).updateComponents.components;
      comps.splice(2, 1,
        {id: 'board_controls', component: 'Card', child: 'board_controls_items'},
        {id: 'board_controls_items', component: 'Column', children: ['board_control_buyer', 'board_control_spend']},
        {id: 'board_control_buyer', component: 'ChoicePicker', label: 'Buyer', variant: 'mutuallyExclusive', value: {path: '/controls/buyer'},
         options: [{label: 'All', value: 'all'}, {label: 'Dept A', value: '0'}]},
        {id: 'board_control_spend', component: 'Slider', label: 'Min spend', min: 0, max: 100, value: {path: '/controls/spend'}},
      );
      (r.a2ui[2] as {updateDataModel: {value: unknown}}).updateDataModel.value = {buyer: ['all'], spend: 5};
      return r;
    };
    const agent = controlsAgent(seq => ({reply: inPlaceReply(seq, 'Spend 20', 'Buyer set to Dept A')}), withInputs);
    const app = await mount(agent.connector);
    const input = deepQuery(q(app, '.canvas a2ui-surface')!, 'input[type="range"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    input.focus();
    expect(isFocused(input)).toBe(true);

    dataModel(app).set('/controls/buyer', ['0']);
    await settle(app);
    expect(agent.sent.map(s => s.seq)).toEqual([1]);
    expect(surfaceText(app)).toContain('Spend 20'); // the in-place reply was applied

    expect(input.isConnected).toBe(true);
    expect(deepQuery(q(app, '.canvas a2ui-surface')!, 'input[type="range"]')).toBe(input);
    expect(isFocused(input)).toBe(true);
  });
});

/** querySelector that also looks inside open shadow roots. */
function deepQuery(root: Element | ShadowRoot, sel: string): Element | null {
  const hit = root.querySelector(sel);
  if (hit) return hit;
  for (const el of root.querySelectorAll('*')) {
    if (el.shadowRoot) {
      const found = deepQuery(el.shadowRoot, sel);
      if (found) return found;
    }
  }
  return null;
}

/** The element is the active element of its own root (document or shadow root). */
const isFocused = (el: Element) => (el.getRootNode() as Document | ShadowRoot).activeElement === el;

// --- Entity Boards (SPEC §18 slice 3) -------------------------------------------------------------------

const ENTITY_SID = 'turn-0-2-abc';

const entityReply = (): AskEvent => ({
  kind: 'final', contextId: 'ctx-board', prose: '',
  a2ui: [
    {version: 'v0.9', createSurface: {surfaceId: ENTITY_SID, catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: ENTITY_SID, components: [
      {id: 'root', component: 'Column', children: ['board_heading', 'board_controls', 'board_layout']},
      {id: 'board_heading', component: 'Text', text: 'Buyer · Dept A', variant: 'h2'},
      {id: 'board_controls', component: 'Text', text: 'controls'},
      {id: 'board_layout', component: 'Column', children: ['total']},
      {id: 'total', component: 'Text', text: 'Total 5'},
    ]}},
    {version: 'v0.9', updateDataModel: {surfaceId: ENTITY_SID, path: '/controls', value: {big: false}}},
  ],
  metadata: {
    [LEVEL_KEY]: {turn: 0, level: 2, label: 'Dept A', surfaceId: ENTITY_SID},
    [TAB_KEY]: {id: 't2', kind: 'board', title: 'Buyer · Dept A'},
    [LOG_KEY]: {kind: 'board', tag: 'NEW TAB · NO MODEL', text: 'Opened Buyer · Dept A'},
    [BOARD_KEY]: {id: 'b2', controls: [{name: 'big', kind: 'toggle'}]},
  },
});

describe('explorer-app Entity Boards', () => {
  it('turns a row click\'s running tab into the Board\'s tab and attaches its Controls', async () => {
    const sent: {name: string; context: unknown; surfaceId: string}[] = [];
    const base = controlsAgent(seq => ({reply: {kind: 'final', contextId: 'ctx-board', prose: '', a2ui: [],
      metadata: {[IN_PLACE_KEY]: {board: 'b2', seq}, [LOG_KEY]: {kind: 'controls', tag: 'IN PLACE · NO MODEL', text: 'Large only set to on'}}}}));
    const connector = async () => {
      const c = await base.connector();
      return {...c, async *act(action: UiAction, contextId?: string, viewing?: string) {
        sent.push({name: action.name, context: action.context, surfaceId: action.surfaceId});
        if (action.name === 'drill') {
          yield entityReply();
          return;
        }
        yield* c.act(action, contextId, viewing);
      }};
    };
    const app = await mount(connector);
    const surface = (app as unknown as {processor: MessageProcessor<ComponentApi>}).processor.model.surfacesMap.get(BOARD_SID)!;
    await surface.dispatchAction({event: {name: 'drill', context: {source: 'v1', row: 0}, userMessage: 'Dept A'}}, 'by_buyer');
    await settle(app);
    expect(texts(app, '.tab-strip .tab-title')).toEqual(['Explorer', 'Buyer · Dept A']);
    expect(logTexts(app)).toEqual(['Opened Explorer', 'Opened Buyer · Dept A']);
    expect(q(app, '.canvas .ask-instead')).toBeNull();
    expect(surfaceText(app)).toContain('Total 5');
    expect(texts(app, '.canvas .examples button')).toEqual([]); // examples belong to the Explorer only

    const entityModel = (app as unknown as {processor: MessageProcessor<ComponentApi>}).processor.model.surfacesMap.get(ENTITY_SID)!.dataModel;
    entityModel.set('/controls/big', true);
    await settle(app);
    expect(base.sent).toEqual([{seq: 1, values: {big: true}, surfaceId: ENTITY_SID, contextId: 'ctx-board'}]);
    expect(logTexts(app)).toEqual(['Opened Explorer', 'Opened Buyer · Dept A', 'Large only set to on']);
  });
});
