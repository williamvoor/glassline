import { LitElement, html } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import { confirmCard } from '../cards.js';
import { modelChip } from '../model-chip.js';
import { Api } from './api.js';
import { runCard, usd } from './run-card.js';
import type { BoardFile, BoardPreview, BoardsState, DraftEvent, Estimate } from './types.js';

const fmt = new Intl.NumberFormat('en');

/**
 * Wizard step 4: draft the Boards (the button carries the cost ceiling), edit each as TOML, see its
 * problems and its preview. Fires `estimate` and `draft-boards` with {model, effort}, and
 * `boards-changed` with the state after each re-validation. Skip and Keep are the wizard's buttons.
 */
@customElement('bs-boards')
export class BsBoards extends LitElement {
  @property({ attribute: false }) api: Api = new Api();
  @property({ attribute: false }) boards: BoardsState | null = null;
  @property({ attribute: false }) estimate: Estimate | null = null;
  @property({ attribute: false }) events: DraftEvent[] = [];
  @property({ type: Boolean }) running = false;
  @property() model = '';
  @property() effort = 'low';
  /** Milliseconds between the last edit and re-validation; tests set 0. */
  @property({ type: Number }) validateDelay = 400;

  @state() files: BoardFile[] = [];
  @state() tab = '';
  @state() error = '';
  @state() asking = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private validateSeq = 0;
  private saving: Promise<boolean> = Promise.resolve(true);
  private savingFiles: BoardFile[] | undefined;
  private loadedFrom: BoardsState | null = null;

  protected override createRenderRoot() {
    return this;
  }

  override connectedCallback() {
    super.connectedCallback();
    this.fire('estimate');
  }

  override willUpdate() {
    if (this.boards && this.boards !== this.loadedFrom) {
      this.loadedFrom = this.boards;
      // Boards from outside (a draft or re-draft) replace the text. This component's own
      // re-validation replies set `loadedFrom` first, so they never land here and newer edits stay.
      this.files = this.boards.boards.map((b) => ({ ...b }));
      if (!this.files.some((b) => b.name === this.tab)) this.tab = this.files[0]?.name ?? '';
    }
  }

  private fire(name: 'estimate' | 'draft-boards') {
    this.dispatchEvent(new CustomEvent(name, { detail: { model: this.model, effort: this.effort } }));
  }

  private edit(name: string, toml: string) {
    this.files = this.files.map((b) => (b.name === name ? { ...b, toml } : b));
    ++this.validateSeq;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.validate(), this.validateDelay);
  }

  /** Re-check and re-preview (which also stores the edits on the server). */
  validate(): Promise<boolean> {
    clearTimeout(this.timer);
    if (this.savingFiles === this.files) return this.saving;
    const files = this.files;
    this.savingFiles = files;
    const seq = ++this.validateSeq;
    // Serial writes also order server persistence; ignoring old replies alone cannot do that.
    this.saving = this.saving.then(async () => {
      if (!this.isConnected || seq !== this.validateSeq) return false;
      try {
        const state = await this.api.validateBoards(files);
        if (seq === this.validateSeq && this.isConnected) {
          this.error = '';
          this.loadedFrom = state;
          this.boards = state;
          this.dispatchEvent(new CustomEvent('boards-changed', { detail: state }));
        }
        return true;
      } catch (e) {
        if (seq === this.validateSeq && this.isConnected) this.error = e instanceof Error ? e.message : String(e);
        return false;
      } finally {
        if (this.savingFiles === files) this.savingFiles = undefined;
      }
    });
    return this.saving;
  }

  /** Save the latest edit, including one made while an older save was running. */
  async leave(): Promise<boolean> {
    let files: BoardFile[];
    do {
      files = this.files;
      const saved = await this.validate();
      if (!saved && files === this.files) return false;
    } while (files !== this.files);
    return true;
  }

  override disconnectedCallback() {
    clearTimeout(this.timer);
    ++this.validateSeq;
    super.disconnectedCallback();
  }

  private renderPreview(p: BoardPreview | undefined) {
    if (!p) return html`<p class="muted preview-none">No preview: fix the problems above first.</p>`;
    if (p.skipped) return html`<p class="muted preview-skipped">${p.skipped}</p>`;
    const key = p.key ? html` with the key <code>${p.key.value}</code> from <code>${p.key.source}</code>` : '';
    return html`<div class="preview">
      ${p.error
        ? html`<p class="error">${p.error}</p>`
        : html`<p class="cold ${p.verdict}">One cold render${key}: ${p.seconds?.toFixed(2)} s (target under ${p.target_s} s)${p.verdict === 'slow' ? ', too slow' : ''}.</p>
            <table class="visuals">
              <thead><tr><th scope="col">Visual</th><th scope="col">Macro</th><th scope="col">Query</th><th scope="col">Result</th></tr></thead>
              <tbody>${(p.visuals ?? []).map((v) => html`<tr class=${v.verdict}><th scope="row"><code>${v.id}</code></th><td>${v.macro}</td>
                <td><code>${v.query}</code></td><td>${v.verdict === 'no_rows' ? 'No rows' : v.verdict}</td></tr>`)}</tbody>
            </table>`}
      ${Object.entries(p.queries ?? {}).map(([name, q]) =>
        'error' in q
          ? html`<h4><code>${name}</code></h4><p class="error query-error">${q.error} (${q.ms} ms)</p>`
          : html`<h4><code>${name}</code>: ${fmt.format(q.rows)} rows${q.truncated ? ' (truncated)' : ''}, ${q.ms} ms</h4>
              <table class="rows">
                <thead><tr>${q.columns.map((c) => html`<th scope="col">${c}</th>`)}</tr></thead>
                <tbody>${q.first_rows.map((r) => html`<tr>${r.map((v) => html`<td>${v}</td>`)}</tr>`)}</tbody>
              </table>`,
      )}
    </div>`;
  }

  override render() {
    const s = this.boards;
    const current = this.files.find((b) => b.name === this.tab);
    const problems = current ? (s?.problems[current.name] ?? []) : [];
    const estimate = this.estimate?.model === this.model ? this.estimate : null; // never a stale ceiling (S5)
    const ceiling = estimate ? ` (at most ${usd(estimate.run_ceiling_usd)})` : '';
    return html`<section class="glass" aria-labelledby="boards-h">
      <h2 id="boards-h">Boards</h2>
      <p class="lede">The Explorer Board and up to a few Entity Boards, drafted from the reviewed files. Each is checked by the
        agent's own startup check and previewed with one cold render. The Workspace opens on its Explorer Board.</p>
      <div class="row">${modelChip(this.model)}</div>
      ${estimate
        ? html`<p class="estimate">At most ${usd(estimate.first_call_usd)} for the first call and
            ${usd(estimate.run_ceiling_usd)} if all ${estimate.max_calls} calls are used
            (~${fmt.format(estimate.input_tokens_est)} input tokens each). These are ceilings, so a run usually costs much less.</p>`
        : html`<p class="estimate muted">Working out the cost ceiling…</p>`}
      ${this.asking
        ? confirmCard({
            message: 'This replaces the Boards and your edits to them.',
            confirm: `Draft the Boards again${ceiling}`,
            onConfirm: () => { this.asking = false; this.fire('draft-boards'); },
            onCancel: () => (this.asking = false),
          })
        : html`<div class="row"><button class="${s ? '' : 'primary '}draft-boards" ?disabled=${this.running || !estimate}
            @click=${() => (s ? (this.asking = true) : this.fire('draft-boards'))}>
            ${this.running ? 'Drafting Boards…' : `${s ? 'Draft the Boards again' : 'Draft Boards'}${ceiling}`}</button></div>`}
      ${runCard({ model: this.running ? this.model : s?.model ?? this.model, events: this.events, running: this.running,
        estimate: this.estimate, done: this.running || !s?.model ? null : s.usage?.totals ?? null, what: 'Drafting the Boards' })}
      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : ''}
      ${s && s.set_problems.length
        ? html`<ul class="errors set-problems">${s.set_problems.map((p) => html`<li>${p}</li>`)}</ul>`
        : ''}
      ${this.files.length
        ? html`<div role="tablist" aria-label="Boards">
              ${this.files.map((b) => {
                const bad = (s?.problems[b.name] ?? []).length > 0;
                return html`<button role="tab" class="tab ${bad ? 'bad' : ''}" id="board-tab-${b.name}" aria-controls="board-panel"
                  aria-selected=${this.tab === b.name ? 'true' : 'false'} @click=${() => (this.tab = b.name)}>
                  ${b.name}.toml${bad ? ' (problems)' : ''}</button>`;
              })}
            </div>
            ${current
              ? html`<div role="tabpanel" id="board-panel" aria-labelledby="board-tab-${current.name}">
                  <p class="why">${current.why}</p>
                  <textarea class="mono file board-toml" rows="20" aria-label="${current.name}.toml" .value=${current.toml}
                    @input=${(e: Event) => this.edit(current.name, (e.target as HTMLTextAreaElement).value)}></textarea>
                  ${problems.length
                    ? html`<ul class="errors board-problems">${problems.map((p) => html`<li>${p}</li>`)}</ul>`
                    : html`<p class="ok">Passes the agent's checks.</p>`}
                  ${problems.length ? '' : this.renderPreview(s?.preview[current.name])}
                </div>`
              : ''}`
        : ''}
    </section>`;
  }
}
