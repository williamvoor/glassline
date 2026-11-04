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

/** Why export is blocked right now (empty = allowed). The server checks again on export. */
export function exportBlockers(opts: {
  validation: Validation | null;
  claims: Claim[];
  edited: Set<number>;
  name: string;
  /** Phase 3: the server's Board problems (export writes the Boards it holds). */
  boardProblems?: string[];
}): string[] {
  const out: string[] = [];
  if (!opts.name.trim()) out.push('Give the dataset a name.');
  if (!opts.validation) out.push('The files have not been validated yet.');
  else if (opts.validation.errors.length) out.push('The files do not validate.');
  if (opts.edited.size) out.push('Re-run the edited claims.');
  opts.claims.forEach((c, i) => {
    const state = c.result?.state;
    if (!c.dismissed && !opts.edited.has(i) && (state === 'failed' || state === 'error' || !state)) {
      out.push(`Claim ${i + 1} is ${state ?? 'not checked'}: fix and re-run, or dismiss it.`);
    }
  });
  if (opts.boardProblems?.length) out.push('A Board has problems: fix them on the Boards step.');
  return out;
}

/** Step 3: edit every drafted file, the comments and the claims; validate; export. */
@customElement('bs-review')
export class BsReview extends LitElement {
  @property({ attribute: false }) api: Api = new Api();
  @property({ attribute: false }) draft: Draft | null = null;
  @property({ attribute: false }) validation: Validation | null = null;
  /** Phase 3: problems of the Boards the server holds (none when there are no Boards). */
  @property({ attribute: false }) boardProblems: string[] = [];
  /** Milliseconds between the last edit and validation; tests set 0. */
  @property({ type: Number }) validateDelay = 400;
  /** Settings (Workspaces SPEC D3): the name is the Workspace's, comments go to comments.toml,
   * and a regenerate (`fixedName`) exports back into its own folder. */
  @property({ type: Boolean }) workspaceMode = false;
  @property() fixedName = '';

  @state() files: Files = { dataset_toml: '', notes_md: '', glossary_md: '' };
  @state() comments: DraftComment[] = [];
  @state() claims: Claim[] = [];
  @state() edited = new Set<number>();
  @state() badExpect = new Set<number>();
  @state() tab: keyof Files = 'dataset_toml';
  @state() name = '';
  @state() target: 'toml' | 'sql' = 'toml';
  @state() force = false;
  @state() reasons: string[] = [];
  @state() exported = '';
  @state() overwrote: string[] = [];
  @state() backup: string | null = null;
  @state() busy = false;
  @state() error = '';
  private timer: ReturnType<typeof setTimeout> | undefined;
  private validateSeq = 0;
  private loadedFrom: Draft | null = null;

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
      this.target = this.draft.report.summary.can_comment ? 'sql' : 'toml';
      this.name = this.name || (this.files.dataset_toml.match(/^name\s*=\s*"([a-z][a-z0-9_]*)"/m)?.[1] ?? '');
    }
  }

  private scheduleValidate() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.validate(), this.validateDelay);
  }

  /** Validate (which also stores the edits on the server). False when the request failed. */
  async validate(): Promise<boolean> {
    const seq = ++this.validateSeq;
    try {
      const v = await this.api.validate(this.files, this.comments);
      if (seq === this.validateSeq) this.validation = v; // ignore replies to older edits
      return true;
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
      return false;
    }
  }

  private editFile(key: keyof Files, value: string) {
    this.files = { ...this.files, [key]: value };
    this.exported = '';
    this.overwrote = [];
    this.backup = null;
    this.scheduleValidate();
  }

  private editComment(i: number, patch: Partial<DraftComment>) {
    this.comments = this.comments.map((c, j) => (j === i ? { ...c, ...patch } : c));
    this.exported = '';
    this.overwrote = [];
    this.backup = null;
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

  async exportNow() {
    this.busy = true;
    this.reasons = [];
    this.error = '';
    try {
      const dismissed = this.claims.flatMap((c, i) => (c.dismissed ? [i] : []));
      const r = await this.api.export({ name: this.name.trim(), target: this.target, files: this.files,
        comments: this.comments, dismissed, force: this.force });
      this.exported = r.path;
      this.overwrote = r.overwrote;
      this.backup = r.backup;
      this.dispatchEvent(new CustomEvent('exported', { detail: r, bubbles: true }));
    } catch (e) {
      if (e instanceof ApiError && e.reasons.length) this.reasons = e.reasons;
      else this.error = (e as Error).message;
    } finally {
      this.busy = false;
    }
  }

  /** Bring the Export section into view, as "Continue to Export" on the Boards step asks. */
  focusExport() {
    const h = this.querySelector<HTMLElement>('#export-h');
    h?.scrollIntoView?.({ block: 'start' });
    h?.focus();
  }

  /** Save pending file and comment edits on the server (validate stores them) before leaving. */
  async leaveForReport() {
    clearTimeout(this.timer);
    if (!(await this.validate())) return; // the edits weren't saved: stay, with the error shown
    this.dispatchEvent(new CustomEvent('rerun-report', { bubbles: true }));
  }

  /** Phase 3: the Boards are drafted from the files the server holds, so save the edits first. */
  async leaveForBoards() {
    clearTimeout(this.timer);
    if (!(await this.validate())) return;
    this.dispatchEvent(new CustomEvent('to-boards', { bubbles: true }));
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
    const blockers = exportBlockers({
      validation: this.validation, claims: this.claims, edited: this.edited, name: this.fixedName || this.name, boardProblems: this.boardProblems,
    });
    const v = this.validation;
    return html`<section aria-labelledby="review-h">
      <h2 id="review-h">Review</h2>
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

      <h3 id="export-h" tabindex="-1">Export</h3>
      <div class="row">
        ${this.workspaceMode && this.fixedName
          ? html`<span class="name-fixed">Regenerating <strong>${this.fixedName}</strong> (replaced files are backed up)</span>`
          : html`<label>${this.workspaceMode ? 'Workspace name' : 'Name'} <input class="name" .value=${this.name}
              @input=${(e: Event) => (this.name = (e.target as HTMLInputElement).value)} /></label>`}
        ${this.workspaceMode ? '' : html`<fieldset class="target">
          <legend>Comments go to</legend>
          <label><input type="radio" name="target" value="toml" .checked=${this.target === 'toml'} @change=${() => (this.target = 'toml')} />
            comments.toml (Comment overlay)</label>
          <label><input type="radio" name="target" value="sql" .checked=${this.target === 'sql'} @change=${() => (this.target = 'sql')} />
            comments.sql (apply as the owner)</label>
        </fieldset>
        <label><input type="checkbox" class="force" .checked=${this.force}
          @change=${(e: Event) => (this.force = (e.target as HTMLInputElement).checked)} />
          Overwrite the draft's files if the directory exists (replaced files are backed up)</label>`}
        <button class="primary export" ?disabled=${this.busy || blockers.length > 0} @click=${() => void this.exportNow()}>Export</button>
        <button class="rerun-report" @click=${() => void this.leaveForReport()}>Re-run report</button>
        <button class="to-boards" @click=${() => void this.leaveForBoards()}>Boards</button>
      </div>
      ${blockers.length ? html`<ul class="blockers">${blockers.map((b) => html`<li>${b}</li>`)}</ul>` : ''}
      ${this.reasons.length ? html`<div class="refused" role="alert"><p>Export refused:</p><ul>${this.reasons.map((r) => html`<li>${r}</li>`)}</ul></div>` : ''}
      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : ''}
      ${this.exported ? html`<p class="ok exported">Exported to <code>${this.exported}</code>${this.overwrote.length ? html`, replacing ${this.overwrote.join(', ')}` : ''}${this.backup ? html` (previous files kept in <code>${this.backup}</code>)` : ''}.</p>` : ''}
    </section>`;
  }
}
