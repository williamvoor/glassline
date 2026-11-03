// <explorer-app>: the chat view. A typed question starts a Turn; clicking a Visual adds a Level to
// its Turn, shown in place with a Breadcrumb back (explorer SPEC §15). Surface IDs are
// `turn-<t>-<l>-<tag>`, the tag random per conversation. Layout (SPEC §16): a top bar, a Sidebar
// with the Turns and the composer, and a Canvas showing the selected Turn.
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

export const LEVEL_KEY = 'explorerLevel';
export const ACTION_FAILED_KEY = 'explorerActionFailed';
export const MODEL_KEY = 'explorerModel';
export const NO_BOARDS_KEY = 'explorerNoBoards';
export const OPEN_BOARD = 'open_board';

interface Level {
  surfaceId?: string;
  label: string;
  proseHtml?: string; // sanitized by @a2ui/markdown-it, images removed; empty for a Drill-down
  elapsedMs: number;
  steps: string[]; // the agent's status updates for this Level, in order
  model?: string | null; // the serving model; null for a Drill-down; undefined if the agent didn't say
}

interface Running {
  label: string;
  action: UiAction;
  steps: string[];
  startedAt: number;
}

interface Turn {
  question: string;
  steps: string[]; // the agent's status updates for the typed question, in order (until Level 1 arrives)
  startedAt: number;
  viewing: string[]; // explorerViewing when the question was typed; Retry sends it again
  elapsedMs?: number; // set when the question's answer ends, answered or failed
  error?: string;
  levels: Level[]; // Level 1 (Overview) first
  current: number; // index of the Level shown
  confirm?: {label: string; action: UiAction}; // an Ask waiting for the user's OK
  running?: Running; // an Action in flight
  actionError?: {message: string; label: string; action: UiAction};
}

/** The Explorer Board (SPEC §18): Turn 0, shown before the first question. Not clickable yet
 * (slice 1a). `none`: the Dataset has no Boards; `failed`: shown as `none`, the examples alone. */
type BoardView = {state: 'loading'} | {state: 'none'} | {state: 'failed'} | {state: 'shown'; surfaceId: string};

interface ActionPayload extends UiAction {
  userMessage?: string;
  catalogId?: string;
}

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

@customElement('explorer-app')
export class ExplorerApp extends LitElement {
  @provide({context: Context.markdown})
  markdownRenderer = (value: string, options?: unknown) => renderSafeMarkdown(value, options);

  @state() private turns: Turn[] = [];
  @state() private selected = 0; // the Turn on the Canvas
  @state() private busy = false;
  @state() private info?: AgentInfo;
  @state() private connectError?: string;
  @state() private now = Date.now();
  @state() private board: BoardView = {state: 'loading'};
  private boardOpening?: Promise<void>; // a question waits for it, so both use one conversation

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

  /** Sends `open_board` for the Explorer when a conversation starts (SPEC §18). Its reply starts
   * the conversation, so the first question waits for it and uses the same contextId. */
  private openBoard() {
    const processor = this.processor;
    this.board = {state: 'loading'};
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
            this.board = {state: event.metadata?.[NO_BOARDS_KEY] ? 'none' : 'failed'};
            return;
          }
          this.contextId = event.contextId;
          processor.processMessages(event.a2ui as unknown as A2uiMessage[]);
          this.board = {state: 'shown', surfaceId};
        }
      } catch {
        if (processor === this.processor) this.board = {state: 'failed'};
      }
    })();
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

  private patch(index: number, patch: Partial<Turn>) {
    this.turns = this.turns.map((t, i) => (i === index ? {...t, ...patch} : t));
  }

  private startTicker() {
    this.now = Date.now();
    this.ticker ??= setInterval(() => (this.now = Date.now()), 200);
  }

  private stopTicker() {
    clearInterval(this.ticker);
    this.ticker = undefined;
  }

  /** `explorerViewing`: the selected Turn's shown Level when it isn't that Turn's latest. Other
   * Turns aren't on screen (SPEC §16). */
  private viewing(): string[] {
    const t = this.turns[this.selected];
    if (!t || t.levels.length < 2 || t.current === t.levels.length - 1) return [];
    const surfaceId = t.levels[t.current]?.surfaceId;
    return surfaceId ? [surfaceId] : [];
  }

  private select(index: number) {
    this.selected = index; // from memory, no agent call
    void this.updateComplete.then(() => this.querySelector('.canvas')?.scrollTo?.({top: 0}));
  }

  // typed questions ----------------------------------------------------------------------------

  async ask(question: string) {
    if (this.busy) return;
    await this.boardOpening; // the Board's reply carries the conversation's contextId
    if (this.busy) return;
    const viewing = this.viewing(); // what was on screen when the question was typed
    this.turns = [...this.turns, {question, viewing, steps: [], startedAt: Date.now(), levels: [], current: 0}];
    this.select(this.turns.length - 1);
    await this.runQuestion(this.turns.length - 1);
  }

  private async retry(index: number) {
    if (this.busy) return;
    const {question} = this.turns[index]!;
    this.patch(index, {question, steps: [], startedAt: Date.now(), elapsedMs: undefined, error: undefined});
    await this.runQuestion(index);
  }

  private async runQuestion(index: number) {
    const turn = this.turns[index]!;
    await this.stream(index, agent => agent.askQuestion(turn.question, this.contextId, turn.viewing), {
      status: text => this.patch(index, {steps: [...this.turns[index]!.steps, text]}),
      final: async event => {
        const elapsedMs = Date.now() - turn.startedAt;
        const level: Level = {
          surfaceId: surfaceIdOf(event.a2ui), label: 'Overview', proseHtml: await renderSafeMarkdown(event.prose),
          elapsedMs, steps: this.turns[index]!.steps, model: modelOf(event.metadata),
        };
        this.patch(index, {elapsedMs, levels: [level], current: 0});
      },
      fail: message => this.patch(index, {elapsedMs: Date.now() - turn.startedAt, error: message}),
    });
  }

  // Actions -------------------------------------------------------------------------------------

  private turnOf(surfaceId: string): number {
    return this.turns.findIndex(t => t.levels.some(l => l.surfaceId === surfaceId));
  }

  /** The text of a Button's Text child (a Follow-up or the Drill-down's Ask button). */
  private buttonText(surfaceId: string, componentId: string): string | undefined {
    const components = this.processor.model.surfacesMap.get(surfaceId)?.componentsModel;
    const child = components?.get(componentId)?.properties?.['child'];
    const text = typeof child === 'string' ? components?.get(child)?.properties?.['text'] : undefined;
    return typeof text === 'string' ? text : undefined;
  }

  private onAction(payload: ActionPayload) {
    if (this.busy) return; // clicks are disabled while anything runs
    const index = this.turnOf(payload.surfaceId);
    if (index < 0) return;
    const {name, surfaceId, sourceComponentId, timestamp, context} = payload;
    const action: UiAction = {name, surfaceId, sourceComponentId, timestamp, context}; // IDs only
    const label = payload.userMessage || this.buttonText(surfaceId, sourceComponentId) || 'this';
    if (name === 'drill') {
      void this.runAction(index, action, label);
    } else {
      this.patch(index, {confirm: {label, action}, actionError: undefined});
      void this.updateComplete.then(() => (this.querySelector('.canvas .turn .confirm .ok') as HTMLElement | null)?.focus());
    }
  }

  private confirmAsk(index: number) {
    const confirm = this.turns[index]?.confirm;
    if (!confirm || this.busy) return;
    this.patch(index, {confirm: undefined});
    void this.runAction(index, confirm.action, confirm.label);
  }

  private cancelAsk(index: number) {
    this.patch(index, {confirm: undefined});
  }

  /** Ask the agent instead, after a failed Drill-down (the same row, confirmed). */
  private askInstead(index: number) {
    const failed = this.turns[index]?.actionError;
    if (!failed) return;
    const action = {...failed.action, name: 'ask', timestamp: new Date().toISOString()};
    this.patch(index, {actionError: undefined, confirm: {label: failed.label, action}});
  }

  private async runAction(index: number, action: UiAction, label: string) {
    if (this.busy || !this.contextId) return;
    const contextId = this.contextId;
    const from = this.turns[index]!.current; // the Level clicked from
    this.patch(index, {running: {label, action, steps: [], startedAt: Date.now()}, actionError: undefined, confirm: undefined});
    await this.stream(index, agent => agent.act(action, contextId, this.viewing()), {
      status: text => {
        const running = this.turns[index]!.running;
        if (running) this.patch(index, {running: {...running, steps: [...running.steps, text]}});
      },
      final: async event => {
        const level = event.metadata?.[LEVEL_KEY] as {label?: string} | undefined;
        if (!level || event.metadata?.[ACTION_FAILED_KEY]) {
          this.patch(index, {running: undefined, actionError: {message: event.prose, label, action}});
          return;
        }
        const turn = this.turns[index]!;
        const running = turn.running;
        const levels: Level[] = [
          ...turn.levels.slice(0, from + 1), // a new Action from an earlier Level drops the ones after it
          {
            surfaceId: surfaceIdOf(event.a2ui), label: level.label ?? label, proseHtml: await renderSafeMarkdown(event.prose),
            elapsedMs: Date.now() - (running?.startedAt ?? Date.now()), steps: running?.steps ?? [], model: modelOf(event.metadata),
          },
        ];
        this.patch(index, {running: undefined, levels, current: levels.length - 1});
      },
      fail: message => this.patch(index, {running: undefined, actionError: {message, label, action}}),
    });
  }

  private retryAction(index: number) {
    const failed = this.turns[index]?.actionError;
    if (!failed || this.busy) return;
    const action = {...failed.action, timestamp: new Date().toISOString()};
    void this.runAction(index, action, failed.label);
  }

  private showLevel(index: number, level: number) {
    if (this.turns[index]?.running) return;
    this.patch(index, {current: level, confirm: undefined, actionError: undefined}); // from memory, no agent call
  }

  private async stream(
    index: number,
    open: (agent: AgentConnection) => AsyncGenerator<AskEvent, void, undefined>,
    on: {status(text: string): void; final(event: Extract<AskEvent, {kind: 'final'}>): Promise<void>; fail(message: string): void},
  ) {
    const processor = this.processor; // a new conversation replaces it mid-run
    this.busy = true;
    this.startTicker();
    if (index === this.selected) {
      void this.updateComplete.then(() => this.querySelector('.canvas .turn')?.scrollIntoView?.({behavior: 'smooth', block: 'start'}));
    }
    try {
      const agent = await this.connectionOnce();
      for await (const event of open(agent)) {
        if (processor !== this.processor) return; // the user started a new conversation
        if (event.kind === 'status') {
          on.status(event.text);
          continue;
        }
        this.contextId = event.contextId;
        if (event.a2ui.length) processor.processMessages(event.a2ui as unknown as A2uiMessage[]);
        await on.final(event);
      }
    } catch (e) {
      if (processor !== this.processor) return;
      on.fail(e instanceof Error ? e.message : String(e));
    } finally {
      if (processor === this.processor) {
        this.busy = false;
        this.stopTicker();
      }
    }
  }

  private newConversation() {
    this.turns = [];
    this.selected = 0;
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
      <button class="new-conversation" type="button" @click=${this.newConversation} ?disabled=${!this.turns.length}>
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

  /** The Explorer Board on the Canvas before the first question, above the examples (SPEC §18). */
  private renderBoard() {
    if (this.turns.length || this.board.state !== 'shown') return nothing;
    const surface = this.processor.model.surfacesMap.get(this.board.surfaceId);
    return surface ? html`<section class="board"><a2ui-surface .surface=${surface}></a2ui-surface></section>` : nothing;
  }

  private renderEmpty() {
    if (this.turns.length) return nothing;
    const examples = this.info?.examples ?? [];
    return html`<div class="empty">
      <h2>Ask a question in plain English.</h2>
      <p>Every number comes from the database, and each chart and table shows its SQL.</p>
      ${examples.length
        ? html`<p class="label">Try</p>
            <div class="examples">
              ${examples.map(x => html`<button type="button" @click=${() => void this.ask(x)}>${x}</button>`)}
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

  // Sidebar -------------------------------------------------------------------------------------

  private renderEntryState(turn: Turn) {
    if (turn.elapsedMs === undefined || turn.running) {
      const startedAt = turn.running?.startedAt ?? turn.startedAt;
      return html`<span class="spinner" aria-hidden="true"></span><span class="elapsed">${seconds(this.now - startedAt)}</span>`;
    }
    if (turn.error) return html`Failed`;
    return turn.levels.length > 1 ? html`${turn.levels.length} levels` : nothing;
  }

  /** Rendered only when there are Turns: an `ol` holding template whitespace never matches `:empty`. */
  private renderTurnList() {
    return html`<ol class="turn-list">
      ${this.turns.map((t, i) => html`<li>
        <button
          type="button"
          class=${i === this.selected ? 'turn-entry selected' : 'turn-entry'}
          aria-current=${i === this.selected ? 'page' : 'false'}
          @click=${() => this.select(i)}
        >
          <span class="entry-question">${t.question}</span>
          <span class="entry-state">${this.renderEntryState(t)}</span>
        </button>
      </li>`)}
    </ol>`;
  }

  private renderSidebar() {
    return html`<aside class="sidebar" aria-label="Turns">
      <div class="sidebar-head label"><span>Turns</span><span class="count">${this.turns.length}</span></div>
      ${this.turns.length ? this.renderTurnList() : html`<div class="turn-list-spacer"></div>`}
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

  // Canvas --------------------------------------------------------------------------------------

  private renderBreadcrumb(turn: Turn, index: number) {
    if (turn.levels.length < 2) return nothing;
    return html`<nav class="breadcrumb" aria-label="Levels">
      ${turn.levels.map((l, i) => html`${i ? html`<span class="sep" aria-hidden="true">›</span>` : nothing}<button
          type="button"
          class=${i === turn.current ? 'level current' : 'level'}
          aria-current=${i === turn.current ? 'page' : 'false'}
          ?disabled=${!!turn.running}
          @click=${() => this.showLevel(index, i)}
        >${l.label}</button>`)}
    </nav>`;
  }

  private renderActionBars(turn: Turn, index: number) {
    return html`${turn.confirm
      ? html`<div
          class="confirm"
          role="dialog"
          aria-label="Ask the agent"
          @keydown=${(e: KeyboardEvent) => e.key === 'Escape' && this.cancelAsk(index)}
        >
          <span>Ask the agent: <strong>${turn.confirm.label}</strong>. This takes a few seconds.</span>
          <button type="button" class="ok" ?disabled=${this.busy} @click=${() => this.confirmAsk(index)}>Ask</button>
          <button type="button" class="cancel" @click=${() => this.cancelAsk(index)}>Cancel</button>
        </div>`
      : nothing}
    ${turn.actionError
      ? html`<div class="error action-error" role="alert">
          <span>${turn.actionError.message}</span>
          <button class="retry" type="button" ?disabled=${this.busy} @click=${() => this.retryAction(index)}>Retry</button>
          ${turn.actionError.action.name === 'drill'
            ? html`<button class="ask-instead" type="button" ?disabled=${this.busy} @click=${() => this.askInstead(index)}>Ask the agent instead</button>`
            : nothing}
        </div>`
      : nothing}
    ${turn.running ? this.renderStatus(turn.running.steps, turn.running.startedAt) : nothing}`;
  }

  /** The status line under a Level: time, steps and the serving model (SPEC §16). */
  private renderMeta(elapsedMs: number, steps: string[], model: string | null | undefined, failed = false) {
    const parts = [
      ...(steps.length ? [`${steps.length} step${steps.length === 1 ? '' : 's'}`] : []),
      ...(model === null ? ['No model · database only'] : model ? [model] : []),
    ];
    return html`<details class="meta">
      <summary><span class="elapsed">${failed ? 'Failed after' : 'Answered in'} ${seconds(elapsedMs)}</span>${parts.map(
        p => html` · <span>${p}</span>`,
      )}</summary>
      ${steps.length ? html`<ol class="steps">${steps.map(s => html`<li>${s}</li>`)}</ol>` : nothing}
    </details>`;
  }

  private renderTurn(turn: Turn, index: number) {
    const shown = turn.levels[turn.current];
    const surface = shown?.surfaceId ? this.processor.model.surfacesMap.get(shown.surfaceId) : undefined;
    const answering = turn.elapsedMs === undefined;
    return html`<section class="turn">
      <h2 class="question">${turn.question}</h2>
      ${answering ? this.renderStatus(turn.steps, turn.startedAt) : nothing}
      ${turn.error
        ? html`<div class="error" role="alert">
            <span>${turn.error}</span>
            <button class="retry" type="button" ?disabled=${this.busy} @click=${() => void this.retry(index)}>Retry</button>
          </div>`
        : nothing}
      ${this.renderBreadcrumb(turn, index)} ${this.renderActionBars(turn, index)}
      <div class=${turn.running || this.busy ? 'level-view inert' : 'level-view'} ?inert=${!!turn.running}>
        ${shown?.proseHtml ? html`<div class="prose">${unsafeHTML(shown.proseHtml)}</div>` : nothing}
        ${surface ? html`<a2ui-surface .surface=${surface}></a2ui-surface>` : nothing}
      </div>
      ${shown
        ? this.renderMeta(shown.elapsedMs, shown.steps, shown.model)
        : turn.error && turn.elapsedMs !== undefined
          ? this.renderMeta(turn.elapsedMs, turn.steps, undefined, true)
          : nothing}
    </section>`;
  }

  override render() {
    const turn = this.turns[this.selected];
    return html`
      ${this.renderHeader()}
      <div class="shell">
        ${this.renderSidebar()}
        <main class="canvas">
          ${this.renderBanner()} ${this.renderBoard()} ${this.renderEmpty()} ${turn ? this.renderTurn(turn, this.selected) : nothing}
        </main>
      </div>
    `;
  }
}
