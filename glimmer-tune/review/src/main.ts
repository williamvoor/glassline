// <review-app>: one draft row at a time. Edit the intent, check the rendered reference, approve.
import {LitElement, html, nothing} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {provide} from '@lit/context';
import {Context, basicCatalog} from '@a2ui/lit/v0_9';
import '@a2ui/lit/v0_9';
import {MessageProcessor, type A2uiMessage, type ComponentApi} from '@a2ui/web_core/v0_9';
import {renderMarkdown} from '@a2ui/markdown-it';

interface Row {
  id: string;
  family: string;
  split: string;
  intent: string;
  reviewed: boolean;
  reference_express: string;
  expected: string[];
  v09: Record<string, unknown>[];
  render_error?: string;
  // Synthetic review mode (the server adds `synthetic: true`): a verdict per row instead of an edited intent.
  synthetic?: boolean;
  skeleton?: string;
  domain?: string;
  detail?: string;
  lint?: string[];
  verdict?: 'approve' | 'reject' | PredictionVerdict | null;
  reason?: string;
  // Prediction review mode (`prediction_mode: true`): a checkpoint's answer beside the reference.
  prediction_mode?: boolean;
  prediction_text?: string | null;
  prediction_v09?: Record<string, unknown>[];
  prediction_error?: string;
  finish?: string;
  scores?: {slot_f1_blind: number; slot_f1: number; compiles: boolean};
  outside_catalog_kinds?: string[];
  note?: string;
}

type PredictionVerdict = 'ok' | 'model' | 'reference' | 'scorer';
const PREDICTION_VERDICTS: [PredictionVerdict, string, string][] = [
  ['ok', 'OK', '1'],
  ['model', 'Model error', '2'],
  ['reference', 'Reference problem', '3'],
  ['scorer', 'Scorer artefact', '4'],
];

type Filter = 'all' | 'todo' | 'dev' | 'test';

const norm = (s: string) => s.toLowerCase().replace(/\s+/g, ' ');

@customElement('review-app')
export class ReviewApp extends LitElement {
  @provide({context: Context.markdown})
  markdownRenderer = (value: string, options?: unknown) =>
    renderMarkdown(value, options as Parameters<typeof renderMarkdown>[1]);

  @state() private rows: Row[] = [];
  @state() private index = 0;
  @state() private draft = ''; // the intent being edited
  @state() private filter: Filter = 'all';
  @state() private error = '';
  @state() private saving = false;
  @state() private reason = ''; // the reject reason being typed (synthetic mode)

  override createRenderRoot() {
    return this; // light DOM, so index.html styles reach the A2UI components
  }

  override async connectedCallback() {
    super.connectedCallback();
    window.addEventListener('keydown', this.onKey);
    try {
      const res = await fetch('/api/rows');
      if (!res.ok) throw new Error(`GET /api/rows: ${res.status}`);
      this.rows = await res.json();
      const firstTodo = this.rows.findIndex(r => !r.reviewed);
      this.select(firstTodo < 0 ? 0 : firstTodo);
    } catch (e) {
      this.error = String(e);
    }
  }

  override disconnectedCallback() {
    window.removeEventListener('keydown', this.onKey);
    super.disconnectedCallback();
  }

  private get row(): Row | undefined {
    return this.rows[this.index];
  }

  private visible(): number[] {
    return this.rows
      .map((r, i) => [r, i] as const)
      .filter(([r]) => this.filter === 'all' || (this.filter === 'todo' ? !r.reviewed : r.split === this.filter))
      .map(([, i]) => i);
  }

  /** Earlier row with the same reference: approving both weights one UI twice. */
  private duplicateOf(i: number): string | undefined {
    const express = this.rows[i].reference_express;
    return this.rows.slice(0, i).find(r => r.reference_express === express)?.id;
  }

  private select(i: number) {
    if (i < 0 || i >= this.rows.length) return;
    this.index = i;
    this.draft = this.rows[i].intent;
    this.reason = this.rows[i].reason ?? this.rows[i].note ?? '';
  }

  private async save(reviewed: boolean): Promise<boolean> {
    const row = this.row;
    if (row?.synthetic || row?.prediction_mode) return true; // nothing to edit; verdicts are saved by `verdict`
    if (!row || !this.draft.trim()) return false;
    if (row.intent === this.draft && row.reviewed === reviewed) return true;
    this.saving = true;
    try {
      const res = await fetch('/api/rows', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: row.id, intent: this.draft, reviewed}),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      this.rows = this.rows.map(r => (r.id === row.id ? {...r, intent: body.intent, reviewed: body.reviewed} : r));
      this.error = '';
      return true;
    } catch (e) {
      this.error = `Not saved: ${e}`;
      return false;
    } finally {
      this.saving = false;
    }
  }

  /** Synthetic mode: records approve or reject (a reject needs a reason) and moves on. */
  private async verdict(kind: 'approve' | 'reject') {
    const row = this.row;
    if (!row) return;
    if (kind === 'reject' && !this.reason.trim()) {
      this.error = 'A reject needs a reason.';
      return;
    }
    this.saving = true;
    try {
      const res = await fetch('/api/verdict', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: row.id, verdict: kind, reason: kind === 'reject' ? this.reason : ''}),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      this.rows = this.rows.map(r => (r.id === row.id ? {...r, verdict: body.verdict, reason: body.reason, reviewed: true} : r));
      this.error = '';
    } catch (e) {
      this.error = `Not saved: ${e}`;
      return;
    } finally {
      this.saving = false;
    }
    await this.step(1);
  }

  /** Moves within the current filter, saving an edited intent first. */
  private async step(delta: number, reviewed?: boolean) {
    if (!(await this.save(reviewed ?? this.row?.reviewed ?? false))) return;
    const vis = this.visible();
    const pos = vis.indexOf(this.index);
    const next = pos < 0 ? vis[0] : vis[pos + delta];
    if (next !== undefined) this.select(next);
  }

  /** Prediction mode: records why the row scores as it does, with an optional note, and moves on. */
  private async judge(kind: PredictionVerdict) {
    const row = this.row;
    if (!row) return;
    this.saving = true;
    try {
      const res = await fetch('/api/verdict', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: row.id, verdict: kind, note: this.reason}),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      this.rows = this.rows.map(r => (r.id === row.id ? {...r, verdict: body.verdict, note: body.note, reviewed: true} : r));
      this.error = '';
    } catch (e) {
      this.error = `Not saved: ${e}`;
      return;
    } finally {
      this.saving = false;
    }
    await this.step(1);
  }

  private onKey = (e: KeyboardEvent) => {
    const tag = (e.target as HTMLElement)?.tagName;
    const inText = tag === 'TEXTAREA' || tag === 'INPUT';
    if (this.row?.prediction_mode) {
      const pick = PREDICTION_VERDICTS.find(([, , key]) => key === e.key);
      if (!inText && pick) void this.judge(pick[0]);
      else if (!inText && e.key === 'j') void this.step(1);
      else if (!inText && e.key === 'k') void this.step(-1);
      return;
    }
    if (this.row?.synthetic) {
      if (!inText && e.key === 'a') void this.verdict('approve');
      else if (!inText && e.key === 'j') void this.step(1);
      else if (!inText && e.key === 'k') void this.step(-1);
      else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void this.verdict('reject');
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      void this.step(1, true);
    } else if (!inText && e.key === 'j') void this.step(1);
    else if (!inText && e.key === 'k') void this.step(-1);
    else if (!inText && e.key === 'a') void this.step(1, true);
    else if (!inText && e.key === 'u') void this.save(false);
  };

  private renderSurface(row: Row) {
    return this.renderMessages(row.v09, row.render_error);
  }

  private renderMessages(messages: Record<string, unknown>[], error?: string) {
    if (error) return html`<div class="error">${error}</div>`;
    // A fresh processor per row: many rows share one surface ID.
    const processor = new MessageProcessor<ComponentApi>([basicCatalog]);
    try {
      processor.processMessages(messages as unknown as A2uiMessage[]);
    } catch (e) {
      return html`<div class="error">Renderer: ${String(e)}</div>`;
    }
    const create = messages.find(m => 'createSurface' in m)?.createSurface as {surfaceId?: string} | undefined;
    const surface = create?.surfaceId ? processor.model.surfacesMap.get(create.surfaceId) : undefined;
    return surface
      ? html`<a2ui-surface .surface=${surface}></a2ui-surface>`
      : html`<div class="error">No surface in the compiled messages</div>`;
  }

  private renderList() {
    const done = this.rows.filter(r => r.reviewed).length;
    let family = '';
    return html`<aside>
      <header>
        <div class="progress">${done} / ${this.rows.length} ${this.rows[0]?.synthetic || this.rows[0]?.prediction_mode ? 'reviewed' : 'approved'}</div>
        <select @change=${(e: Event) => (this.filter = (e.target as HTMLSelectElement).value as Filter)}>
          <option value="all">All rows</option>
          <option value="todo">Not approved</option>
          <option value="dev">Dev only</option>
          <option value="test">Test only</option>
        </select>
      </header>
      ${this.visible().map(i => {
        const r = this.rows[i];
        const heading = !r.prediction_mode && r.family !== family ? html`<div class="family">${r.split} · ${r.family}</div>` : nothing;
        family = r.family;
        return html`${heading}
          <div class="item ${i === this.index ? 'current' : ''}" @click=${() => this.save(this.row!.reviewed).then(ok => ok && this.select(i))}>
            <span class="mark">${r.prediction_mode ? (r.verdict ? r.verdict[0].toUpperCase() : '') : r.synthetic ? (r.verdict === 'reject' ? '✗' : r.verdict === 'approve' ? '✓' : '') : r.reviewed ? '✓' : ''}</span>
            <span class="text">${r.prediction_mode ? `${r.scores?.slot_f1_blind.toFixed(2)} · ${r.family} ` : ''}${r.id.split('/').pop()} ${r.intent}</span>
            ${this.duplicateOf(i) ? html`<span class="dup" title="Same UI as ${this.duplicateOf(i)}">dup</span>` : nothing}
          </div>`;
      })}
    </aside>`;
  }

  private renderSynthetic(row: Row) {
    return html`${this.renderList()}
      <main>
        <div class="meta">
          <strong>${row.id}</strong>
          <span class="badge">${row.domain} · ${row.detail}</span>
          ${row.verdict ? html`<span class="badge ${row.verdict === 'approve' ? 'ok' : 'warn'}">${row.verdict}</span>` : html`<span class="badge">not reviewed</span>`}
          ${(row.lint ?? []).map(rule => html`<span class="badge warn">lint: ${rule}</span>`)}
        </div>
        <p class="intent">${row.intent}</p>
        <div class="values">
          <span class="hint">Values in the UI:</span>
          ${row.expected.map(v => html`<span class="chip ${norm(row.intent).includes(norm(v)) ? '' : 'missing'}">${v}</span>`)}
        </div>
        <div class="actions">
          <input type="text" placeholder="Reason (required to reject)" .value=${this.reason} @input=${(e: Event) => (this.reason = (e.target as HTMLInputElement).value)} />
          <button class="primary" ?disabled=${this.saving} @click=${() => this.verdict('approve')}>Approve &amp; next</button>
          <button ?disabled=${this.saving} @click=${() => this.verdict('reject')}>Reject &amp; next</button>
          <button ?disabled=${this.saving} @click=${() => this.step(-1)}>← Prev</button>
          <button ?disabled=${this.saving} @click=${() => this.step(1)}>Skip →</button>
          <span class="hint">a approve · ⌘/Ctrl+Enter reject · j/k next/prev</span>
        </div>
        ${this.error ? html`<div class="error">${this.error}</div>` : nothing}
        <div class="canvas">${this.renderSurface(row)}</div>
        <details>
          <summary>Reference Express</summary>
          <pre>${row.reference_express}</pre>
        </details>
      </main>`;
  }

  private renderPrediction(row: Row) {
    const s = row.scores;
    return html`${this.renderList()}
      <main>
        <div class="meta">
          <strong>${row.id}</strong>
          <span class="badge">blind F1 ${s?.slot_f1_blind.toFixed(3)} · literal ${s?.slot_f1.toFixed(3)}</span>
          <span class="badge ${s?.compiles ? 'ok' : 'warn'}">${s?.compiles ? 'compiles' : 'does not compile'}</span>
          ${(row.outside_catalog_kinds ?? []).map(k => html`<span class="badge warn">outside catalog: ${k}</span>`)}
          ${row.verdict ? html`<span class="badge ok">${row.verdict}</span>` : html`<span class="badge">not reviewed</span>`}
        </div>
        <p class="intent">${row.intent}</p>
        <div class="actions">
          <input type="text" placeholder="Note (optional)" .value=${this.reason} @input=${(e: Event) => (this.reason = (e.target as HTMLInputElement).value)} />
          ${PREDICTION_VERDICTS.map(([kind, label, key]) => html`<button class=${kind === 'ok' ? 'primary' : ''} ?disabled=${this.saving} @click=${() => this.judge(kind)}>${label} (${key})</button>`)}
          <button ?disabled=${this.saving} @click=${() => this.step(-1)}>← Prev</button>
          <button ?disabled=${this.saving} @click=${() => this.step(1)}>Skip →</button>
          <span class="hint">1–4 verdict · j/k next/prev</span>
        </div>
        ${this.error ? html`<div class="error">${this.error}</div>` : nothing}
        <div class="pair">
          <section><h3>Reference</h3><div class="canvas">${this.renderSurface(row)}</div></section>
          <section><h3>Model (${row.finish})</h3><div class="canvas">${this.renderMessages(row.prediction_v09 ?? [], row.prediction_error)}</div></section>
        </div>
        <div class="pair">
          <details open><summary>Reference Express</summary><pre>${row.reference_express}</pre></details>
          <details open><summary>Model Express</summary><pre>${row.prediction_text ?? '(no response)'}</pre></details>
        </div>
      </main>`;
  }

  override render() {
    const row = this.row;
    if (!row) return html`<aside></aside><main>${this.error ? html`<div class="error">${this.error}</div>` : 'Loading…'}</main>`;
    if (row.prediction_mode) return this.renderPrediction(row);
    if (row.synthetic) return this.renderSynthetic(row);
    const text = norm(this.draft);
    const dup = this.duplicateOf(this.index);
    return html`${this.renderList()}
      <main>
        <div class="meta">
          <strong>${row.id}</strong>
          <span class="badge">${row.split}</span>
          ${row.reviewed ? html`<span class="badge ok">approved</span>` : html`<span class="badge">not approved</span>`}
          ${dup ? html`<span class="badge warn">same UI as ${dup}</span>` : nothing}
          ${row.intent !== this.draft ? html`<span class="badge warn">edited, not saved</span>` : nothing}
        </div>
        <textarea .value=${this.draft} @input=${(e: Event) => (this.draft = (e.target as HTMLTextAreaElement).value)}></textarea>
        <div class="values">
          <span class="hint">Values in the UI:</span>
          ${row.expected.map(v => html`<span class="chip ${text.includes(norm(v)) ? '' : 'missing'}" title=${text.includes(norm(v)) ? 'in the intent' : 'not found in the intent text'}>${v}</span>`)}
        </div>
        <div class="actions">
          <button class="primary" ?disabled=${this.saving} @click=${() => this.step(1, true)}>Approve &amp; next</button>
          <button ?disabled=${this.saving} @click=${() => this.save(false)}>Unapprove</button>
          <button ?disabled=${this.saving} @click=${() => this.step(-1)}>← Prev</button>
          <button ?disabled=${this.saving} @click=${() => this.step(1)}>Skip →</button>
          <span class="hint">⌘/Ctrl+Enter approve · a approve · j/k next/prev · u unapprove</span>
        </div>
        ${this.error ? html`<div class="error">${this.error}</div>` : nothing}
        <div class="canvas">${this.renderSurface(row)}</div>
        <details>
          <summary>Reference Express</summary>
          <pre>${row.reference_express}</pre>
        </details>
      </main>`;
  }
}
