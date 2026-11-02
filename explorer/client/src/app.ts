// <explorer-app>: the chat view. One surface per agent turn (surface ID `turn-<n>-<tag>`, the tag random per conversation).
import {LitElement, html, nothing} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {unsafeHTML} from 'lit/directives/unsafe-html.js';
import {provide} from '@lit/context';
import {Context} from '@a2ui/lit/v0_9';
import '@a2ui/lit/v0_9';
import type {A2uiMessage, ComponentApi, MessageProcessor} from '@a2ui/web_core/v0_9';
import {connect, type AgentConnection, type AgentInfo} from './a2a.js';
import {createProcessor} from './catalog.js';
import {renderSafeMarkdown} from './markdown.js';

interface Turn {
  question: string;
  steps: string[]; // the agent's status updates, in order
  startedAt: number;
  elapsedMs?: number; // set when the turn ends, answered or failed
  proseHtml?: string; // sanitized by @a2ui/markdown-it, images removed
  surfaceId?: string;
  error?: string;
}

export function surfaceIdOf(messages: Record<string, unknown>[]): string | undefined {
  for (const m of messages) {
    const create = m['createSurface'] as {surfaceId?: string} | undefined;
    if (create?.surfaceId) return create.surfaceId;
  }
  return undefined;
}

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

@customElement('explorer-app')
export class ExplorerApp extends LitElement {
  @provide({context: Context.markdown})
  markdownRenderer = (value: string, options?: unknown) => renderSafeMarkdown(value, options);

  @state() private turns: Turn[] = [];
  @state() private busy = false;
  @state() private info?: AgentInfo;
  @state() private connectError?: string;
  @state() private now = Date.now();

  private processor: MessageProcessor<ComponentApi> = createProcessor();
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
  }

  override disconnectedCallback() {
    super.disconnectedCallback();
    this.stopTicker();
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

  async ask(question: string) {
    if (this.busy) return;
    this.turns = [...this.turns, {question, steps: [], startedAt: Date.now()}];
    await this.run(this.turns.length - 1);
  }

  private async retry(index: number) {
    if (this.busy) return;
    const {question} = this.turns[index]!;
    this.patch(index, {question, steps: [], startedAt: Date.now(), elapsedMs: undefined, error: undefined});
    await this.run(index);
  }

  private async run(index: number) {
    const turn = this.turns[index]!;
    const processor = this.processor; // a new conversation replaces it mid-turn
    this.busy = true;
    this.startTicker();
    void this.updateComplete.then(() =>
      this.querySelectorAll('.turn')[index]?.scrollIntoView?.({behavior: 'smooth', block: 'start'}),
    );
    try {
      const agent = await this.connectionOnce();
      for await (const event of agent.ask(turn.question, this.contextId)) {
        if (processor !== this.processor) return; // the user started a new conversation
        if (event.kind === 'status') {
          this.patch(index, {steps: [...this.turns[index]!.steps, event.text]});
          continue;
        }
        this.contextId = event.contextId;
        if (event.a2ui.length) processor.processMessages(event.a2ui as unknown as A2uiMessage[]);
        this.patch(index, {
          elapsedMs: Date.now() - turn.startedAt,
          proseHtml: await renderSafeMarkdown(event.prose),
          surfaceId: surfaceIdOf(event.a2ui),
        });
      }
    } catch (e) {
      if (processor !== this.processor) return;
      this.patch(index, {elapsedMs: Date.now() - turn.startedAt, error: e instanceof Error ? e.message : String(e)});
    } finally {
      if (processor === this.processor) {
        this.busy = false;
        this.stopTicker();
      }
    }
  }

  private newConversation() {
    this.turns = [];
    this.contextId = undefined;
    this.processor = createProcessor();
    this.busy = false;
    this.stopTicker();
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

  private renderHeader() {
    return html`<header>
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

  private renderEmpty() {
    if (this.turns.length) return nothing;
    const examples = this.info?.examples ?? [];
    return html`<div class="empty">
      <p>Ask a question in plain English. Every number comes from the database, and each chart and table shows its SQL.</p>
      ${examples.length
        ? html`<div class="examples">
            ${examples.map(x => html`<button type="button" @click=${() => void this.ask(x)}>${x}</button>`)}
          </div>`
        : nothing}
    </div>`;
  }

  private renderTurn(turn: Turn, index: number) {
    const surface = turn.surfaceId ? this.processor.model.surfacesMap.get(turn.surfaceId) : undefined;
    const running = turn.elapsedMs === undefined;
    const steps = turn.steps.length
      ? html`<ol class="steps">${turn.steps.map(s => html`<li>${s}</li>`)}</ol>`
      : nothing;
    return html`<section class="turn">
      <div class="question">${turn.question}</div>
      ${running
        ? html`<div class="status" aria-live="polite">
            <span class="spinner" aria-hidden="true"></span>
            ${turn.steps.at(-1) ?? 'Thinking…'} <span class="elapsed">${seconds(this.now - turn.startedAt)}</span>
          </div>`
        : nothing}
      ${turn.error
        ? html`<div class="error" role="alert">
            ${turn.error}
            <button class="retry" type="button" ?disabled=${this.busy} @click=${() => void this.retry(index)}>Retry</button>
          </div>`
        : nothing}
      ${turn.proseHtml ? html`<div class="prose">${unsafeHTML(turn.proseHtml)}</div>` : nothing}
      ${surface ? html`<a2ui-surface .surface=${surface}></a2ui-surface>` : nothing}
      ${!running
        ? html`<details class="meta">
            <summary><span class="elapsed">${turn.error ? 'Failed after' : 'Answered in'} ${seconds(turn.elapsedMs!)}</span>${
              turn.steps.length ? html` · ${turn.steps.length} step${turn.steps.length === 1 ? '' : 's'}` : nothing
            }</summary>
            ${steps}
          </details>`
        : nothing}
    </section>`;
  }

  override render() {
    return html`
      ${this.renderHeader()} ${this.renderBanner()}
      <main>${this.renderEmpty()} ${this.turns.map((t, i) => this.renderTurn(t, i))}</main>
      <form class="composer" @submit=${this.onSubmit}>
        <textarea
          rows="2"
          placeholder="Ask a question about the data (Enter to send, Shift+Enter for a new line)"
          @keydown=${this.onKeydown}
        ></textarea>
        <button type="submit" ?disabled=${this.busy}>${this.busy ? 'Working…' : 'Ask'}</button>
      </form>
    `;
  }
}
