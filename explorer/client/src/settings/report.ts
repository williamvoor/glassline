import { LitElement, html } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import { confirmCard } from '../cards.js';
import { modelChip, modelName } from '../model-chip.js';
import { runCard, usd } from './run-card.js';
import { EFFORTS, MODELS } from './types.js';
import type { ColumnReport, Draft, DraftEvent, Estimate, ObjectReport, Report } from './types.js';

const fmt = new Intl.NumberFormat('en');

export { usd };

function commented(o: ObjectReport): number {
  return (o.comment ? 1 : 0) + o.columns.filter((c) => c.comment).length;
}

/** Objects and columns to show: everything, or only what lacks a comment. */
export function visibleObjects(report: Report, onlyMissing: boolean): { object: ObjectReport; columns: ColumnReport[] }[] {
  return report.objects
    .map((object) => ({ object, columns: onlyMissing ? object.columns.filter((c) => !c.comment) : object.columns }))
    .filter(({ object, columns }) => !onlyMissing || !object.comment || columns.length > 0);
}

/** Wizard step 2 (Workspaces SPEC phase 3): start a Draft, whose button carries the cost ceiling, then
 * the report it reads. Fires `estimate` and `draft` with {model, effort}. */
@customElement('bs-report')
export class BsReport extends LitElement {
  @property({ attribute: false }) report: Report | null = null;
  @property({ attribute: false }) estimate: Estimate | null = null;
  @property({ attribute: false }) events: DraftEvent[] = [];
  @property({ type: Boolean }) running = false;
  /** Models the server allows, and which one to start on: both arrive before first render. */
  @property({ type: Array }) models: string[] = [...MODELS];
  @property() defaultModel: string = MODELS[0];
  /** A Draft exists: drafting again asks first, on a card (it replaces the Draft and its Boards). */
  @property({ type: Boolean }) hasDraft = false;
  /** The last finished run's totals (the Draft's usage), for the summary line. */
  @property({ attribute: false }) doneTotals: Draft['usage']['totals'] | null = null;
  /** The model of the finished Draft, when there is one (its summary names it). */
  @property() draftModel = '';
  @state() onlyMissing = false;
  @state() model: string = MODELS[0];
  @state() effort = 'low';
  @state() asking = false;

  protected override createRenderRoot() {
    return this;
  }

  protected override willUpdate() {
    if (!this.models.includes(this.model)) {
      this.model = this.models.includes(this.defaultModel) ? this.defaultModel : (this.models[0] ?? MODELS[0]);
    }
  }

  override firstUpdated() {
    this.fire('estimate');
  }

  private fire(name: 'estimate' | 'draft') {
    this.dispatchEvent(new CustomEvent(name, { detail: { model: this.model, effort: this.effort }, bubbles: true }));
  }

  private renderColumn(c: ColumnReport) {
    return html`<tr>
      <th scope="row"><code>${c.name}</code></th>
      <td><code>${c.type}</code></td>
      <td>${c.nullable ? 'yes' : 'no'}</td>
      <td>${c.null_frac === null ? '–' : `${(c.null_frac * 100).toFixed(1)}%`}</td>
      <td>${c.distinct_estimate === null ? '–' : fmt.format(c.distinct_estimate)}</td>
      <td>${c.most_common.map((m) => m.value).join(', ') || '–'}</td>
      <td>${c.comment ? c.comment : html`<span class="missing">no comment</span>`}</td>
    </tr>`;
  }

  private renderObject(o: ObjectReport, columns: ColumnReport[]) {
    const total = o.columns.length + 1;
    const n = commented(o);
    return html`<table class="object">
      <caption>
        <code>${o.schema}.${o.name}</code> <span class="muted">${o.kind}</span>
        <span class="badge ${n === total ? 'ok' : 'warn'}">${n}/${total} commented</span>
        <span class="muted">${o.est_rows === null ? 'rows unknown' : `~${fmt.format(o.est_rows)} rows`}</span>
        <div>${o.comment ? o.comment : html`<span class="missing">no comment</span>`}</div>
      </caption>
      <thead>
        <tr>
          <th scope="col">Column</th><th scope="col">Type</th><th scope="col">Nullable</th>
          <th scope="col">Null</th><th scope="col">Distinct</th><th scope="col">Top values</th>
          <th scope="col">Comment</th>
        </tr>
      </thead>
      <tbody>${columns.map((c) => this.renderColumn(c))}</tbody>
    </table>`;
  }

  private renderDraft() {
    // Only a ceiling for the model now selected: a stale one must never sit on the button (S5, refuter).
    const est = this.estimate?.model === this.model ? this.estimate : null;
    const ceiling = est ? ` (at most ${usd(est.run_ceiling_usd)})` : '';
    return html`<section class="draft-panel glass" aria-labelledby="draft-h">
      <h2 id="draft-h">Draft the Dataset</h2>
      <p class="lede">The model reads the report below, runs SQL to check what it writes, and drafts the description,
        notes and glossary. Nothing is used until you export.</p>
      <div class="row">
        <label>Model
          <select class="model" ?disabled=${this.running}
            @change=${(e: Event) => { this.model = (e.target as HTMLSelectElement).value; this.fire('estimate'); }}>
            ${this.models.map((m) => html`<option .value=${m} ?selected=${m === this.model}>${modelName(m)}</option>`)}
          </select>
        </label>
        <label>Effort
          <select class="effort" ?disabled=${this.running}
            @change=${(e: Event) => { this.effort = (e.target as HTMLSelectElement).value; this.fire('estimate'); }}>
            ${EFFORTS.map((x) => html`<option .value=${x} ?selected=${x === this.effort}>${x}</option>`)}
          </select>
        </label>
        ${modelChip(this.model)}
      </div>
      ${est
        ? html`<p class="estimate">At most ${usd(est.first_call_usd)} for the first call and ${usd(est.run_ceiling_usd)} if all
            ${est.max_calls} calls are used (~${fmt.format(est.input_tokens_est)} input tokens each). These are ceilings: they
            assume every call uses its full output allowance, so a run usually costs much less.</p>`
        : html`<p class="estimate muted">Working out the cost ceiling…</p>`}
      ${this.asking
        ? confirmCard({
            message: 'This replaces the Draft and its Boards. Your edits are lost.',
            confirm: `Draft again${ceiling}`,
            onConfirm: () => { this.asking = false; this.fire('draft'); },
            onCancel: () => (this.asking = false),
          })
        : html`<button class="primary draft" ?disabled=${this.running || !est}
            @click=${() => (this.hasDraft ? (this.asking = true) : this.fire('draft'))}>
            ${this.running ? 'Drafting…' : `${this.hasDraft ? 'Draft again' : 'Draft'}${ceiling}`}</button>`}
      ${runCard({ model: this.running ? this.model : this.draftModel || this.model, events: this.events, running: this.running,
        estimate: est, done: this.running ? null : this.doneTotals, what: 'Drafting the Dataset' })}
    </section>`;
  }

  override render() {
    const r = this.report;
    if (!r) return html``;
    const s = r.summary;
    const over = s.context_chars > s.context_target;
    return html`
      ${this.renderDraft()}
      <section class="report glass" aria-labelledby="report-h">
        <h2 id="report-h">What the model will read</h2>
        <dl class="summary">
          <div><dt>Objects</dt><dd class="objects">${s.objects}</dd></div>
          <div><dt>Columns</dt><dd class="columns">${s.columns}</dd></div>
          <div><dt>With comments</dt><dd class="coverage">${s.coverage_pct}%</dd></div>
          <div>
            <dt>Context</dt>
            <dd class="context ${over ? 'over' : ''}">
              ${fmt.format(s.context_chars)} / ${fmt.format(s.context_target)} chars${s.context_scope === 'full' ? '' : ' (schema only)'}
            </dd>
          </div>
        </dl>
        <label><input type="checkbox" class="only-missing" .checked=${this.onlyMissing}
          @change=${(e: Event) => (this.onlyMissing = (e.target as HTMLInputElement).checked)} /> Only missing comments</label>
        ${visibleObjects(r, this.onlyMissing).map(({ object, columns }) => this.renderObject(object, columns))}
      </section>
    `;
  }
}
