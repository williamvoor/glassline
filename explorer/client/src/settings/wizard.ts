// <explorer-wizard>: New Workspace (and Regenerate) as five steps — Database, Draft, Review, Boards,
// Export (specs/workspaces/SPEC.md phase 3, ADR 0044). No pop-ups: every confirmation is a card in the
// step. Next never calls a model; only the Draft buttons do, and they carry the cost ceiling. The flow's
// state lives here, and the server holds a copy (`/api/session`), so a reloaded page resumes.
import {LitElement, html, nothing} from 'lit';
import {customElement, property, state} from 'lit/decorators.js';

import {confirmCard} from '../cards.js';
import {modelChip} from '../model-chip.js';
import {Api, ApiError} from './api.js';
import './connect.js';
import './report.js';
import './review.js';
import './boards.js';
import type {ReportSelection} from './connect.js';
import {suggestedName, type BsReview} from './review.js';
import {MODELS, boardProblems} from './types.js';
import type {BoardsState, Connection, Draft, DraftEvent, Estimate, Report, Validation} from './types.js';

export type WizardStep = 'database' | 'draft' | 'review' | 'boards' | 'export';
export const WIZARD_STEPS: {key: WizardStep; label: string; model: boolean}[] = [
  {key: 'database', label: 'Database', model: false},
  {key: 'draft', label: 'Draft', model: true},
  {key: 'review', label: 'Review', model: false},
  {key: 'boards', label: 'Boards', model: true},
  {key: 'export', label: 'Export', model: false},
];
const NAME = /^[a-z][a-z0-9_]*$/;
const LOST = 'This replaces the Draft and its Boards. Your edits are lost.';

interface Asking {
  message: string;
  confirm: string;
  run: () => Promise<void> | void;
}

interface Exported {
  workspace: string;
  reloaded: boolean;
  backup: string | null;
}

@customElement('explorer-wizard')
export class ExplorerWizard extends LitElement {
  @property({attribute: false}) api = new Api();
  /** Milliseconds between progress polls while drafting; tests set 0. */
  @property({type: Number}) pollMs = 500;

  @state() step: WizardStep = 'database';
  @state() reached = 0; // the furthest step index the flow's state supports
  @state() available: string[] = [];
  @state() connection: (Connection & {dsn_env: string}) | null = null;
  @state() regenerating = '';
  @state() report: Report | null = null;
  @state() models: string[] = [...MODELS];
  @state() defaultModel: string = MODELS[0];
  @state() model = '';
  @state() effort = 'low';
  @state() estimate: Estimate | null = null;
  @state() events: DraftEvent[] = [];
  @state() running = false;
  @state() draft: Draft | null = null;
  @state() validation: Validation | null = null;
  @state() reviewBlockers: string[] = [];
  @state() boards: BoardsState | null = null;
  @state() boardsEstimate: Estimate | null = null;
  @state() boardEvents: DraftEvent[] = [];
  @state() boardsRunning = false;
  @state() name = '';
  @state() exported: Exported | null = null;
  @state() reasons: string[] = [];
  @state() asking: Asking | null = null;
  @state() busy = false;
  @state() error = '';
  @state() notice = '';

  protected override createRenderRoot() {
    return this;
  }

  override connectedCallback() {
    super.connectedCallback();
    void this.loadModels();
  }

  /** Something is in progress: the list offers to continue it. */
  get inProgress(): boolean {
    return Boolean(this.connection || this.report || this.draft) && !this.exported;
  }

  private async loadModels() {
    try {
      const m = await this.api.models();
      if (m.models.length > 0) this.models = m.models;
      if (m.models.includes(m.default)) this.defaultModel = m.default;
    } catch {
      // the built-in list
    }
  }

  private async guard(fn: () => Promise<void>) {
    this.error = '';
    try {
      await fn();
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    }
  }

  private go(step: WizardStep) {
    this.step = step;
    this.reached = Math.max(this.reached, WIZARD_STEPS.findIndex(s => s.key === step));
    this.asking = null;
    this.error = this.notice = ''; // a message belongs to the step it was shown on (refuter)
  }

  /** Clears everything after `from` (a change upstream makes it stale). */
  private dropAfter(from: WizardStep) {
    const i = WIZARD_STEPS.findIndex(s => s.key === from);
    if (i < 1) {
      this.report = null;
      this.estimate = null;
    }
    if (i < 2) {
      this.draft = this.validation = null;
      this.events = [];
      this.reviewBlockers = [];
      this.name = '';
    }
    if (i < 4) {
      this.boards = this.boardsEstimate = null;
      this.boardEvents = [];
    }
    this.exported = null;
    this.reasons = [];
    this.reached = i;
  }

  reset() {
    this.connection = null;
    this.regenerating = '';
    this.dropAfter('database');
    this.step = 'database';
    this.reached = 0;
    this.asking = null;
    this.error = this.notice = '';
  }

  private close() {
    this.dispatchEvent(new CustomEvent('wizard-close', {bubbles: true, composed: true}));
  }

  /** Shown again (opened from the list, or after a page reload): match the server's state. */
  async resume() {
    await this.guard(async () => {
      const s = await this.api.session();
      const stale = (this.report && !s.report) || (this.connection && s.dsn_env !== this.connection.dsn_env);
      if (stale && !this.running && !this.boardsRunning) {
        const lost = Boolean(this.report || this.draft);
        this.reset();
        if (lost) this.notice = 'The server restarted, so the Draft is gone.';
      }
      // Rebuilt only from progress (a report): a login alone, kept after a discard (E8), starts afresh.
      if (!this.report && !this.connection && s.report) {
        if (s.dsn_env) this.connection = {...(await this.api.connection()), dsn_env: s.dsn_env};
        this.regenerating = s.regenerating ?? '';
        this.report = s.report;
        if (s.draft) {
          const d = await this.api.currentDraft();
          this.draft = d.draft;
          this.validation = d.validation;
          this.name = suggestedName(d.draft);
        }
        if (s.boards) this.boards = await this.api.currentBoards();
        const boardsRun = s.run?.kind === 'boards'; // a Boards run has no Boards yet, but belongs on their step
        this.step = this.boards || boardsRun ? 'boards' : this.draft ? 'review' : this.report ? 'draft' : 'database';
        this.reached = WIZARD_STEPS.findIndex(x => x.key === this.step);
        if (s.run) void this.follow(s.run.id, s.run.kind);
      }
      if (!this.connection && !this.regenerating) this.available = (await this.api.connections()).connections;
    });
  }

  // Step 1: Database ---------------------------------------------------------------------------

  chooseConnection(name: string) {
    if (this.connection?.dsn_env === name) return;
    if (this.report || this.draft) {
      this.asking = {message: LOST, confirm: `Use ${name}`, run: () => this.connect(name)};
      return;
    }
    void this.connect(name);
  }

  private async connect(name: string) {
    this.asking = null;
    this.notice = '';
    this.busy = true;
    await this.guard(async () => {
      this.connection = await this.api.connect(name);
      this.regenerating = '';
      this.dropAfter('database');
      this.step = 'database';
    });
    this.busy = false;
  }

  runReport(sel: ReportSelection) {
    if (this.draft) {
      this.asking = {message: LOST, confirm: 'Run the report again', run: () => this.report_(sel)};
      return;
    }
    void this.report_(sel);
  }

  private async report_(sel: ReportSelection) {
    this.asking = null;
    this.busy = true;
    await this.guard(async () => {
      this.report = await this.api.report(sel); // the server drops its Draft and Boards (E5)
      this.dropAfter('draft');
      this.go('draft');
    });
    this.busy = false;
  }

  /** A run is going: nothing may replace the flow under it. */
  get runningNow(): boolean {
    return this.running || this.boardsRunning;
  }

  /** Regenerate from the list: the database and schemas are the Workspace's, so start at Draft. The list
   * offers it only when no New Workspace is in progress; the state is replaced only once the server agrees. */
  async regenerate(name: string) {
    if (this.runningNow) return;
    this.busy = true;
    this.error = '';
    try {
      const report = await this.api.regenerate(name);
      this.reset();
      this.report = report;
      this.regenerating = name;
      this.go('draft');
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.busy = false;
    }
  }

  // Steps 2 and 4: model runs ------------------------------------------------------------------

  private estimateSeq = 0;

  /** Only the newest request's reply is kept, so an older model's ceiling never lands last (S5). */
  async refreshEstimate(model: string, effort: string) {
    const seq = ++this.estimateSeq;
    this.estimate = null;
    await this.guard(async () => {
      const e = await this.api.estimate(model, effort);
      if (seq === this.estimateSeq) this.estimate = e;
    });
  }

  async startDraft(model: string, effort: string) {
    this.model = model;
    this.effort = effort;
    await this.guard(async () => {
      const {run_id} = await this.api.startDraft(model, effort);
      this.dropAfter('draft');
      await this.follow(run_id, 'draft');
    });
  }

  private boardsEstimateSeq = 0;

  async refreshBoardsEstimate(model: string, effort: string) {
    const seq = ++this.boardsEstimateSeq;
    this.boardsEstimate = null;
    await this.guard(async () => {
      const e = await this.api.boardsEstimate(model, effort);
      if (seq === this.boardsEstimateSeq) this.boardsEstimate = e;
    });
  }

  async startBoards(model: string, effort: string) {
    await this.guard(async () => {
      const {run_id} = await this.api.startBoards(model, effort);
      await this.follow(run_id, 'boards');
    });
  }

  /** Polls a run until it's done; a finished Draft moves to Review, finished Boards stay. */
  private async follow(runId: string, kind: 'draft' | 'boards') {
    const isDraft = kind === 'draft';
    if (isDraft) {
      this.running = true;
      this.events = [];
    } else {
      this.boardsRunning = true;
      this.boardEvents = [];
    }
    try {
      let after = 0;
      for (;;) {
        const r = await this.api.events(runId, after);
        if (isDraft) this.events = [...this.events, ...r.events];
        else this.boardEvents = [...this.boardEvents, ...r.events];
        after = r.next;
        if (r.done) {
          if (r.error) throw new Error(r.error);
          if (isDraft) {
            this.draft = r.draft ?? null;
            this.validation = r.validation ?? null;
            this.name = this.name || suggestedName(this.draft);
            this.boards = null;
            this.go('review');
          } else {
            this.boards = r.boards ?? null;
          }
          return;
        }
        await new Promise(ok => setTimeout(ok, this.pollMs));
      }
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    } finally {
      if (isDraft) this.running = false;
      else this.boardsRunning = false;
    }
  }

  // Step 3: Review -----------------------------------------------------------------------------

  private get reviewEl(): BsReview | null {
    return this.querySelector<BsReview>('bs-review');
  }

  async leaveReview() {
    await this.goTo('boards');
  }

  /** Moves by Back, Next or the step bar (E15): leaving Review saves its edits first (stay, with the
   * error shown, if that fails), and arriving on Boards rechecks the held Boards against them. */
  async goTo(step: WizardStep) {
    if (this.step === 'review' && step !== 'review') {
      const review = this.reviewEl;
      if (review && !(await review.leave())) return;
    }
    this.go(step);
    if (step === 'boards' && this.boards && !this.boardsRunning) {
      const files = this.boards.boards;
      await this.guard(async () => {
        this.boards = await this.api.validateBoards(files);
      });
    }
  }

  // Step 4: Boards -----------------------------------------------------------------------------

  async keepBoards() {
    this.busy = true;
    try {
      this.boards = await this.api.keepBoards();
      this.error = '';
    } catch (e) {
      if (e instanceof ApiError && e.status === 404 && !this.draft) this.error = e.message;
      else if (e instanceof ApiError && e.status === 404) {
        this.go('export');
        this.notice = `${this.regenerating} has no Boards to keep, so it goes on without Boards.`;
      } else this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.busy = false;
    }
  }

  // Step 5: Export -----------------------------------------------------------------------------

  async exportNow() {
    const review = this.reviewEl;
    if (!review) return;
    this.busy = true;
    this.reasons = [];
    this.error = '';
    try {
      const r = await this.api.export({name: this.regenerating || this.name.trim(), target: 'toml', force: false,
        ...review.exportPayload()});
      this.exported = {workspace: r.workspace ?? this.name, reloaded: r.reloaded ?? false, backup: r.backup};
      this.dispatchEvent(new CustomEvent('workspaces-changed', {bubbles: true, composed: true}));
    } catch (e) {
      if (e instanceof ApiError && e.reasons.length) this.reasons = e.reasons;
      else this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.busy = false;
    }
  }

  private async reloadAgent() {
    await this.guard(async () => {
      const r = await this.api.reload();
      if (this.exported) this.exported = {...this.exported, reloaded: r.reloaded};
      if (!r.reloaded) this.error = `The agent didn't reload${r.error ? `: ${r.error}` : r.status === 0 ? ': it is not running' : ''}.`;
      this.dispatchEvent(new CustomEvent('workspaces-changed', {bubbles: true, composed: true}));
    });
  }

  /** Done with this flow (exported, or discarded): the server forgets it, and so does the wizard. */
  private async finish(then: () => void) {
    try {
      await this.api.discard();
    } catch {
      // nothing held, or a run: the next connect clears it anyway
    }
    this.reset();
    then();
  }

  /** With a Draft (it cost a model run), ask on a card; without one, just go back to Settings. */
  discard() {
    if (!this.draft) {
      void this.finish(() => this.close());
      return;
    }
    this.asking = {
      message: 'Discard the Draft? It cost a model run, and it can\'t be brought back.',
      confirm: 'Discard the Draft',
      run: () => this.finish(() => this.close()),
    };
  }

  // Rendering ----------------------------------------------------------------------------------

  private stepModel(key: WizardStep): string | null {
    if (key === 'draft') return this.draft?.model ?? (this.model || this.defaultModel);
    if (key === 'boards') return this.boards?.model ?? this.draft?.model ?? (this.model || this.defaultModel);
    return null;
  }

  private canVisit(i: number): boolean {
    if (this.running || this.boardsRunning || this.exported) return false;
    if (this.regenerating && i === 0) return false;
    return i <= this.reached;
  }

  private renderSteps() {
    const at = WIZARD_STEPS.findIndex(s => s.key === this.step);
    return html`<nav class="wizard-steps" aria-label="New Workspace steps"><ol>
      ${WIZARD_STEPS.map((s, i) => {
        const state = i === at ? 'current' : i < at || i <= this.reached ? 'done' : 'todo';
        const locked = this.regenerating && i === 0;
        const label = html`<span class="num" aria-hidden="true">${i + 1}</span><span class="step-name">${s.label}</span>
          ${s.model ? modelChip(this.stepModel(s.key)) : modelChip(null)}`;
        const name = `Step ${i + 1}: ${s.label}${locked ? ' (fixed in a Regenerate)' : ''}`; // the label hides on phones
        return html`<li class="${state}${locked ? ' locked' : ''}" aria-current=${i === at ? 'step' : 'false'}>
          ${this.canVisit(i) && i !== at
            ? html`<button type="button" class="step-link" data-step=${s.key} aria-label=${name} @click=${() => void this.goTo(s.key)}>${label}</button>`
            : html`<span class="step-link" aria-label=${name}>${label}</span>`}
        </li>`;
      })}
    </ol></nav>`;
  }

  private renderDatabase() {
    if (this.regenerating) return nothing;
    return html`<section class="glass database" aria-labelledby="db-h">
        <h2 id="db-h">Choose a database</h2>
        <p class="lede">Pick the read-only login for the new Workspace. Logins are variables in the repo's <code>.env</code>;
          only their names are shown.</p>
        ${this.available.length || this.connection
          ? html`<div class="connections" role="radiogroup" aria-label="Logins">
              ${[...new Set([...(this.connection ? [this.connection.dsn_env] : []), ...this.available])].map(name => html`
                <button type="button" role="radio" class="connect-var" aria-checked=${this.connection?.dsn_env === name ? 'true' : 'false'}
                  ?disabled=${this.busy} @click=${() => this.chooseConnection(name)}>${name}</button>`)}
            </div>`
          : html`<p class="muted">Every <code>*_DATABASE_URL</code> variable in <code>.env</code> already has a Workspace.
              Add a new variable with a read-only login, then restart the explorer.</p>`}
      </section>
      ${this.connection
        ? html`<bs-connect .connection=${this.connection} .api=${this.api} .datasets=${[]} ?busy=${this.busy}
            @run-report=${(e: CustomEvent<ReportSelection>) => this.runReport(e.detail)}></bs-connect>`
        : nothing}`;
  }

  private renderDraft() {
    return html`<bs-report .report=${this.report} .estimate=${this.estimate} .events=${this.events} ?running=${this.running}
      .models=${this.models} .defaultModel=${this.defaultModel} ?hasDraft=${Boolean(this.draft)}
      .doneTotals=${this.draft?.usage.totals ?? null} .draftModel=${this.draft?.model ?? ''}
      @estimate=${(e: CustomEvent<{model: string; effort: string}>) => void this.refreshEstimate(e.detail.model, e.detail.effort)}
      @draft=${(e: CustomEvent<{model: string; effort: string}>) => void this.startDraft(e.detail.model, e.detail.effort)}
    ></bs-report>`;
  }

  /** Review stays mounted (hidden) through Export: its claim dismissals live only in the component. */
  private renderReview() {
    if (!this.draft) return nothing;
    return html`<div class="review-step" ?hidden=${this.step !== 'review'}>
      <bs-review .api=${this.api} .draft=${this.draft} .validation=${this.validation}
        @review-blockers=${(e: CustomEvent<string[]>) => (this.reviewBlockers = e.detail)}></bs-review>
    </div>`;
  }

  private renderBoards() {
    const model = this.draft?.model ?? (this.model || this.defaultModel);
    return html`<bs-boards .api=${this.api} .boards=${this.boards} .estimate=${this.boardsEstimate} .events=${this.boardEvents}
      ?running=${this.boardsRunning} .model=${model} .effort=${this.effort}
      @estimate=${(e: CustomEvent<{model: string; effort: string}>) => void this.refreshBoardsEstimate(e.detail.model, e.detail.effort)}
      @draft-boards=${(e: CustomEvent<{model: string; effort: string}>) => void this.startBoards(e.detail.model, e.detail.effort)}
      @boards-changed=${(e: CustomEvent<BoardsState>) => (this.boards = e.detail)}></bs-boards>`;
  }

  private renderExport() {
    const x = this.exported;
    if (x) {
      return html`<section class="glass done-card" aria-labelledby="done-h">
        <h2 id="done-h"><strong>${x.workspace}</strong> is ready</h2>
        ${x.backup ? html`<p class="muted">The previous files are kept in <code>${x.backup}</code>.</p>` : nothing}
        ${x.reloaded
          ? nothing
          : html`<p class="warn-text">The agent didn't pick it up yet.</p>
              <button type="button" class="reload-agent" @click=${() => void this.reloadAgent()}>Reload the agent</button>`}
        <div class="row">
          <button type="button" class="primary open-workspace" ?disabled=${!x.reloaded}
            @click=${() => void this.finish(() => this.dispatchEvent(new CustomEvent('open-workspace',
              {detail: x.workspace, bubbles: true, composed: true})))}>Open ${x.workspace}</button>
          <button type="button" class="back-to-list" @click=${() => void this.finish(() => this.close())}>Back to Workspaces</button>
        </div>
      </section>`;
    }
    const name = this.regenerating || this.name.trim();
    const ok = NAME.test(name);
    const boards = this.boards ? `${this.boards.boards.length} Board${this.boards.boards.length === 1 ? '' : 's'}` : 'no Boards';
    return html`<section class="glass export" aria-labelledby="export-h">
      <h2 id="export-h">Export</h2>
      ${this.regenerating
        ? html`<p class="name-fixed">Regenerating <strong>${this.regenerating}</strong> (replaced files are backed up).</p>`
        : html`<label class="name-field">Workspace name
            <input class="name" .value=${this.name} autocomplete="off" spellcheck="false"
              @input=${(e: Event) => (this.name = (e.target as HTMLInputElement).value)} /></label>
            ${ok || !name ? nothing : html`<p class="error">Use lowercase letters, digits and underscores, starting with a letter.</p>`}`}
      <p class="muted">Writes the Dataset with ${this.regenerating && !this.boards ? 'its current Boards' : boards} into the data
        directory, then asks the agent to load it. SQL only, no model.</p>
      <button type="button" class="primary export" ?disabled=${this.busy || !ok} @click=${() => void this.exportNow()}>
        ${this.busy ? 'Exporting…' : 'Export'}</button>
      ${this.reasons.length
        ? html`<div class="refused" role="alert"><p>Export refused:</p><ul>${this.reasons.map(r => html`<li>${r}</li>`)}</ul></div>`
        : nothing}
    </section>`;
  }

  private renderFooter() {
    if (this.step === 'database' || this.exported) return nothing;
    const at = WIZARD_STEPS.findIndex(s => s.key === this.step);
    const back = at > (this.regenerating ? 1 : 0)
      ? html`<button type="button" class="back" ?disabled=${this.running || this.boardsRunning}
          @click=${() => void this.goTo(WIZARD_STEPS[at - 1]!.key)}>Back</button>`
      : html`<span></span>`;
    let next: unknown = nothing;
    let blockers: string[] = [];
    if (this.step === 'draft') {
      next = html`<button type="button" class="primary next" ?disabled=${!this.draft || this.running}
        @click=${() => this.go('review')}>Next</button>`;
    } else if (this.step === 'review') {
      blockers = this.reviewBlockers;
      next = html`<button type="button" class="primary next" ?disabled=${blockers.length > 0}
        @click=${() => void this.leaveReview()}>Next</button>`;
    } else if (this.step === 'boards') {
      blockers = boardProblems(this.boards).length ? ['A Board has problems: fix them above, or draft the Boards again.'] : [];
      next = this.boards
        ? html`<button type="button" class="primary next" ?disabled=${blockers.length > 0 || this.boardsRunning}
            @click=${() => this.go('export')}>Next</button>`
        : this.regenerating
          ? html`<button type="button" class="keep-boards" ?disabled=${this.busy || this.boardsRunning}
              @click=${() => void this.keepBoards()}>Keep current Boards</button>`
          : html`<button type="button" class="skip-boards" ?disabled=${this.boardsRunning}
              @click=${() => this.go('export')}>Skip Boards</button>`;
    }
    return html`<footer class="wizard-footer">
      ${back}
      ${blockers.length ? html`<ul class="blockers">${blockers.map(b => html`<li>${b}</li>`)}</ul>` : html`<span class="spacer"></span>`}
      ${next}
    </footer>`;
  }

  private renderStep() {
    switch (this.step) {
      case 'database':
        return this.renderDatabase();
      case 'draft':
        return this.renderDraft();
      case 'review':
        return nothing; // rendered by render(), so it survives the later steps
      case 'boards':
        return this.renderBoards();
      case 'export':
        return this.renderExport();
    }
  }

  override render() {
    return html`<div class="wizard">
      <header class="wizard-head">
        <div>
          <h1>${this.regenerating ? html`Regenerate <strong>${this.regenerating}</strong>` : 'New Workspace'}</h1>
          <p class="muted">${this.regenerating ? 'Same database and name; replaced files are backed up.' : 'From a read-only database login to a Workspace you can ask questions in.'}</p>
        </div>
        ${this.exported ? nothing : html`<button type="button" class="discard" ?disabled=${this.running || this.boardsRunning}
          @click=${() => this.discard()}>${this.draft ? 'Discard Draft' : 'Cancel'}</button>`}
      </header>
      ${this.renderSteps()}
      ${this.asking
        ? confirmCard({message: this.asking.message, confirm: this.asking.confirm, tone: 'danger',
            onConfirm: () => { const a = this.asking!; this.asking = null; void a.run(); },
            onCancel: () => (this.asking = null)})
        : nothing}
      ${this.notice ? html`<p class="notice-wizard" role="status">${this.notice}</p>` : nothing}
      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : nothing}
      <div class="wizard-body" data-step=${this.step}>
        ${this.renderReview()}
        ${this.renderStep()}
      </div>
      ${this.renderFooter()}
    </div>`;
  }
}
