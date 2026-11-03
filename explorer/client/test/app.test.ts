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
  const app = document.createElement('explorer-app') as ExplorerApp;
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
    expect(q(app, 'header')!.textContent).toContain('Demo contracts');
    expect(q(app, '.canvas .examples')).not.toBeNull();
    expect(texts(app, '.examples button')).toEqual(['Top suppliers?', 'Spend by year?']);
  });

  it('asks an example question when it is clicked, and hides the examples', async () => {
    const agent = fakeAgent(() => answer('**Supplier A** received the most.'));
    const app = await mount(agent.connector);
    (q(app, '.examples button') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.calls).toEqual([{text: 'Top suppliers?', contextId: undefined}]);
    expect(q(app, '.examples')).toBeNull();
    expect(q(app, '.turn .question')!.textContent).toContain('Top suppliers?');
    expect(q(app, '.turn .prose strong')!.textContent).toBe('Supplier A');
    expect(q(app, '.turn .elapsed')!.textContent).toMatch(/^Answered in \d+\.\d s$/);
    // No explorerModel from the agent: the status line names no model.
    expect(q(app, '.turn .meta summary')!.textContent!.replace(/\s+/g, ' ').trim()).toMatch(/^Answered in \d+\.\d s · 1 step$/);
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
    expect(app.querySelectorAll('.turn')).toHaveLength(0);
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
    expect(q(app, '.turn .error')!.textContent).toContain('Turn timed out');
    expect(q(app, '.sidebar .turn-entry .entry-state')!.textContent).toContain('Failed');
    expect(q(app, '.turn .meta summary')!.textContent!.replace(/\s+/g, ' ').trim()).toMatch(/^Failed after \d+\.\d s$/);

    fail = false;
    (q(app, '.turn button.retry') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.calls.map(c => c.text)).toEqual(['slow one', 'slow one']);
    expect(app.querySelectorAll('.turn')).toHaveLength(1);
    expect(q(app, '.turn .error')).toBeNull();
    expect(q(app, '.turn .prose')!.textContent).toContain('recovered');
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

// --- The Explorer Board on load (SPEC §18, slice 1a) -----------------------------------------------

import {EXPLORER_CATALOG_ID} from '../src/catalog.js';
import {ACTION_FAILED_KEY, LEVEL_KEY, NO_BOARDS_KEY, OPEN_BOARD} from '../src/app.js';
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
  metadata: {[LEVEL_KEY]: {turn: 0, level: 1, label: 'Explorer', surfaceId: BOARD_SID}},
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
  it('opens the Explorer on load and shows it above the examples; the first question joins its conversation', async () => {
    const agent = boardAgent(() => boardReply());
    const app = await mount(agent.connector);
    expect(agent.acts.map(a => [a.action.name, a.action.context, a.contextId])).toEqual([[OPEN_BOARD, {board: 'explorer'}, undefined]]);
    const board = q(app, '.canvas .board a2ui-surface');
    expect(board).not.toBeNull();
    expect(board!.textContent).toContain('Explorer');
    // The examples move below it.
    expect(board!.closest('.board')!.compareDocumentPosition(q(app, '.canvas .examples')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(q(app, '.turn-list')).toBeNull(); // Turn 0 has no Sidebar entry
    (q(app, '.examples button') as HTMLButtonElement).click();
    await settle(app);
    expect(agent.calls).toEqual([{text: 'Top suppliers?', contextId: 'ctx-board'}]);
    expect(q(app, '.canvas .board')).toBeNull();
  });

  it('a question asked before the Board arrives waits for it and uses its conversation', async () => {
    let open!: () => void;
    const agent = boardAgent(() => boardReply(), new Promise<void>(r => (open = r)));
    const app = await mount(agent.connector);
    void app.ask('Early?');
    await settle(app);
    expect(agent.calls).toEqual([]);
    open();
    await settle(app);
    expect(agent.calls).toEqual([{text: 'Early?', contextId: 'ctx-board'}]);
  });

  it('without Boards shows the empty state as before', async () => {
    const agent = boardAgent(() => ({kind: 'final', contextId: 'ctx-x', prose: 'none', a2ui: [],
      metadata: {[ACTION_FAILED_KEY]: true, [NO_BOARDS_KEY]: true}}));
    const app = await mount(agent.connector);
    expect(q(app, '.canvas .board')).toBeNull();
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
    expect(q(app, '.canvas .board a2ui-surface')).not.toBeNull();
  });
});
