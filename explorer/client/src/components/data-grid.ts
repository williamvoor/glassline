// DataGrid: a table of pre-formatted text cells (explorer SPEC §4). Cells are text, never HTML.
import {html, nothing} from 'lit';
import {z} from 'zod';
import {A2uiLitElement} from '@a2ui/lit/v0_9';
import type {ComponentApi} from '@a2ui/web_core/v0_9';
import {spec} from '@a2ui/web_core/v1_0';

export const DataGridApi = {
  name: 'DataGrid',
  schema: spec(
    z
      .object({
        columns: z.array(
          z
            .object({key: z.string(), label: z.string(), align: z.enum(['start', 'end']).optional()})
            .strict(),
        ),
        rows: z.array(z.array(z.string())),
        caption: z.string().optional(),
      })
      .describe('A table of pre-formatted text cells.')
      .strict(),
    {mixins: ['ComponentCommon'], required: ['columns', 'rows'], closure: 'unevaluatedProperties'},
  ),
} satisfies ComponentApi;

type Column = {key: string; label: string; align?: 'start' | 'end'};

export class ExplorerDataGrid extends A2uiLitElement<typeof DataGridApi> {
  static readonly tagName = 'explorer-data-grid';

  override createRenderRoot() {
    return this;
  }

  override render() {
    const props = this.controller.props as {columns?: Column[]; rows?: string[][]; caption?: string};
    const columns = props.columns ?? [];
    const rows = props.rows ?? [];
    const align = (c: Column) => (c.align === 'end' ? 'text-align:right' : 'text-align:left');
    return html`<table class="data-grid">
      ${props.caption ? html`<caption>${props.caption}</caption>` : nothing}
      <thead>
        <tr>
          ${columns.map(c => html`<th scope="col" style=${align(c)}>${c.label}</th>`)}
        </tr>
      </thead>
      <tbody>
        ${rows.map(
          row => html`<tr>
            ${columns.map((c, i) => html`<td style=${align(c)}>${row[i] ?? ''}</td>`)}
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
