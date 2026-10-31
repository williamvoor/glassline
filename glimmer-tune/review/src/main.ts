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
}

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
  }

  private async save(reviewed: boolean): Promise<boolean> {
    const row = this.row;
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

  /** Moves within the current filter, saving an edited intent first. */
  private async step(delta: number, reviewed?: boolean) {
    if (!(await this.save(reviewed ?? this.row?.reviewed ?? false))) return;
    const vis = this.visible();
    const pos = vis.indexOf(this.index);
    const next = pos < 0 ? vis[0] : vis[pos + delta];
    if (next !== undefined) this.select(next);
  }

  private onKey = (e: KeyboardEvent) => {
    const inText = (e.target as HTMLElement)?.tagName === 'TEXTAREA';
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      void this.step(1, true);
    } else if (!inText && e.key === 'j') void this.step(1);
    else if (!inText && e.key === 'k') void this.step(-1);
    else if (!inText && e.key === 'a') void this.step(1, true);
    else if (!inText && e.key === 'u') void this.save(false);
  };

  private renderSurface(row: Row) {
    if (row.render_error) return html`<div class="error">${row.render_error}</div>`;
    // A fresh processor per row: many rows share one surface ID.
    const processor = new MessageProcessor<ComponentApi>([basicCatalog]);
    try {
      processor.processMessages(row.v09 as unknown as A2uiMessage[]);
    } catch (e) {
      return html`<div class="error">Renderer: ${String(e)}</div>`;
    }
    const create = row.v09.find(m => 'createSurface' in m)?.createSurface as {surfaceId?: string} | undefined;
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
        <div class="progress">${done} / ${this.rows.length} approved</div>
        <select @change=${(e: Event) => (this.filter = (e.target as HTMLSelectElement).value as Filter)}>
          <option value="all">All rows</option>
          <option value="todo">Not approved</option>
          <option value="dev">Dev only</option>
          <option value="test">Test only</option>
        </select>
      </header>
      ${this.visible().map(i => {
        const r = this.rows[i];
        const heading = r.family !== family ? html`<div class="family">${r.split} · ${r.family}</div>` : nothing;
        family = r.family;
        return html`${heading}
          <div class="item ${i === this.index ? 'current' : ''}" @click=${() => this.save(this.row!.reviewed).then(ok => ok && this.select(i))}>
            <span class="mark">${r.reviewed ? '✓' : ''}</span>
            <span class="text">${r.id.split('/').pop()} ${r.intent}</span>
            ${this.duplicateOf(i) ? html`<span class="dup" title="Same UI as ${this.duplicateOf(i)}">dup</span>` : nothing}
          </div>`;
      })}
    </aside>`;
  }

  override render() {
    const row = this.row;
    if (!row) return html`<aside></aside><main>${this.error ? html`<div class="error">${this.error}</div>` : 'Loading…'}</main>`;
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
