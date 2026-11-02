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
    async *ask(text, contextId) {
      calls.push({text, contextId});
      const result = script(text);
      if (result instanceof Error) throw result;
      yield* result;
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
