import { LitElement, html } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import { Api, ApiError } from './api.js';
import './connect.js';
import './report.js';
import './review.js';
import './boards.js';
import type { ReportSelection } from './connect.js';
import { MODELS } from './types.js';
import { boardProblems } from './types.js';
import type { BoardsState, Connection, Draft, DraftEvent, Estimate, Report, Validation } from './types.js';

export type Step = 'connect' | 'report' | 'review' | 'boards';
const STEPS: { key: Step; label: string }[] = [
  { key: 'connect', label: 'Connect' },
  { key: 'report', label: 'Report' },
  { key: 'review', label: 'Review' },
  { key: 'boards', label: 'Boards' },
];

/** Holds the state and shows one screen at a time, with the current step always visible. */
@customElement('bootstrap-app')
export class BootstrapApp extends LitElement {
  @property({ attribute: false }) api = new Api();
  /** Milliseconds between progress polls while drafting; tests set 0. */
  @property({ type: Number }) pollMs = 500;

  @state() step: Step = 'connect';
  @state() connection: Connection | null = null;
  @state() datasets: string[] = [];
  @state() models: string[] = [...MODELS];
  @state() defaultModel: string = MODELS[0];
  @state() selection: ReportSelection | null = null;
  @state() report: Report | null = null;
  @state() estimate: Estimate | null = null;
  @state() events: DraftEvent[] = [];
  @state() running = false;
  @state() busy = false;
  @state() draft: Draft | null = null;
  @state() validation: Validation | null = null;
  @state() error = '';
  // Phase 3: the Boards step, at the model and effort last picked on the Report screen.
  @state() boards: BoardsState | null = null;
  @state() boardsEstimate: Estimate | null = null;
  @state() boardEvents: DraftEvent[] = [];
  @state() boardsRunning = false;
  @state() model = '';
  @state() effort = 'low';

  protected override createRenderRoot() {
    return this;
  }

  override connectedCallback() {
    super.connectedCallback();
    void this.load();
  }

  private async guard(fn: () => Promise<void>) {
    this.error = '';
    try {
      await fn();
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    }
  }

  async load() {
    await this.guard(async () => {
      this.connection = await this.api.connection();
    });
    // Datasets and the model list are a bonus: the client works without them.
    try {
      this.datasets = (await this.api.datasets()).datasets;
    } catch {
      this.datasets = [];
    }
    try {
      const m = await this.api.models();
      if (m.models.length > 0) this.models = m.models;
      if (m.models.includes(m.default)) this.defaultModel = m.default;
    } catch {
      this.models = [...MODELS];
      this.defaultModel = MODELS[0];
    }
  }

  async runReport(sel: ReportSelection) {
    this.busy = true;
    await this.guard(async () => {
      this.selection = sel;
      this.report = await this.api.report(sel);
      this.estimate = null;
      this.step = 'report';
    });
    this.busy = false;
  }

  async refreshEstimate(model: string, effort: string) {
    await this.guard(async () => {
      this.estimate = await this.api.estimate(model, effort);
    });
  }

  async startDraft(model: string, effort: string) {
    this.model = model;
    this.effort = effort;
    await this.guard(async () => {
      const { run_id } = await this.api.startDraft(model, effort);
      this.running = true;
      this.events = [];
      let after = 0;
      for (;;) {
        const r = await this.api.events(run_id, after);
        this.events = [...this.events, ...r.events];
        after = r.next;
        if (r.done) {
          this.running = false;
          if (r.error) throw new Error(r.error);
          this.draft = r.draft ?? null;
          this.validation = r.validation ?? null;
          this.boards = null; // the server clears them with a new draft
          this.step = 'review';
          return;
        }
        await new Promise((ok) => setTimeout(ok, this.pollMs));
      }
    });
    this.running = false;
  }

  /** Back to the draft as the server holds it: it has the stored edits and claim results. */
  async backToDraft() {
    await this.guard(async () => {
      const r = await this.api.currentDraft();
      this.draft = r.draft;
      this.validation = r.validation;
      this.step = 'review';
    });
  }

  /** To the Boards step, with the Boards the server holds (none yet is a 404). */
  async toBoards() {
    await this.guard(async () => {
      try {
        this.boards = await this.api.currentBoards();
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) throw e;
        this.boards = null;
      }
      this.boardEvents = [];
      this.boardsEstimate = null;
      this.step = 'boards';
    });
  }

  async refreshBoardsEstimate(model: string, effort: string) {
    await this.guard(async () => {
      this.boardsEstimate = await this.api.boardsEstimate(model, effort);
    });
  }

  async startBoards(model: string, effort: string) {
    await this.guard(async () => {
      const { run_id } = await this.api.startBoards(model, effort);
      this.boardsRunning = true;
      this.boardEvents = [];
      let after = 0;
      for (;;) {
        const r = await this.api.events(run_id, after);
        this.boardEvents = [...this.boardEvents, ...r.events];
        after = r.next;
        if (r.done) {
          this.boardsRunning = false;
          if (r.error) throw new Error(r.error);
          this.boards = r.boards ?? null;
          return;
        }
        await new Promise((ok) => setTimeout(ok, this.pollMs));
      }
    });
    this.boardsRunning = false;
  }

  private renderStep() {
    switch (this.step) {
      case 'connect':
        return html`<bs-connect .connection=${this.connection} .api=${this.api} .datasets=${this.datasets} ?busy=${this.busy}
          @run-report=${(e: CustomEvent<ReportSelection>) => void this.runReport(e.detail)}></bs-connect>`;
      case 'report':
        return html`<bs-report .report=${this.report} .estimate=${this.estimate} .events=${this.events} ?running=${this.running} .models=${this.models} .defaultModel=${this.defaultModel}
          @estimate=${(e: CustomEvent<{ model: string; effort: string }>) => void this.refreshEstimate(e.detail.model, e.detail.effort)}
          @draft=${(e: CustomEvent<{ model: string; effort: string }>) => void this.startDraft(e.detail.model, e.detail.effort)}
        ></bs-report>
        ${this.draft ? html`<button class="to-review" @click=${() => void this.backToDraft()}>Back to the draft</button>` : ''}`;
      case 'review':
        return html`<bs-review .api=${this.api} .draft=${this.draft} .validation=${this.validation} .boardProblems=${boardProblems(this.boards)}
          @rerun-report=${() => {
            if (this.selection) void this.runReport(this.selection);
          }}
          @to-boards=${() => void this.toBoards()}></bs-review>`;
      case 'boards':
        return html`<bs-boards .api=${this.api} .boards=${this.boards} .estimate=${this.boardsEstimate} .events=${this.boardEvents}
          ?running=${this.boardsRunning} .model=${this.model || this.defaultModel} .effort=${this.effort}
          @estimate=${(e: CustomEvent<{ model: string; effort: string }>) => void this.refreshBoardsEstimate(e.detail.model, e.detail.effort)}
          @draft-boards=${(e: CustomEvent<{ model: string; effort: string }>) => void this.startBoards(e.detail.model, e.detail.effort)}
          @boards-changed=${(e: CustomEvent<BoardsState>) => (this.boards = e.detail)}
          @back=${() => void this.backToDraft()}></bs-boards>`;
    }
  }

  override render() {
    return html`
      <header>
        <h1>Dataset bootstrap</h1>
        <nav aria-label="Steps">
          <ol class="steps">
            ${STEPS.map(
              (s) => html`<li class=${this.step === s.key ? 'current' : ''} aria-current=${this.step === s.key ? 'step' : 'false'}>
                ${s.label}
              </li>`,
            )}
          </ol>
        </nav>
      </header>
      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : ''}
      <main>${this.renderStep()}</main>
    `;
  }
}
