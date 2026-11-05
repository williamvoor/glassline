import { LitElement, html } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import type { Api } from './api.js';
import type { Connection, SchemaDump } from './types.js';

export type ReportSelection = { schemas: string[] } | { dataset: string };

/** Step 1: which database, and what to report on. Fires `run-report` with a ReportSelection.
 *
 * An existing dataset on the server (a plain name: the directory stays server-side) or,
 * when there is none yet, hand-picked schemas. The client never touches the filesystem.
 * Also tests the connection on demand and reads/dumps the visible schema.
 */
@customElement('bs-connect')
export class BsConnect extends LitElement {
  @property({ attribute: false }) connection: Connection | null = null;
  @property({ attribute: false }) api!: Api;
  @property({ type: Array }) datasets: string[] = [];
  @property({ type: Boolean }) busy = false;
  @state() private mode: 'dataset' | 'schemas' | null = null;
  @state() private chosen = new Set<string>();
  @state() private picked = '';
  @state() private connTest: string | null = null;
  @state() private schema: SchemaDump | null = null;
  @state() private schemaOpen = false;
  @state() private schemaBusy = false;

  protected override createRenderRoot() {
    return this;
  }

  /** Schemas mode when the server holds no datasets; otherwise the dataset picked so far. */
  private get effectiveMode(): 'dataset' | 'schemas' {
    if (this.datasets.length === 0) return 'schemas';
    return this.mode ?? 'dataset';
  }

  private get effectiveDataset(): string {
    return this.picked || this.datasets[0] || '';
  }

  private toggle(name: string, on: boolean) {
    const next = new Set(this.chosen);
    if (on) next.add(name);
    else next.delete(name);
    this.chosen = next;
  }

  private run() {
    const detail: ReportSelection =
      this.effectiveMode === 'dataset'
        ? { dataset: this.effectiveDataset }
        : { schemas: [...this.chosen].sort() };
    this.dispatchEvent(new CustomEvent('run-report', { detail, bubbles: true }));
  }

  private get canRun() {
    return this.effectiveMode === 'dataset' ? this.effectiveDataset !== '' : this.chosen.size > 0;
  }

  private async testConnection() {
    this.connTest = 'Testing…';
    const t0 = performance.now();
    try {
      const c = await this.api.connection();
      const n = c.schemas.length;
      this.connTest = `Connection OK: ${c.name} on ${c.host} — ${n} schema${n === 1 ? '' : 's'} visible (${Math.round(performance.now() - t0)} ms)`;
    } catch (e) {
      this.connTest = `Connection failed: ${e instanceof Error ? e.message : String(e)}`;
    }
  }

  private async toggleSchema() {
    this.schemaOpen = !this.schemaOpen;
    if (!this.schemaOpen || this.schema || this.schemaBusy) return;
    this.schemaBusy = true;
    try {
      this.schema = await this.api.schema();
    } catch (e) {
      this.schema = null;
      this.schemaOpen = true;
      this.connTest = `Reading the schema failed: ${e instanceof Error ? e.message : String(e)}`;
    } finally {
      this.schemaBusy = false;
    }
  }

  private downloadSchema() {
    if (!this.schema) return;
    const blob = new Blob([JSON.stringify(this.schema, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'schema.json';
    a.click();
    URL.revokeObjectURL(url);
  }

  private renderSchema() {
    if (!this.schemaOpen) return html``;
    if (this.schemaBusy) return html`<p>Reading the schema…</p>`;
    if (!this.schema) return html`<p>No schema loaded.</p>`;
    if (this.schema.schemas.length === 0) return html`<p>This login can't see any tables or views.</p>`;
    return html`
      <button @click=${this.downloadSchema}>Download schema (JSON)</button>
      ${this.schema.schemas.map(
        (s) => html`<details open>
          <summary><code>${s.name}</code> <span class="muted">(${s.tables.length} tables and views)</span></summary>
          ${s.tables.length === 0
            ? html`<p class="muted">Nothing visible in this schema.</p>`
            : html`<ul>
                ${s.tables.map(
                  (t) => html`<li>
                    <code>${t.name}</code> <span class="muted">${t.kind}</span>
                    ${t.comment ? html`<div>${t.comment}</div>` : html`<div class="missing">no comment</div>`}
                    <ul>
                      ${t.columns.map(
                        (c) => html`<li>
                          <code>${c.name}</code> <span class="muted">${c.type}${c.nullable ? '' : ', not null'}</span>
                          ${c.comment ? html` — ${c.comment}` : html` <span class="missing">no comment</span>`}
                        </li>`,
                      )}
                    </ul>
                  </li>`,
                )}
              </ul>`}
        </details>`,
      )}
    `;
  }

  override render() {
    const c = this.connection;
    if (!c) return html`<p>Connecting…</p>`;
    const mode = this.effectiveMode;
    return html`
      <section class="glass" aria-labelledby="connect-h">
        <h2 id="connect-h">Schemas</h2>
        <p>Database <strong>${c.name}</strong> on <strong>${c.host}</strong></p>
        <p>
          <button @click=${() => void this.testConnection()}>Test connection</button>
          ${this.connTest ? html`<span class="status" role="status">${this.connTest}</span>` : ''}
        </p>
        ${this.datasets.length > 0
          ? html`<fieldset>
              <legend>Report on</legend>
              <label>
                <input type="radio" name="mode" value="dataset" .checked=${mode === 'dataset'}
                  @change=${() => (this.mode = 'dataset')} />
                Existing dataset
              </label>
              <label>
                <input type="radio" name="mode" value="schemas" .checked=${mode === 'schemas'}
                  @change=${() => (this.mode = 'schemas')} />
                Choose schemas
              </label>
            </fieldset>`
          : ''}
        ${mode === 'dataset'
          ? html`<fieldset>
              <legend>Dataset</legend>
              ${this.datasets.map(
                (d) => html`<label class="dataset">
                  <input type="radio" name="dataset" .value=${d} .checked=${this.effectiveDataset === d}
                    @change=${() => (this.picked = d)} />
                  ${d}
                </label>`,
              )}
            </fieldset>`
          : html`<fieldset>
              <legend>Schemas to include</legend>
              ${c.schemas.length === 0 ? html`<p>This login can't see any schemas.</p>` : ''}
              ${c.schemas.map(
                (s) => html`<label class="schema">
                  <input
                    type="checkbox"
                    name="schema"
                    .value=${s.name}
                    .checked=${this.chosen.has(s.name)}
                    @change=${(e: Event) => this.toggle(s.name, (e.target as HTMLInputElement).checked)}
                  />
                  ${s.name} <span class="muted">(${s.objects} tables and views)</span>
                </label>`,
              )}
            </fieldset>`}
        <p class="muted">Next runs the report: SQL only, no model.</p>
        <button class="primary next run-report" ?disabled=${this.busy || !this.canRun} @click=${this.run}>
          ${this.busy ? 'Running the report…' : 'Next'}
        </button>
        <p>
          <button @click=${() => void this.toggleSchema()}>
            ${this.schemaOpen ? 'Hide schema' : 'Show schema'}
          </button>
        </p>
        ${this.renderSchema()}
      </section>
    `;
  }
}
