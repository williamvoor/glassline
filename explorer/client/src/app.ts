// <explorer-app>: the chat view. One surface per agent turn (surface ID `turn-<n>`).
import {LitElement, html, nothing} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {unsafeHTML} from 'lit/directives/unsafe-html.js';
import {provide} from '@lit/context';
import {Context} from '@a2ui/lit/v0_9';
import '@a2ui/lit/v0_9';
import type {A2uiMessage, ComponentApi, MessageProcessor} from '@a2ui/web_core/v0_9';
import {connect, type AgentConnection} from './a2a.js';
import {createProcessor} from './catalog.js';
import {renderSafeMarkdown} from './markdown.js';

interface Turn {
  question: string;
  status?: string;
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

@customElement('explorer-app')
export class ExplorerApp extends LitElement {
  @provide({context: Context.markdown})
  markdownRenderer = (value: string, options?: unknown) => renderSafeMarkdown(value, options);

  @state() private turns: Turn[] = [];
  @state() private busy = false;

  private readonly processor: MessageProcessor<ComponentApi> = createProcessor();
  private connection?: Promise<AgentConnection>;
  private contextId?: string;

  agentUrl = import.meta.env.VITE_AGENT_URL ?? 'http://127.0.0.1:10002';

  override createRenderRoot() {
    // Light DOM, so page styles (index.html) reach the A2UI components.
    return this;
  }

  private connectionOnce(): Promise<AgentConnection> {
    this.connection ??= connect(
      this.agentUrl,
      this.processor.getRendererCapabilities({versions: ['v0.9']}),
    );
    return this.connection;
  }

  private update_(index: number, patch: Partial<Turn>) {
    this.turns = this.turns.map((t, i) => (i === index ? {...t, ...patch} : t));
  }

  async ask(question: string) {
    const index = this.turns.length;
    this.turns = [...this.turns, {question, status: 'Thinking…'}];
    this.busy = true;
    try {
      const agent = await this.connectionOnce();
      for await (const event of agent.ask(question, this.contextId)) {
        if (event.kind === 'status') {
          this.update_(index, {status: event.text});
          continue;
        }
        this.contextId = event.contextId;
        if (event.a2ui.length) this.processor.processMessages(event.a2ui as unknown as A2uiMessage[]);
        this.update_(index, {
          status: undefined,
          proseHtml: await renderSafeMarkdown(event.prose),
          surfaceId: surfaceIdOf(event.a2ui),
        });
      }
    } catch (e) {
      this.connection = undefined; // retry the connection next time
      this.update_(index, {status: undefined, error: e instanceof Error ? e.message : String(e)});
    } finally {
      this.busy = false;
    }
  }

  private onSubmit(e: Event) {
    e.preventDefault();
    const input = (e.target as HTMLFormElement).querySelector('input')!;
    const question = input.value.trim();
    if (!question || this.busy) return;
    input.value = '';
    void this.ask(question);
  }

  override render() {
    return html`
      ${this.turns.map(turn => {
        const surface = turn.surfaceId ? this.processor.model.surfacesMap.get(turn.surfaceId) : undefined;
        return html`<section class="turn">
          <div class="question">${turn.question}</div>
          ${turn.status ? html`<div class="status">${turn.status}</div>` : nothing}
          ${turn.error ? html`<div class="error">${turn.error}</div>` : nothing}
          ${turn.proseHtml ? html`<div class="prose">${unsafeHTML(turn.proseHtml)}</div>` : nothing}
          ${surface ? html`<a2ui-surface .surface=${surface}></a2ui-surface>` : nothing}
        </section>`;
      })}
      <form @submit=${this.onSubmit}>
        <input placeholder="Ask a question about the data" ?disabled=${this.busy} />
        <button ?disabled=${this.busy}>Ask</button>
      </form>
    `;
  }
}
