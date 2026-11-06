// Pins web_core's markdown rule that the agent copies (issue 9): h1–h5 and caption Text, and Tabs
// titles, render as plain text; body Text renders markdown. The agent escapes filled database
// values only for markdown (`macros.PLAIN_TEXT_VARIANTS`), so if an SDK bump changes this rule,
// titles would show backslashes or render markdown. This test fails first.
import {afterEach, expect, it} from 'vitest';
import type {AgentConnection, AskEvent} from '../src/a2a.js';
import '../src/app.js';
import {ACTION_FAILED_KEY, LEVEL_KEY, LOG_KEY, NO_BOARDS_KEY, OPEN_BOARD, TAB_KEY, type ExplorerApp} from '../src/app.js';
import {EXPLORER_CATALOG_ID} from '../src/catalog.js';
import {oneWorkspace} from './one-workspace.js';

const PLAIN = ['h1', 'h2', 'h3', 'h4', 'h5', 'caption'];
const SID = 'turn-1-1-x';

const reply = (): AskEvent => ({
  kind: 'final', contextId: 'c', prose: '',
  a2ui: [
    {version: 'v0.9', createSurface: {surfaceId: SID, catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: SID, components: [
      {id: 'root', component: 'Column', children: [...PLAIN.map(v => `t_${v}`), 't_body', 'tabs']},
      ...PLAIN.map(v => ({id: `t_${v}`, component: 'Text', text: '*x*', variant: v})),
      {id: 't_body', component: 'Text', text: '*x*', variant: 'body'},
      {id: 'tabs', component: 'Tabs', tabs: [{title: '*x*', child: 'tab_child'}]},
      {id: 'tab_child', component: 'Text', text: 'child', variant: 'caption'},
    ]}},
  ],
  metadata: {
    [LEVEL_KEY]: {turn: 1, level: 1, label: 'q', surfaceId: SID},
    [TAB_KEY]: {id: 't1', kind: 'question', title: 'q'},
    [LOG_KEY]: {kind: 'question', tag: 'NEW TAB', text: 'q'},
  },
});

const connection: AgentConnection = {
  info: {description: 'Demo', examples: []},
  async *askQuestion() {
    yield reply();
  },
  async *act(action) {
    expect(action.name).toBe(OPEN_BOARD);
    yield {kind: 'final', contextId: 'c', prose: 'No Boards', a2ui: [], metadata: {[ACTION_FAILED_KEY]: true, [NO_BOARDS_KEY]: true}};
  },
};

afterEach(() => {
  document.body.innerHTML = '';
});

it('renders h1–h5, caption and Tabs titles as plain text, and body Text as markdown', async () => {
  const app = document.createElement('explorer-app') as ExplorerApp;
  app.workspaceApi = oneWorkspace();
  app.connector = async () => connection;
  document.body.appendChild(app);
  for (let i = 0; i < 12; i++) { // the Workspace opens first (ADR 0048)
    await new Promise(r => setTimeout(r, 5));
    await app.updateComplete;
  }
  await app.ask('q');
  for (let i = 0; i < 12; i++) {
    await new Promise(r => setTimeout(r, 5));
    await app.updateComplete;
  }
  const surface = app.querySelector('.canvas a2ui-surface')!;
  const texts = [...surface.querySelectorAll('.a2ui-text')];
  const byVariant = (v: string) => texts.find(t => t.classList.contains(`a2ui-text-${v}`) && t.textContent!.includes('x'))!;
  // Markdown would turn `*x*` into an <em>x</em>; plain text keeps the asterisks. (A caption's own
  // wrapper is an <em>, holding the literal text.)
  for (const v of PLAIN) expect(byVariant(v).textContent!.trim(), v).toBe('*x*');
  expect(byVariant('body').textContent!.trim()).toBe('x');
  expect(byVariant('body').querySelector('em')!.textContent).toBe('x');
  const tabTitle = [...surface.querySelectorAll('*')].find(e => e.childElementCount === 0 && e.textContent!.trim() === '*x*'
    && !e.closest('.a2ui-text'));
  expect(tabTitle, 'Tabs title rendered as plain text').toBeDefined();
});
