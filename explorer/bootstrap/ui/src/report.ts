import { LitElement, html } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import { EFFORTS, MODELS } from './types.js';
import type { ColumnReport, DraftEvent, Estimate, ObjectReport, Report } from './types.js';

const fmt = new Intl.NumberFormat('en');

export function usd(n: number): string {
  return `$${n < 1 ? n.toFixed(4) : n.toFixed(2)}`;
}

function commented(o: ObjectReport): number {
  return (o.comment ? 1 : 0) + o.columns.filter((c) => c.comment).length;
}

/** Objects and columns to show: everything, or only what lacks a comment. */
export function visibleObjects(report: Report, onlyMissing: boolean): { object: ObjectReport; columns: ColumnReport[] }[] {
  return report.objects
    .map((object) => ({ object, columns: onlyMissing ? object.columns.filter((c) => !c.comment) : object.columns }))
    .filter(({ object, columns }) => !onlyMissing || !object.comment || columns.length > 0);
}

/** Step 2: the report, and starting a draft. Fires `estimate` and `draft` with {model, effort}. */
@customElement('bs-report')
export class BsReport extends LitElement {
  @property({ attribute: false }) report: Report | null = null;
  @property({ attribute: false }) estimate: Estimate | null = null;
  @property({ attribute: false }) events: DraftEvent[] = [];
  @property({ type: Boolean }) running = false;
  @state() onlyMissing = false;
  @state() model: string = MODELS[0];
  @state() effort = 'low';

  protected override createRenderRoot() {
    return this;
  }

  override firstUpdated() {
    this.fire('estimate');
  }

  private fire(name: 'estimate' | 'draft') {
    this.dispatchEvent(new CustomEvent(name, { detail: { model: this.model, effort: this.effort }, bubbles: true }));
  }

  private totals() {
    let cost = 0;
    let calls = 0;
    for (const e of this.events) {
      if (e.type === 'call') {
        cost += e.usage.cost_usd;
        calls += 1;
      }
    }
    return { cost, calls };
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

  override render() {
    const r = this.report;
    if (!r) return html``;
    const s = r.summary;
    const over = s.context_chars > s.context_target;
    const { cost, calls } = this.totals();
    return html`
      <section aria-labelledby="report-h">
        <h2 id="report-h">Report</h2>
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

        <h3>Draft</h3>
        <div class="row">
          <label>Model
            <select class="model" @change=${(e: Event) => { this.model = (e.target as HTMLSelectElement).value; this.fire('estimate'); }}>
              ${MODELS.map((m) => html`<option .value=${m} ?selected=${m === this.model}>${m}</option>`)}
            </select>
          </label>
          <label>Effort
            <select class="effort" @change=${(e: Event) => { this.effort = (e.target as HTMLSelectElement).value; this.fire('estimate'); }}>
              ${EFFORTS.map((x) => html`<option .value=${x} ?selected=${x === this.effort}>${x}</option>`)}
            </select>
          </label>
          <button class="primary draft" ?disabled=${this.running} @click=${() => this.fire('draft')}>
            ${this.running ? 'Drafting…' : `Draft with ${this.model}`}
          </button>
        </div>
        ${this.estimate
          ? html`<p class="estimate">
              Rough ceiling: ${usd(this.estimate.first_call_usd)} for the first call, ${usd(this.estimate.run_ceiling_usd)} if all
              ${this.estimate.max_calls} calls are used (~${fmt.format(this.estimate.input_tokens_est)} input tokens each). It
              ignores the cache discount and the input that query results add; the actual cost is shown as it runs.
            </p>`
          : ''}
        ${this.events.length
          ? html`<ol class="progress" aria-live="polite">
              ${this.events.map((e) =>
                e.type === 'sql'
                  ? html`<li>run_sql: ${e.purpose}</li>`
                  : e.type === 'call'
                    ? html`<li class="muted">model call: ${fmt.format(e.usage.prompt_tokens)} prompt (${fmt.format(e.usage.cached_tokens)} cached),
                        ${fmt.format(e.usage.completion_tokens)} completion, ${usd(e.usage.cost_usd)}</li>`
                    : html`<li class="warn">draft rejected (${e.problems.length} problems); the model is fixing it</li>`,
              )}
            </ol>
            <p class="totals">${calls} calls, ${usd(cost)} so far</p>`
          : ''}
      </section>
    `;
  }
}
