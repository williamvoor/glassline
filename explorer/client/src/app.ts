// <explorer-app>: the investigation view (explorer SPEC §16, §18). One tab strip at the top of the
// Canvas holds every step: the Explorer Board, each typed question, each Drill-down and each Ask.
// The Sidebar is the investigation log, one entry per tab, with the composer below. Surface IDs
// are `turn-<t>-<l>-<tag>`; Turns and Levels stay the server's (§15).
import {LitElement, html, nothing} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {unsafeHTML} from 'lit/directives/unsafe-html.js';
import {provide} from '@lit/context';
import {Context} from '@a2ui/lit/v0_9';
import '@a2ui/lit/v0_9';
import type {A2uiMessage, ComponentApi, MessageProcessor} from '@a2ui/web_core/v0_9';
import {connect, type AgentConnection, type AgentInfo, type AskEvent, type UiAction} from './a2a.js';
import {createProcessor} from './catalog.js';
import {renderSafeMarkdown} from './markdown.js';
import {closeTab, logOrder, openTab, selectTab, type TabState} from './tabs.js';

export const LEVEL_KEY = 'explorerLevel';
export const ACTION_FAILED_KEY = 'explorerActionFailed';
export const MODEL_KEY = 'explorerModel';
export const NO_BOARDS_KEY = 'explorerNoBoards';
export const TAB_KEY = 'explorerTab';
export const LOG_KEY = 'explorerLogEntry';
export const OPEN_BOARD = 'open_board';
const EXPLORER_KEY = 'explorer';

type TabKind = 'board' | 'question' | 'drill' | 'ask';

interface Tab extends TabState {
  id?: string; // the server's t<n>, from the reply's explorerTab; a failed tab never gets one
  kind: TabKind;
  title: string; // plain text: it can hold database text (a row label)
  log: {tag?: string; text: string}; // the log entry: explorerLogEntry once the reply arrives
  startedAt: number;
  steps: string[]; // the agent's status updates, in order
  elapsedMs?: number; // set when the reply ends, answered or failed
  error?: string;
  surfaceId?: string;
  proseHtml?: string; // sanitized by @a2ui/markdown-it, images removed; empty for a Drill-down
  model?: string | null; // the serving model; null for no model; undefined if the agent didn't say
  question?: string; // a question tab: what was asked
  action?: UiAction; // a Drill-down or Ask tab: what was sent, for Retry
  viewing?: string; // explorerViewing when it was sent; Retry sends it again
  confirm?: {label: string; action: UiAction}; // an Ask from this tab, waiting for the user's OK
}

/** The Explorer Board's load (SPEC §18): its tab exists only once it is `shown`. `none`: the
 * Dataset has no Boards; `failed`: shown as `none`, the examples alone. */
type BoardLoad = 'loading' | 'none' | 'failed' | 'shown';

interface ActionPayload extends UiAction {
  userMessage?: string;
  catalogId?: string;
}

type FinalEvent = Extract<AskEvent, {kind: 'final'}>;

export function surfaceIdOf(messages: Record<string, unknown>[]): string | undefined {
  for (const m of messages) {
    const create = m['createSurface'] as {surfaceId?: string} | undefined;
    if (create?.surfaceId) return create.surfaceId;
  }
  return undefined;
}

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

/** `explorerModel` from a final message: a name, null (no model), or undefined (not sent). */
function modelOf(metadata: Record<string, unknown> | undefined): string | null | undefined {
  if (!metadata || !(MODEL_KEY in metadata)) return undefined;
  const model = metadata[MODEL_KEY];
  return typeof model === 'string' ? model : null;
}

/** The tab ID, title and log entry a reply brings (SPEC §18), each only when well formed. */
function tabOf(metadata: Record<string, unknown> | undefined): Partial<Pick<Tab, 'id' | 'title' | 'log'>> {
  const tab = metadata?.[TAB_KEY] as {id?: unknown; title?: unknown} | undefined;
  const log = metadata?.[LOG_KEY] as {tag?: unknown; text?: unknown} | undefined;
  return {
    ...(typeof tab?.id === 'string' ? {id: tab.id} : {}),
    ...(typeof tab?.title === 'string' ? {title: tab.title} : {}),
    ...(typeof log?.text === 'string' ? {log: {text: log.text, ...(typeof log.tag === 'string' ? {tag: log.tag} : {})}} : {}),
  };
}

@customElement('explorer-app')
export class ExplorerApp extends LitElement {
  @provide({context: Context.markdown})
  markdownRenderer = (value: string, options?: unknown) => renderSafeMarkdown(value, options);

  @state() private tabs: Tab[] = [];
  @state() private selected?: string; // the key of the tab on the Canvas
  @state() private busy = false;
  @state() private info?: AgentInfo;
  @state() private connectError?: string;
  @state() private now = Date.now();
  @state() private board: BoardLoad = 'loading';
  @state() private logOpen = false; // narrow screens: the log is a collapsible panel
  private boardOpening?: Promise<void>; // a question waits for it, so both use one conversation
  private seq = 0;

  private processor: MessageProcessor<ComponentApi> = this.newProcessor();
  private connection?: Promise<AgentConnection>;
  private contextId?: string;
  private ticker?: ReturnType<typeof setInterval>;

  agentUrl = import.meta.env.VITE_AGENT_URL ?? 'http://127.0.0.1:10002';
  /** Replaceable in tests. */
  connector: () => Promise<AgentConnection> = () =>
    connect(this.agentUrl, this.processor.getRendererCapabilities({versions: ['v0.9']}));

  override createRenderRoot() {
    // Light DOM, so page styles (index.html) reach the A2UI components.
    return this;
  }

  override connectedCallback() {
    super.connectedCallback();
    void this.connectionOnce().catch(() => {}); // load the card early for the header and examples
    this.openBoard();
  }

  override disconnectedCallback() {
    super.disconnectedCallback();
    this.stopTicker();
  }

  private newProcessor(): MessageProcessor<ComponentApi> {
    const processor = createProcessor();
    processor.model.onAction.subscribe(payload => this.onAction(payload as ActionPayload));
    return processor;
  }

  private connectionOnce(): Promise<AgentConnection> {
    if (!this.connection) {
      this.connection = this.connector().then(
        c => {
          this.info = c.info;
          this.connectError = undefined;
          return c;
        },
        e => {
          this.connection = undefined; // try again next time
          this.connectError = e instanceof Error ? e.message : String(e);
          throw e;
        },
      );
    }
    return this.connection;
  }

  // tabs ----------------------------------------------------------------------------------------

  private tab(key: string | undefined): Tab | undefined {
    return key === undefined ? undefined : this.tabs.find(t => t.key === key);
  }

  private patch(key: string, patch: Partial<Tab>) {
    this.tabs = this.tabs.map(t => (t.key === key ? {...t, ...patch} : t));
  }

  /** Opens a tab after the selected one and selects it (SPEC §18). */
  private open(tab: Omit<Tab, 'key' | 'seq' | 'closed' | 'isNew'>): string {
    const key = `tab-${++this.seq}`;
    this.tabs = openTab(this.tabs, {...tab, key, seq: this.seq, closed: false, isNew: true}, this.selected);
    this.select(key);
    return key;
  }

  private select(key: string) {
    this.tabs = selectTab(this.tabs, this.selected, key); // from memory, no agent call; reopens a closed tab
    this.selected = key;
    this.logOpen = false;
    void this.updateComplete.then(() => this.querySelector('.canvas')?.scrollTo?.({top: 0}));
  }

  private close(key: string) {
    const {tabs, selected} = closeTab(this.tabs, key, this.selected);
    this.tabs = tabs;
    this.selected = selected;
  }

  /** `explorerViewing`: the selected tab's server ID (SPEC §18). */
  private viewing(): string | undefined {
    return this.tab(this.selected)?.id;
  }

  private startTicker() {
    this.now = Date.now();
    this.ticker ??= setInterval(() => (this.now = Date.now()), 200);
  }

  private stopTicker() {
    clearInterval(this.ticker);
    this.ticker = undefined;
  }

  // the Explorer Board ----------------------------------------------------------------------------

  /** Sends `open_board` for the Explorer when a conversation starts (SPEC §18). Its reply starts
   * the conversation, so the first question waits for it and uses the same contextId. */
  private openBoard() {
    const processor = this.processor;
    this.board = 'loading';
    const action: UiAction = {
      name: OPEN_BOARD, surfaceId: '', sourceComponentId: '', timestamp: new Date().toISOString(), context: {board: 'explorer'},
    };
    this.boardOpening = (async () => {
      try {
        const agent = await this.connectionOnce();
        for await (const event of agent.act(action, this.contextId)) {
          if (processor !== this.processor) return; // a new conversation replaced this one
          if (event.kind !== 'final') continue;
          const surfaceId = surfaceIdOf(event.a2ui);
          if (event.metadata?.[ACTION_FAILED_KEY] || !surfaceId) {
            this.board = event.metadata?.[NO_BOARDS_KEY] ? 'none' : 'failed';
            return;
          }
          this.contextId = event.contextId;
          processor.processMessages(event.a2ui as unknown as A2uiMessage[]);
          this.addExplorer(event, surfaceId);
          this.board = 'shown';
        }
      } catch {
        if (processor === this.processor) this.board = 'failed';
      }
    })();
  }

  /** The Explorer is the first tab and can't be closed. A question asked while it loaded keeps
   * the selection. */
  private addExplorer(event: FinalEvent, surfaceId: string) {
    const meta = tabOf(event.metadata);
    const explorer: Tab = {
      key: EXPLORER_KEY, seq: 0, closed: false, isNew: false, fixed: true, kind: 'board', title: meta.title ?? 'Explorer',
      log: meta.log ?? {text: 'Explorer'}, id: meta.id, startedAt: Date.now(), elapsedMs: 0, steps: [], surfaceId,
      model: modelOf(event.metadata),
    };
    this.tabs = [explorer, ...this.tabs];
    this.selected ??= EXPLORER_KEY;
  }

  // typed questions -------------------------------------------------------------------------------

  async ask(question: string) {
    if (this.busy) return;
    this.busy = true; // the tab shows at once, before the Board's reply (SPEC §18 slice 1b)
    const viewing = this.viewing(); // what was on screen when the question was typed
    const key = this.open({kind: 'question', title: question, log: {text: question}, question, viewing, running: true,
                           startedAt: Date.now(), steps: []});
    this.startTicker();
    await this.boardOpening; // the Board's reply carries the conversation's contextId
    await this.runQuestion(key);
  }

  private async retry(key: string) {
    if (this.busy) return;
    this.patch(key, {running: true, steps: [], startedAt: Date.now(), elapsedMs: undefined, error: undefined});
    await this.runQuestion(key);
  }

  private async runQuestion(key: string) {
    const tab = this.tab(key);
    if (!tab?.question) return;
    await this.stream(key, agent => agent.askQuestion(tab.question!, this.contextId, tab.viewing), async event => {
      this.patch(key, {
        ...tabOf(event.metadata), surfaceId: surfaceIdOf(event.a2ui), proseHtml: await renderSafeMarkdown(event.prose),
        model: modelOf(event.metadata),
      });
    });
  }

  // Actions ---------------------------------------------------------------------------------------

  /** The text of a Button's Text child (a Follow-up or the Drill-down's Ask button). */
  private buttonText(surfaceId: string, componentId: string): string | undefined {
    const components = this.processor.model.surfacesMap.get(surfaceId)?.componentsModel;
    const child = components?.get(componentId)?.properties?.['child'];
    const text = typeof child === 'string' ? components?.get(child)?.properties?.['text'] : undefined;
    return typeof text === 'string' ? text : undefined;
  }

  private onAction(payload: ActionPayload) {
    if (this.busy) return; // clicks are disabled while anything runs
    const from = this.tabs.find(t => t.surfaceId === payload.surfaceId);
    if (!from) return;
    const {name, surfaceId, sourceComponentId, timestamp, context} = payload;
    const action: UiAction = {name, surfaceId, sourceComponentId, timestamp, context}; // IDs only
    const label = payload.userMessage || this.buttonText(surfaceId, sourceComponentId) || 'this';
    if (name === 'drill') {
      void this.runAction(from.key, action, label);
    } else {
      this.patch(from.key, {confirm: {label, action}});
      void this.updateComplete.then(() => (this.querySelector('.canvas .tab-view .confirm .ok') as HTMLElement | null)?.focus());
    }
  }

  private confirmAsk(key: string) {
    const confirm = this.tab(key)?.confirm;
    if (!confirm || this.busy) return;
    this.patch(key, {confirm: undefined});
    void this.runAction(key, confirm.action, confirm.label);
  }

  private cancelAsk(key: string) {
    this.patch(key, {confirm: undefined});
  }

  /** Ask the agent instead, after a failed Drill-down (the same row, confirmed). */
  private askInstead(key: string) {
    const tab = this.tab(key);
    if (!tab?.action) return;
    this.patch(key, {confirm: {label: tab.title, action: {...tab.action, name: 'ask', timestamp: new Date().toISOString()}}});
  }

  /** A Drill-down or a confirmed Ask: a new tab after the one clicked, running until its reply. */
  private async runAction(fromKey: string, action: UiAction, label: string) {
    if (this.busy || !this.contextId) return;
    if (this.selected !== fromKey) this.select(fromKey);
    const viewing = this.viewing();
    const key = this.open({kind: action.name === 'drill' ? 'drill' : 'ask', title: label, log: {text: label}, action, viewing,
                           running: true, startedAt: Date.now(), steps: []});
    await this.sendAction(key);
  }

  private retryAction(key: string) {
    const tab = this.tab(key);
    if (!tab?.action || this.busy) return;
    this.patch(key, {action: {...tab.action, timestamp: new Date().toISOString()}, running: true, steps: [],
                     startedAt: Date.now(), elapsedMs: undefined, error: undefined});
    void this.sendAction(key);
  }

  private async sendAction(key: string) {
    const tab = this.tab(key);
    const contextId = this.contextId;
    if (!tab?.action || !contextId) return;
    await this.stream(key, agent => agent.act(tab.action!, contextId, tab.viewing), async event => {
      if (!event.metadata?.[LEVEL_KEY] || event.metadata?.[ACTION_FAILED_KEY]) {
        this.patch(key, {error: event.prose});
        return;
      }
      this.patch(key, {
        ...tabOf(event.metadata), surfaceId: surfaceIdOf(event.a2ui), proseHtml: await renderSafeMarkdown(event.prose),
        model: modelOf(event.metadata),
      });
    });
  }

  /** Runs one request for the tab `key`: its status updates, then its final message. */
  private async stream(key: string, open: (agent: AgentConnection) => AsyncGenerator<AskEvent, void, undefined>,
                       final: (event: FinalEvent) => Promise<void>) {
    const processor = this.processor; // a new conversation replaces it mid-run
    this.busy = true;
    this.startTicker();
    const startedAt = this.tab(key)?.startedAt ?? Date.now();
    try {
      const agent = await this.connectionOnce();
      for await (const event of open(agent)) {
        if (processor !== this.processor) return; // the user started a new conversation
        if (event.kind === 'status') {
          this.patch(key, {steps: [...(this.tab(key)?.steps ?? []), event.text]});
          continue;
        }
        this.contextId = event.contextId;
        if (event.a2ui.length) processor.processMessages(event.a2ui as unknown as A2uiMessage[]);
        await final(event);
      }
    } catch (e) {
      if (processor !== this.processor) return;
      this.patch(key, {error: e instanceof Error ? e.message : String(e)});
    } finally {
      if (processor === this.processor) {
        this.patch(key, {running: false, elapsedMs: Date.now() - startedAt});
        this.busy = false;
        this.stopTicker();
      }
    }
  }

  private newConversation() {
    this.tabs = [];
    this.selected = undefined;
    this.contextId = undefined;
    this.processor = this.newProcessor();
    this.busy = false;
    this.stopTicker();
    this.openBoard();
    this.querySelector('textarea')?.focus();
  }

  private send(box: HTMLTextAreaElement) {
    const question = box.value.trim();
    if (!question || this.busy) return;
    box.value = '';
    void this.ask(question);
  }

  private onKeydown(e: KeyboardEvent) {
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    this.send(e.target as HTMLTextAreaElement);
  }

  private onSubmit(e: Event) {
    e.preventDefault();
    this.send((e.target as HTMLFormElement).querySelector('textarea')!);
  }

  // rendering -----------------------------------------------------------------------------------

  private renderHeader() {
    return html`<header class="topbar">
      <div class="title">
        <h1>Explorer</h1>
        ${this.info?.description ? html`<span class="dataset">${this.info.description}</span>` : nothing}
      </div>
      <button class="new-conversation" type="button" @click=${this.newConversation} ?disabled=${!this.tabs.some(t => !t.fixed)}>
        New conversation
      </button>
    </header>`;
  }

  private renderBanner() {
    if (!this.connectError) return nothing;
    return html`<div class="banner" role="alert">
      Cannot reach the agent at <code>${this.agentUrl}</code> (${this.connectError}).
      <button type="button" @click=${() => void this.connectionOnce().catch(() => {})}>Retry</button>
    </div>`;
  }

  private renderEmpty() {
    const examples = this.info?.examples ?? [];
    return html`<div class="empty">
      <h2>Ask a question in plain English.</h2>
      <p>Every number comes from the database, and each chart and table shows its SQL.</p>
      ${examples.length
        ? html`<p class="label">Try</p>
            <div class="examples">
              ${examples.map(x => html`<button type="button" ?disabled=${this.busy} @click=${() => void this.ask(x)}>${x}</button>`)}
            </div>`
        : nothing}
    </div>`;
  }

  private renderStatus(steps: string[], startedAt: number) {
    return html`<div class="status" aria-live="polite">
      <span class="spinner" aria-hidden="true"></span>
      ${steps.at(-1) ?? 'Thinking…'} <span class="elapsed">${seconds(this.now - startedAt)}</span>
    </div>`;
  }

  // Sidebar: the investigation log --------------------------------------------------------------

  private renderEntryState(tab: Tab) {
    if (tab.running) {
      return html`<span class="spinner" aria-hidden="true"></span><span class="elapsed">${seconds(this.now - tab.startedAt)}</span>`;
    }
    return tab.error ? html`Failed` : nothing;
  }

  private renderLog() {
    const entries = logOrder(this.tabs);
    return html`<button type="button" class="log-head label" aria-expanded=${this.logOpen ? 'true' : 'false'}
        @click=${() => (this.logOpen = !this.logOpen)}>
        <span>Investigation</span><span class="count">${entries.length}</span>
      </button>
      ${entries.length
        ? html`<ol class=${this.logOpen ? 'log open' : 'log'}>
            ${entries.map(t => html`<li>
              <button
                type="button"
                class=${t.key === this.selected ? 'log-entry selected' : 'log-entry'}
                aria-current=${t.key === this.selected ? 'page' : 'false'}
                @click=${() => this.select(t.key)}
              >
                <span class="log-tag">${t.log.tag ?? 'NEW TAB'}</span>
                <span class="log-text">${t.log.text}</span>
                <span class="entry-state">${this.renderEntryState(t)}</span>
              </button>
            </li>`)}
          </ol>`
        : html`<div class="log-spacer"></div>`}`;
  }

  private renderSidebar() {
    return html`<aside class="sidebar" aria-label="Investigation">
      ${this.renderLog()}
      <form class="composer" @submit=${this.onSubmit}>
        <label class="label" for="composer-box">Ask</label>
        <textarea
          id="composer-box"
          rows="3"
          placeholder="Ask a question about the data (Enter to send, Shift+Enter for a new line)"
          @keydown=${this.onKeydown}
        ></textarea>
        <button type="submit" ?disabled=${this.busy}>${this.busy ? 'Working…' : 'Ask'}</button>
      </form>
    </aside>`;
  }

  // Canvas ----------------------------------------------------------------------------------------

  private renderTabStrip() {
    const open = this.tabs.filter(t => !t.closed);
    if (!open.length) return nothing;
    return html`<nav class="tab-strip" role="tablist" aria-label="Tabs">
      ${open.map(t => {
        const selected = t.key === this.selected;
        return html`<div class=${selected ? 'tab selected' : 'tab'}>
          <button type="button" class="tab-select" role="tab" aria-selected=${selected ? 'true' : 'false'} title=${t.title}
            @click=${() => this.select(t.key)}>
            ${t.running ? html`<span class="spinner" aria-hidden="true"></span>` : nothing}
            <span class="tab-title">${t.title}</span>
            ${t.isNew ? html`<span class="new-tag">NEW</span>` : nothing}
          </button>
          ${t.fixed
            ? nothing
            : html`<button type="button" class="tab-close" aria-label=${`Close ${t.title}`} ?disabled=${!!t.running}
                @click=${() => this.close(t.key)}>×</button>`}
        </div>`;
      })}
    </nav>`;
  }

  private renderActionBars(tab: Tab) {
    return html`${tab.confirm
      ? html`<div
          class="confirm"
          role="dialog"
          aria-label="Ask the agent"
          @keydown=${(e: KeyboardEvent) => e.key === 'Escape' && this.cancelAsk(tab.key)}
        >
          <span>Ask the agent: <strong>${tab.confirm.label}</strong>. This takes a few seconds.</span>
          <button type="button" class="ok" ?disabled=${this.busy} @click=${() => this.confirmAsk(tab.key)}>Ask</button>
          <button type="button" class="cancel" @click=${() => this.cancelAsk(tab.key)}>Cancel</button>
        </div>`
      : nothing}
    ${tab.error
      ? html`<div class="error" role="alert">
          <span>${tab.error}</span>
          <button class="retry" type="button" ?disabled=${this.busy}
            @click=${() => (tab.kind === 'question' ? void this.retry(tab.key) : this.retryAction(tab.key))}>Retry</button>
          ${tab.kind === 'drill'
            ? html`<button class="ask-instead" type="button" ?disabled=${this.busy} @click=${() => this.askInstead(tab.key)}>Ask the agent instead</button>`
            : nothing}
        </div>`
      : nothing}`;
  }

  /** The status line under a tab: time, steps and the serving model (SPEC §16). */
  private renderMeta(tab: Tab) {
    if (tab.running || tab.elapsedMs === undefined || tab.kind === 'board') return nothing;
    const parts = [
      ...(tab.steps.length ? [`${tab.steps.length} step${tab.steps.length === 1 ? '' : 's'}`] : []),
      ...(tab.model === null ? ['No model · database only'] : tab.model ? [tab.model] : []),
    ];
    return html`<details class="meta">
      <summary><span class="elapsed">${tab.error ? 'Failed after' : 'Answered in'} ${seconds(tab.elapsedMs)}</span>${parts.map(
        p => html` · <span>${p}</span>`,
      )}</summary>
      ${tab.steps.length ? html`<ol class="steps">${tab.steps.map(s => html`<li>${s}</li>`)}</ol>` : nothing}
    </details>`;
  }

  private renderTab(tab: Tab) {
    const surface = tab.surfaceId ? this.processor.model.surfacesMap.get(tab.surfaceId) : undefined;
    const heading = tab.kind === 'question' || tab.kind === 'ask'; // a Board and a Drill-down title themselves
    const examples = tab.kind === 'board' && !this.tabs.some(t => t.kind === 'question');
    return html`<section class="tab-view" role="tabpanel">
      ${heading ? html`<h2 class="question">${tab.title}</h2>` : nothing}
      ${tab.running ? this.renderStatus(tab.steps, tab.startedAt) : nothing}
      ${this.renderActionBars(tab)}
      <div class=${this.busy ? 'level-view inert' : 'level-view'}>
        ${tab.proseHtml ? html`<div class="prose">${unsafeHTML(tab.proseHtml)}</div>` : nothing}
        ${surface ? html`<a2ui-surface .surface=${surface}></a2ui-surface>` : nothing}
      </div>
      ${this.renderMeta(tab)}
    </section>
    ${examples ? this.renderEmpty() : nothing}`;
  }

  override render() {
    const tab = this.tab(this.selected);
    return html`
      ${this.renderHeader()}
      <div class="shell">
        ${this.renderSidebar()}
        <main class="canvas">
          ${this.renderBanner()} ${this.renderTabStrip()} ${tab && !tab.closed ? this.renderTab(tab) : this.renderEmpty()}
        </main>
      </div>
    `;
  }
}
