import { LitElement, html } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import type { Claim } from './types.js';

const LABELS: Record<string, string> = {
  verified: 'verified',
  failed: 'failed',
  error: 'error',
  needs_review: 'needs review',
};

/** The state a claim shows: edited claims must be re-run before their old result counts. */
export function claimState(claim: Claim, edited: boolean): string {
  if (edited) return 'edited';
  return claim.result?.state ?? 'not checked';
}

/**
 * Each claim with its SQL, expectation, actual result and state. Doesn't change claims itself:
 * fires `claim-edit` {index, sql?, expectText?}, `claim-dismiss` {index, dismissed} and `claims-rerun`.
 */
@customElement('bs-claims')
export class BsClaims extends LitElement {
  @property({ attribute: false }) claims: Claim[] = [];
  @property({ attribute: false }) edited: Set<number> = new Set();
  @property({ attribute: false }) badExpect: Set<number> = new Set();
  @property({ type: Boolean }) busy = false;

  protected override createRenderRoot() {
    return this;
  }

  private fire(name: string, detail?: unknown) {
    this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true }));
  }

  private renderClaim(c: Claim, i: number) {
    const state = claimState(c, this.edited.has(i));
    const label = LABELS[state] ?? state;
    return html`<li class="claim ${c.dismissed ? 'dismissed' : ''}" data-index=${i}>
      <div class="row">
        <span class="badge state-${state.replace(' ', '-')}">${label}</span>
        ${c.dismissed ? html`<span class="badge">dismissed</span>` : ''}
        <span class="claim-text">${c.text}</span> <span class="muted">(${c.file})</span>
      </div>
      ${c.result?.message && !this.edited.has(i) ? html`<p class="message">${c.result.message}</p>` : ''}
      <label>SQL
        <textarea class="mono sql" rows="3" .value=${c.sql}
          @input=${(e: Event) => this.fire('claim-edit', { index: i, sql: (e.target as HTMLTextAreaElement).value })}></textarea>
      </label>
      <label>Expectation (JSON)
        <textarea class="mono expect ${this.badExpect.has(i) ? 'invalid' : ''}" rows="2"
          aria-invalid=${this.badExpect.has(i) ? 'true' : 'false'}
          .value=${JSON.stringify(c.expect)}
          @input=${(e: Event) => this.fire('claim-edit', { index: i, expectText: (e.target as HTMLTextAreaElement).value })}></textarea>
      </label>
      ${c.result?.actual && !this.edited.has(i)
        ? html`<details><summary>Actual result</summary><pre class="mono">${JSON.stringify(c.result.actual, null, 1)}</pre></details>`
        : ''}
      <button class="dismiss" @click=${() => this.fire('claim-dismiss', { index: i, dismissed: !c.dismissed })}>
        ${c.dismissed ? 'Undismiss' : 'Dismiss'}
      </button>
    </li>`;
  }

  override render() {
    return html`<section aria-labelledby="claims-h">
      <h3 id="claims-h">Claims (${this.claims.length})</h3>
      <button class="rerun" ?disabled=${this.busy || this.badExpect.size > 0} @click=${() => this.fire('claims-rerun')}>
        ${this.busy ? 'Re-running…' : 'Re-run claims'}
      </button>
      <ol class="claims">${this.claims.map((c, i) => this.renderClaim(c, i))}</ol>
    </section>`;
  }
}
