import { LitElement, html } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import { Api, ApiError } from './api.js';
import './claims.js';
import type { Claim, Draft, DraftComment, Expect, Files, Validation } from './types.js';

const TABS: { key: keyof Files; label: string }[] = [
  { key: 'dataset_toml', label: 'dataset.toml' },
  { key: 'notes_md', label: 'notes.md' },
  { key: 'glossary_md', label: 'glossary.md' },
];

/** Why the Review step can't be left yet (empty = it can). Workspaces SPEC phase 3, E3. */
export function reviewBlockers(opts: { validation: Validation | null; claims: Claim[]; edited: Set<number> }): string[] {
  const out: string[] = [];
  if (!opts.validation) out.push('The files have not been validated yet.');
  else if (opts.validation.errors.length) out.push('The files do not validate.');
  if (opts.edited.size) out.push('Re-run the edited claims.');
  opts.claims.forEach((c, i) => {
    const state = c.result?.state;
    if (!c.dismissed && !opts.edited.has(i) && (state === 'failed' || state === 'error' || !state)) {
      out.push(`Claim ${i + 1} is ${state ?? 'not checked'}: fix and re-run, or dismiss it.`);
    }
  });
  return out;
}

/** The Workspace name a draft's dataset.toml suggests. */
export function suggestedName(draft: Draft | null): string {
  return draft?.files.dataset_toml.match(/^name\s*=\s*"([a-z][a-z0-9_]*)"/m)?.[1] ?? '';
}

/** Wizard step 3: edit every drafted file, the comments and the claims. Fires `review-blockers`
 * (string[]) when they change; the wizard's Next calls `leave()`, and Export uses `exportPayload()`. */
@customElement('bs-review')
export class BsReview extends LitElement {
  @property({ attribute: false }) api: Api = new Api();
  @property({ attribute: false }) draft: Draft | null = null;
  @property({ attribute: false }) validation: Validation | null = null;
  /** Milliseconds between the last edit and validation; tests set 0. */
  @property({ type: Number }) validateDelay = 400;

  @state() files: Files = { dataset_toml: '', notes_md: '', glossary_md: '' };
  @state() comments: DraftComment[] = [];
  @state() claims: Claim[] = [];
  @state() edited = new Set<number>();
  @state() badExpect = new Set<number>();
  @state() tab: keyof Files = 'dataset_toml';
  @state() busy = false;
  @state() error = '';
  private timer: ReturnType<typeof setTimeout> | undefined;
  private validateSeq = 0;
  private saving: Promise<boolean> = Promise.resolve(true);
  private loadedFrom: Draft | null = null;
  private lastBlockers = '';

  protected override createRenderRoot() {
    return this;
  }

  override willUpdate() {
    if (this.draft && this.draft !== this.loadedFrom) {
      this.loadedFrom = this.draft;
      this.files = { ...this.draft.files };
      this.comments = this.draft.comments.map((c) => ({ ...c }));
      this.claims = this.draft.claims.map((c) => ({ ...c }));
      this.edited = new Set();
      this.badExpect = new Set();
    }
  }

  override updated() {
    const blockers = reviewBlockers({ validation: this.validation, claims: this.claims, edited: this.edited });
    const key = JSON.stringify(blockers);
    if (key !== this.lastBlockers) {
      this.lastBlockers = key;
      this.dispatchEvent(new CustomEvent('review-blockers', { detail: blockers, bubbles: true }));
    }
  }

  /** Before leaving the step: save pending edits on the server (validate stores them). */
  async leave(): Promise<boolean> {
    let files: Files, comments: DraftComment[];
    do {
      files = this.files;
      comments = this.comments;
      const saved = await this.validate();
      if (!saved && files === this.files && comments === this.comments) return false;
    } while (files !== this.files || comments !== this.comments);
    return true;
  }

  /** What Export sends for the Dataset: the edited files and comments, and the dismissed claims. */
  exportPayload(): { files: Files; comments: DraftComment[]; dismissed: number[] } {
    return { files: this.files, comments: this.comments, dismissed: this.claims.flatMap((c, i) => (c.dismissed ? [i] : [])) };
  }

  private scheduleValidate() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.validate(), this.validateDelay);
  }

  /** Validate (which also stores the edits on the server). False when the request failed. */
  validate(): Promise<boolean> {
    clearTimeout(this.timer);
    const seq = ++this.validateSeq;
    const files = this.files, comments = this.comments;
    this.saving = this.saving.then(async () => {
      if (!this.isConnected || seq !== this.validateSeq) return false;
      try {
        const v = await this.api.validate(files, comments);
        if (seq === this.validateSeq && this.isConnected) {
          this.validation = v;
          this.error = '';
        }
        return true;
      } catch (e) {
        if (seq === this.validateSeq && this.isConnected) this.error = e instanceof Error ? e.message : String(e);
        return false;
      }
    });
    return this.saving;
  }

  override disconnectedCallback() {
    clearTimeout(this.timer);
    ++this.validateSeq;
    super.disconnectedCallback();
  }

  private editFile(key: keyof Files, value: string) {
    this.files = { ...this.files, [key]: value };
    this.scheduleValidate();
  }

  private editComment(i: number, patch: Partial<DraftComment>) {
    this.comments = this.comments.map((c, j) => (j === i ? { ...c, ...patch } : c));
    this.scheduleValidate();
  }

  private onClaimEdit(e: CustomEvent<{ index: number; sql?: string; expectText?: string }>) {
    const { index, sql, expectText } = e.detail;
    const claim = { ...this.claims[index] };
    const bad = new Set(this.badExpect);
    if (sql !== undefined) claim.sql = sql;
    if (expectText !== undefined) {
      try {
        claim.expect = JSON.parse(expectText) as Expect;
        bad.delete(index);
      } catch {
        bad.add(index);
      }
    }
    this.claims = this.claims.map((c, j) => (j === index ? claim : c));
    this.edited = new Set(this.edited).add(index);
    this.badExpect = bad;
  }

  private onDismiss(e: CustomEvent<{ index: number; dismissed: boolean }>) {
    this.claims = this.claims.map((c, j) => (j === e.detail.index ? { ...c, dismissed: e.detail.dismissed } : c));
  }

  async rerunClaims() {
    this.busy = true;
    this.error = '';
    try {
      this.claims = (await this.api.verify(this.claims)).claims;
      this.edited = new Set();
    } catch (e) {
      this.error = e instanceof ApiError && e.reasons.length ? e.reasons.join('; ') : String((e as Error).message);
    } finally {
      this.busy = false;
    }
  }

  private renderComments() {
    return html`<table class="comments">
      <caption>Comments (${this.comments.filter((c) => c.accepted).length} of ${this.comments.length} accepted)</caption>
      <thead><tr><th scope="col">Accept</th><th scope="col">Object / column</th><th scope="col">Kind</th><th scope="col">Comment</th></tr></thead>
      <tbody>
        ${this.comments.map((c, i) => {
          const where = c.column ? `${c.object}.${c.column}` : c.object;
          return html`<tr class="comment ${c.kind}">
            <td><input type="checkbox" class="accept" aria-label="Accept the comment for ${where}" .checked=${c.accepted}
              @change=${(e: Event) => this.editComment(i, { accepted: (e.target as HTMLInputElement).checked })} /></td>
            <th scope="row"><code>${where}</code></th>
            <td>${c.kind === 'rewrite' ? html`<span class="badge warn">rewrite</span>` : 'new'}</td>
            <td><textarea class="mono" rows="2" aria-label="Comment for ${where}" .value=${c.comment}
              @input=${(e: Event) => this.editComment(i, { comment: (e.target as HTMLTextAreaElement).value })}></textarea></td>
          </tr>`;
        })}
      </tbody>
    </table>`;
  }

  override render() {
    if (!this.draft) return html``;
    const v = this.validation;
    return html`<section class="glass" aria-labelledby="review-h">
      <h2 id="review-h">Review the Draft</h2>
      <p class="lede">Edit what the model wrote. Claims are checked against the database with SQL, with no model.</p>
      <div role="tablist" aria-label="Drafted files">
        ${TABS.map(
          (t) => html`<button role="tab" class="tab" id="tab-${t.key}" aria-controls="file-panel"
            aria-selected=${this.tab === t.key ? 'true' : 'false'} @click=${() => (this.tab = t.key)}>${t.label}</button>`,
        )}
      </div>
      <div role="tabpanel" id="file-panel" aria-labelledby="tab-${this.tab}">
        <textarea class="mono file" rows="16" aria-label=${TABS.find((t) => t.key === this.tab)!.label} .value=${this.files[this.tab]}
          @input=${(e: Event) => this.editFile(this.tab, (e.target as HTMLTextAreaElement).value)}></textarea>
      </div>

      <div class="validation" aria-live="polite">
        ${!v
          ? html`<p class="muted">Validating…</p>`
          : v.errors.length
            ? html`<ul class="errors">${v.errors.map((e) => html`<li>${e}</li>`)}</ul>`
            : html`<p class="ok">Files validate. Context: ${v.context_chars?.toLocaleString('en')} / 20,000 chars.</p>`}
      </div>

      ${this.renderComments()}

      <bs-claims .claims=${this.claims} .edited=${this.edited} .badExpect=${this.badExpect} ?busy=${this.busy}
        @claim-edit=${this.onClaimEdit} @claim-dismiss=${this.onDismiss} @claims-rerun=${() => void this.rerunClaims()}></bs-claims>

      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : ''}
    </section>`;
  }
}
