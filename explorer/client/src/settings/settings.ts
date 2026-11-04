// <explorer-settings>: the bootstrap workflow inside the explorer (specs/workspaces/SPEC.md phase 2,
// ADR 0041, D6). Adapted from explorer/bootstrap/ui/src/app.ts: a Workspaces screen first, then
// connect, report, draft, review, Boards and export, against the bootstrap server at
// VITE_BOOTSTRAP_URL. Every model call asks for confirmation first, naming the model and the
// cost ceiling.
import {LitElement, html} from 'lit';
import {customElement, property, state} from 'lit/decorators.js';

import {Api, ApiError, type SettingsWorkspace} from './api.js';
import './connect.js';
import './report.js';
import './review.js';
import './boards.js';
import type {ReportSelection} from './connect.js';
import {usd} from './report.js';
import {MODELS, boardProblems} from './types.js';
import type {BoardsState, Connection, Draft, DraftEvent, Estimate, Report, Validation} from './types.js';

export type SettingsStep = 'workspaces' | 'connect' | 'report' | 'review' | 'boards';
const STEPS: {key: SettingsStep; label: string}[] = [
  {key: 'workspaces', label: 'Workspaces'},
  {key: 'connect', label: 'Connect'},
  {key: 'report', label: 'Report'},
  {key: 'review', label: 'Review'},
  {key: 'boards', label: 'Boards'},
];

export const BOOTSTRAP_URL = import.meta.env.VITE_BOOTSTRAP_URL ?? 'http://127.0.0.1:8765';

@customElement('explorer-settings')
export class ExplorerSettings extends LitElement {
  @property({attribute: false}) api = new Api(undefined, BOOTSTRAP_URL);
  /** Milliseconds between progress polls while drafting; tests set 0. */
  @property({type: Number}) pollMs = 500;
  /** Replaceable in tests. */
  @property({attribute: false}) confirmFn: (message: string) => boolean = m => window.confirm(m);

  @state() step: SettingsStep = 'workspaces';
  @state() workspaces: SettingsWorkspace[] = [];
  @state() available: string[] = [];
  @state() regenerating = ''; // D5: the Workspace this draft goes back into
  @state() connection: Connection | null = null;
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
  @state() notice = '';
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

  /** Tells the explorer the Workspace list changed (the picker reloads it). */
  private changed() {
    this.dispatchEvent(new CustomEvent('workspaces-changed', {bubbles: true, composed: true}));
  }

  async load() {
    await this.guard(async () => {
      this.workspaces = (await this.api.workspaces()).workspaces;
      this.available = (await this.api.connections()).connections;
    });
    try {
      const m = await this.api.models();
      if (m.models.length > 0) this.models = m.models;
      if (m.models.includes(m.default)) this.defaultModel = m.default;
    } catch {
      // the built-in list
    }
  }

  async connect(dsnEnv: string) {
    this.busy = true;
    await this.guard(async () => {
      this.connection = await this.api.connect(dsnEnv);
      this.regenerating = '';
      this.selection = null; // a report from an earlier login never applies to this one
      this.report = this.draft = this.boards = null;
      this.step = 'connect';
    });
    this.busy = false;
  }

  async deleteWorkspace(ws: SettingsWorkspace) {
    const what = ws.kind === 'data'
      ? `its Dataset and its saved Conversations (${ws.name} in the data directory)`
      : `its saved Conversations (the repo Dataset's files are not touched)`;
    if (!this.confirmFn(`Delete the Workspace ${ws.name}? This deletes ${what}. It can't be undone.`)) return;
    await this.guard(async () => {
      const r = await this.api.deleteWorkspace(ws.name);
      this.workspaces = r.workspaces;
      this.available = (await this.api.connections()).connections;
      this.notice = `Deleted ${ws.name}.`;
      this.changed();
    });
  }

  async regenerate(ws: {name: string}) {
    this.busy = true;
    await this.guard(async () => {
      this.regenerating = '';
      this.selection = null;
      this.report = await this.api.regenerate(ws.name);
      this.regenerating = ws.name;
      this.connection = null;
      this.draft = this.boards = null;
      this.estimate = null;
      this.step = 'report';
    });
    this.busy = false;
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

  /** Review's "Re-run report": a regenerate reports on its Dataset again (refuter F3). */
  async rerunReport() {
    if (this.regenerating) await this.regenerate({name: this.regenerating});
    else if (this.selection) await this.runReport(this.selection);
  }

  /** Reloads the agent's Workspace list, e.g. after an export whose reload failed (D3). */
  async reloadAgent() {
    await this.guard(async () => {
      const r = await this.api.reload();
      this.notice = r.reloaded
        ? 'The agent reloaded its Workspaces.'
        : `The agent didn't reload${r.error ? `: ${r.error}` : r.status === 0 ? ': it is not running' : ''}.`;
      this.changed();
    });
  }

  async refreshEstimate(model: string, effort: string) {
    await this.guard(async () => {
      this.estimate = await this.api.estimate(model, effort);
    });
  }

  /** S5: every model call is confirmed first, with the model and the cost ceiling. */
  private confirmRun(what: string, model: string, estimate: Estimate): boolean {
    return this.confirmFn(
      `${what} with ${model}? This calls the model: about ${usd(estimate.first_call_usd)} for the first call, ` +
        `at most ${usd(estimate.run_ceiling_usd)} if all ${estimate.max_calls} calls are used.`,
    );
  }

  async startDraft(model: string, effort: string) {
    this.model = model;
    this.effort = effort;
    await this.guard(async () => {
      const estimate = await this.api.estimate(model, effort);
      this.estimate = estimate;
      if (!this.confirmRun('Draft the Dataset', model, estimate)) return;
      const {run_id} = await this.api.startDraft(model, effort);
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
          this.boards = null;
          this.step = 'review';
          return;
        }
        await new Promise(ok => setTimeout(ok, this.pollMs));
      }
    });
    this.running = false;
  }

  async backToDraft() {
    await this.guard(async () => {
      const r = await this.api.currentDraft();
      this.draft = r.draft;
      this.validation = r.validation;
      this.step = 'review';
    });
  }

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
      const estimate = await this.api.boardsEstimate(model, effort);
      this.boardsEstimate = estimate;
      if (!this.confirmRun('Draft the Boards', model, estimate)) return;
      const {run_id} = await this.api.startBoards(model, effort);
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
        await new Promise(ok => setTimeout(ok, this.pollMs));
      }
    });
    this.boardsRunning = false;
  }

  private async exported(detail: {workspace?: string; reloaded?: boolean}) {
    this.notice = detail.reloaded
      ? `${detail.workspace} is ready: pick it in the header.`
      : `${detail.workspace} was exported, but the agent didn't reload. Use "Reload the agent" on the Workspaces screen.`;
    this.regenerating = '';
    await this.load();
    this.changed();
  }

  private renderWorkspaces() {
    return html`<section class="settings-workspaces">
      <h2>Workspaces</h2>
      ${this.workspaces.length
        ? html`<table>
            <thead><tr><th>Name</th><th>Login variable</th><th>Dataset</th><th></th></tr></thead>
            <tbody>
              ${this.workspaces.map(ws => html`<tr>
                <td>${ws.name}</td>
                <td><code>${ws.dsn_env}</code></td>
                <td>${ws.kind === 'data' ? 'made in Settings' : html`in the repo <span class="muted">(regenerate it with
                  <code>regen_dataset.sh</code>)</span>`}</td>
                <td>
                  ${ws.kind === 'data'
                    ? html`<button class="regenerate" ?disabled=${this.busy} @click=${() => void this.regenerate(ws)}>Regenerate</button>`
                    : ''}
                  <button class="delete-workspace" ?disabled=${this.busy} @click=${() => void this.deleteWorkspace(ws)}>Delete</button>
                </td>
              </tr>`)}
            </tbody>
          </table>`
        : html`<p class="muted">No Workspaces yet.</p>`}
      <p><button class="reload-agent" ?disabled=${this.busy} @click=${() => void this.reloadAgent()}>Reload the agent</button>
        <span class="muted">Picks up Workspaces and Dataset files that changed.</span></p>
      <h3>Add a Workspace</h3>
      ${this.available.length
        ? html`<p>Choose the read-only login for the new Workspace. Logins are variables in the repo's
              <code>.env</code>; only their names are shown.</p>
            <ul class="connections">
              ${this.available.map(name => html`<li><button class="connect-var" ?disabled=${this.busy}
                @click=${() => void this.connect(name)}>${name}</button></li>`)}
            </ul>`
        : html`<p class="muted">Every <code>*_DATABASE_URL</code> variable in <code>.env</code> already has a Workspace.
            Add a new variable with a read-only login, then restart the bootstrap server.</p>`}
    </section>`;
  }

  private renderStep() {
    switch (this.step) {
      case 'workspaces':
        return this.renderWorkspaces();
      case 'connect':
        return html`<bs-connect .connection=${this.connection} .api=${this.api} .datasets=${[]} ?busy=${this.busy}
          @run-report=${(e: CustomEvent<ReportSelection>) => void this.runReport(e.detail)}></bs-connect>`;
      case 'report':
        return html`<bs-report .report=${this.report} .estimate=${this.estimate} .events=${this.events} ?running=${this.running}
          .models=${this.models} .defaultModel=${this.defaultModel}
          @estimate=${(e: CustomEvent<{model: string; effort: string}>) => void this.refreshEstimate(e.detail.model, e.detail.effort)}
          @draft=${(e: CustomEvent<{model: string; effort: string}>) => void this.startDraft(e.detail.model, e.detail.effort)}
        ></bs-report>
        ${this.draft ? html`<button class="to-review" @click=${() => void this.backToDraft()}>Back to the draft</button>` : ''}`;
      case 'review':
        return html`<bs-review .api=${this.api} .draft=${this.draft} .validation=${this.validation}
          .boardProblems=${boardProblems(this.boards)} workspaceMode .fixedName=${this.regenerating}
          @rerun-report=${() => void this.rerunReport()}
          @exported=${(e: CustomEvent<{workspace?: string; reloaded?: boolean}>) => void this.exported(e.detail)}
          @to-boards=${() => void this.toBoards()}></bs-review>`;
      case 'boards':
        return html`<bs-boards .api=${this.api} .boards=${this.boards} .estimate=${this.boardsEstimate} .events=${this.boardEvents}
          ?running=${this.boardsRunning} .model=${this.model || this.defaultModel} .effort=${this.effort}
          @estimate=${(e: CustomEvent<{model: string; effort: string}>) => void this.refreshBoardsEstimate(e.detail.model, e.detail.effort)}
          @draft-boards=${(e: CustomEvent<{model: string; effort: string}>) => void this.startBoards(e.detail.model, e.detail.effort)}
          @boards-changed=${(e: CustomEvent<BoardsState>) => (this.boards = e.detail)}
          @back=${() => void this.backToDraft()}></bs-boards>`;
    }
  }

  override render() {
    return html`<div class="settings">
      <nav aria-label="Settings steps">
        <ol class="steps">
          ${STEPS.map(s => html`<li class=${this.step === s.key ? 'current' : ''} aria-current=${this.step === s.key ? 'step' : 'false'}>
            ${s.key === 'workspaces'
              ? html`<button type="button" class="to-workspaces" @click=${() => {
                  this.step = 'workspaces';
                  void this.load();
                }}>${s.label}</button>`
              : s.label}
          </li>`)}
        </ol>
      </nav>
      ${this.regenerating ? html`<p class="regenerating">Regenerating <strong>${this.regenerating}</strong>.</p>` : ''}
      ${this.notice ? html`<p class="ok notice-settings" role="status">${this.notice}</p>` : ''}
      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : ''}
      ${this.renderStep()}
    </div>`;
  }
}
