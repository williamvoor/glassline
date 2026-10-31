// SqlDetails: the SQL behind a visual, collapsed (explorer SPEC §4). Rendered as text.
import {html} from 'lit';
import {z} from 'zod';
import {A2uiLitElement} from '@a2ui/lit/v0_9';
import type {ComponentApi} from '@a2ui/web_core/v0_9';
import {spec} from '@a2ui/web_core/v1_0';

export const SqlDetailsApi = {
  name: 'SqlDetails',
  schema: spec(
    z
      .object({sql: z.string(), summary: z.string().optional()})
      .describe('A collapsed block showing the SQL behind a visual.')
      .strict(),
    {mixins: ['ComponentCommon'], required: ['sql'], closure: 'unevaluatedProperties'},
  ),
} satisfies ComponentApi;

export class ExplorerSqlDetails extends A2uiLitElement<typeof SqlDetailsApi> {
  static readonly tagName = 'explorer-sql-details';

  override createRenderRoot() {
    return this;
  }

  override render() {
    const {sql, summary} = this.controller.props as {sql?: string; summary?: string};
    return html`<details class="sql-details">
      <summary>${summary ? `SQL — ${summary}` : 'SQL'}</summary>
      <pre><code>${sql ?? ''}</code></pre>
    </details>`;
  }
}

if (!customElements.get(ExplorerSqlDetails.tagName)) {
  customElements.define(ExplorerSqlDetails.tagName, ExplorerSqlDetails);
}

export const SqlDetails = {...SqlDetailsApi, tagName: ExplorerSqlDetails.tagName, element: ExplorerSqlDetails};
