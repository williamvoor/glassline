// Model chips (Workspaces SPEC phase 3, "Models on show", ADR 0044): one look for a model everywhere it
// works — the wizard's step bar and run cards, and each answer Turn. Colours are theme tokens.
import {html} from 'lit';

const NAMES: Record<string, {name: string; key: string}> = {
  'meta/muse-wonder-1.0': {name: 'Muse Wonder', key: 'wonder'},
  'meta/muse-spark-1.3': {name: 'Muse Spark', key: 'spark'},
};

/** The display name of a model ID; an unknown ID is shown as it is. */
export function modelName(id: string): string {
  return NAMES[id]?.name ?? id;
}

/** A chip for a model ID, or the SQL chip for `null` (no model: database only). */
export function modelChip(id: string | null, opts: {live?: boolean} = {}) {
  if (id === null) return html`<span class="model-chip model-sql" title="No model: database only">SQL</span>`;
  const known = NAMES[id];
  return html`<span class="model-chip model-${known?.key ?? 'other'}${opts.live ? ' live' : ''}" title=${id}
    ><span class="dot" aria-hidden="true"></span>${known?.name ?? id}</span>`;
}
