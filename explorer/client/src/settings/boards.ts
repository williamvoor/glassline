import { LitElement, html } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import { Api } from './api.js';
import type { BoardFile, BoardPreview, BoardsState, DraftEvent, Estimate } from './types.js';

const usd = (n: number) => `$${n.toFixed(n < 1 ? 4 : 2)}`;
const fmt = new Intl.NumberFormat('en');

/**
 * Step 4 (phase 3): draft the Boards, edit each as TOML, see its problems and its preview.
 * Fires `estimate` and `draft-boards` with {model, effort}, `boards-changed` with the state after
 * each re-validation, `back`, and `to-export` (Review's Export section, once there are Boards).
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
  private timer: ReturnType<typeof setTimeout> | undefined;
  private validateSeq = 0;
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
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.validate(), this.validateDelay);
  }

  /** Re-check and re-preview (which also stores the edits on the server). */
  async validate() {
    const seq = ++this.validateSeq;
    try {
      const state = await this.api.validateBoards(this.files);
      if (seq !== this.validateSeq) return; // a reply to older edits
      this.error = '';
      this.loadedFrom = state; // the text on screen is already the newest
      this.boards = state;
      this.dispatchEvent(new CustomEvent('boards-changed', { detail: state }));
    } catch (e) {
      if (seq === this.validateSeq) this.error = e instanceof Error ? e.message : String(e);
    }
  }

  private renderProgress() {
    if (!this.events.length) return '';
    const calls = this.events.filter((e) => e.type === 'call');
    const cost = calls.reduce((s, e) => s + (e.type === 'call' ? e.usage.cost_usd : 0), 0);
    return html`<ol class="progress" aria-live="polite">
        ${this.events.map((e) =>
          e.type === 'sql'
            ? html`<li>run_sql: ${e.purpose}</li>`
            : e.type === 'call'
              ? html`<li class="muted">model call: ${fmt.format(e.usage.prompt_tokens)} prompt (${fmt.format(e.usage.cached_tokens)} cached),
                  ${fmt.format(e.usage.completion_tokens)} completion, ${usd(e.usage.cost_usd)}</li>`
              : html`<li class="warn">Boards rejected (${e.problems.length} problems); the model is fixing them</li>`,
        )}
      </ol>
      <p class="totals">${calls.length} calls, ${usd(cost)} so far</p>`;
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
    return html`<section aria-labelledby="boards-h">
      <h2 id="boards-h">Boards</h2>
      <p>The Explorer Board and up to a few Entity Boards, drafted from the reviewed files. Each is checked by the
        agent's own startup check and previewed with one cold render. Export writes them with the Dataset, and the
        Workspace then opens on its Explorer Board.</p>
      <div class="row">
        <button class="${s ? '' : 'primary '}draft-boards" ?disabled=${this.running} @click=${() => this.fire('draft-boards')}>
          ${this.running ? 'Drafting Boards…' : s ? `Draft the Boards again with ${this.model}` : `Draft Boards with ${this.model}`}
        </button>
        ${s && !this.running
          ? html`<button class="primary to-export" @click=${() => this.dispatchEvent(new CustomEvent('to-export'))}>Continue to Export</button>`
          : ''}
        <button class="back" @click=${() => this.dispatchEvent(new CustomEvent('back'))}>Back to Review</button>
      </div>
      ${this.estimate
        ? html`<p class="estimate">Rough ceiling: ${usd(this.estimate.first_call_usd)} for the first call,
            ${usd(this.estimate.run_ceiling_usd)} if all ${this.estimate.max_calls} calls are used
            (~${fmt.format(this.estimate.input_tokens_est)} input tokens each).</p>`
        : ''}
      ${this.renderProgress()}
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
