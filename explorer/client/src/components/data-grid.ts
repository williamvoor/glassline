// DataGrid: a table of pre-formatted text cells (explorer SPEC §4). Cells are text, never HTML.
// With `action` (SPEC §15) its rows are clickable and focusable (Enter works too), with a hint
// line above the table and an Ask tag on Ask-only rows (SPEC §16).
import {html, nothing} from 'lit';
import {z} from 'zod';
import {A2uiLitElement} from '@a2ui/lit/v0_9';
import type {ComponentApi} from '@a2ui/web_core/v0_9';
import {spec} from '@a2ui/web_core/v1_0';
import {
  ASK_HINT_ROW,
  ActionPropSchema,
  DRILL_HINT_ROW,
  clickable,
  dispatchRow,
  instant,
  instantRow,
  type ActionProp,
} from '../actions.js';

export const DataGridApi = {
  name: 'DataGrid',
  schema: spec(
    z
      .object({
        columns: z.array(
          z
            .object({
              key: z.string(),
              label: z.string(),
              align: z.enum(['start', 'end']).optional(),
              // Share bars (SPEC §18): per row, the cell's share of the column's largest shown value.
              bars: z.array(z.number().min(0).max(1).nullable()).optional(),
            })
            .strict(),
        ),
        rows: z.array(z.array(z.string())),
        caption: z.string().optional(),
        action: ActionPropSchema.optional(),
      })
      .describe('A table of pre-formatted text cells.')
      .strict(),
    {mixins: ['ComponentCommon'], required: ['columns', 'rows'], closure: 'unevaluatedProperties'},
  ),
} satisfies ComponentApi;

type Column = {key: string; label: string; align?: 'start' | 'end'; bars?: (number | null)[]};

/** A cell: its formatted text, then a thin share bar under it when the column has bars. The bar
 * is decoration (aria-hidden); the text is what screen readers read. */
function cell(text: string, column: Column, row: number) {
  const share = column.bars?.[row];
  if (share === undefined || share === null) return text;
  return html`${text}<span class="share-bar" aria-hidden="true"><span style=${`width:${(share * 100).toFixed(1)}%`}></span></span>`;
}

export class ExplorerDataGrid extends A2uiLitElement<typeof DataGridApi> {
  static readonly tagName = 'explorer-data-grid';

  override createRenderRoot() {
    return this;
  }

  private activate(action: ActionProp, index: number, cells: string[]) {
    void dispatchRow(this.context, action, index, cells.filter(Boolean).join(' · '));
  }

  override render() {
    const props = this.controller.props as {
      columns?: Column[];
      rows?: string[][];
      caption?: string;
      action?: ActionProp;
    };
    const columns = props.columns ?? [];
    const rows = props.rows ?? [];
    const action = props.action;
    const align = (c: Column) => (c.align === 'end' ? 'text-align:right' : 'text-align:left');
    // Per row (SPEC §18 slice 3): a row that opens an Entity Board is instant even when the rest ask.
    const rowClass = (index: number) => (instantRow(action!, index) ? 'clickable drillable' : 'clickable askable');
    const askTag = (index: number) =>
      instantRow(action!, index) ? nothing : html`<span class="ask-tag" aria-hidden="true">Ask</span>`;
    const last = columns.length - 1;
    return html`${action
        ? html`<p class="visual-hint">${instant(action) ? DRILL_HINT_ROW : ASK_HINT_ROW}</p>`
        : nothing}<table class="data-grid">
      ${props.caption ? html`<caption>${props.caption}</caption>` : nothing}
      <thead>
        <tr>
          ${columns.map(c => html`<th scope="col" style=${align(c)}>${c.label}</th>`)}
        </tr>
      </thead>
      <tbody>
        ${rows.map((row, index) =>
          clickable(action, index)
            ? html`<tr
                class=${rowClass(index)}
                tabindex="0"
                @click=${() => this.activate(action!, index, row)}
                @keydown=${(e: KeyboardEvent) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    this.activate(action!, index, row);
                  }
                }}
              >
                ${columns.map((c, i) => html`<td style=${align(c)}>${cell(row[i] ?? '', c, index)}${i === last ? askTag(index) : nothing}</td>`)}
              </tr>`
            : html`<tr>
                ${columns.map((c, i) => html`<td style=${align(c)}>${cell(row[i] ?? '', c, index)}</td>`)}
              </tr>`,
        )}
      </tbody>
    </table>`;
  }
}

if (!customElements.get(ExplorerDataGrid.tagName)) {
  customElements.define(ExplorerDataGrid.tagName, ExplorerDataGrid);
}

export const DataGrid = {...DataGridApi, tagName: ExplorerDataGrid.tagName, element: ExplorerDataGrid};
