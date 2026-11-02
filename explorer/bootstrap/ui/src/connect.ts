import { LitElement, html } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import type { Connection } from './types.js';

/** Step 1: which database, and which schemas to report on. Fires `run-report` {schemas}. */
@customElement('bs-connect')
export class BsConnect extends LitElement {
  @property({ attribute: false }) connection: Connection | null = null;
  @property({ type: Boolean }) busy = false;
  @state() private chosen = new Set<string>();

  protected override createRenderRoot() {
    return this;
  }

  private toggle(name: string, on: boolean) {
    const next = new Set(this.chosen);
    if (on) next.add(name);
    else next.delete(name);
    this.chosen = next;
  }

  private run() {
    const schemas = [...this.chosen].sort();
    this.dispatchEvent(new CustomEvent('run-report', { detail: { schemas }, bubbles: true }));
  }

  override render() {
    const c = this.connection;
    if (!c) return html`<p>Connecting…</p>`;
    return html`
      <section aria-labelledby="connect-h">
        <h2 id="connect-h">Connect</h2>
        <p>Database <strong>${c.name}</strong> on <strong>${c.host}</strong></p>
        <fieldset>
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
        </fieldset>
        <button class="primary" ?disabled=${this.busy || this.chosen.size === 0} @click=${this.run}>
          ${this.busy ? 'Running report…' : 'Run report'}
        </button>
      </section>
    `;
  }
}
