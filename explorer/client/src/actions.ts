// Actions on Visuals (explorer SPEC §15). The agent sends `action: {source, rows, drillable}` on
// a clickable VegaChart or DataGrid. A click dispatches `drill` (a Drill-down, no model) or
// `ask` (a model run, confirmed first) with context `{source, row}`: references only. The
// label rides along as the client-only `userMessage`, for the confirmation; the app strips it.
import {z} from 'zod';

export const ActionPropSchema = z
  .object({source: z.string(), rows: z.array(z.number().int()), drillable: z.boolean()})
  .strict();

export type ActionProp = z.infer<typeof ActionPropSchema>;

export interface ActionContext {
  dispatchAction(action: Record<string, unknown>): Promise<void>;
}

/** True when `row` is one of the Visual's clickable rows. */
export function clickable(action: ActionProp | undefined, row: unknown): row is number {
  return !!action && typeof row === 'number' && action.rows.includes(row);
}

export function dispatchRow(context: ActionContext, action: ActionProp, row: number, label: string) {
  // web_core copies `userMessage` from the event (not the payload) into the dispatched action.
  return context.dispatchAction({
    event: {name: action.drillable ? 'drill' : 'ask', context: {source: action.source, row}, userMessage: label},
  });
}

// Hint lines above clickable Visuals (SPEC §16). They tell a Drill-down (instant) from an Ask
// (a model run, confirmed first). No dataset wording.
export const DRILL_HINT_CHART = 'Click a bar or point for detail';
export const ASK_HINT_CHART = 'Click a bar or point to ask the agent about it (you confirm first)';
export const DRILL_HINT_ROW = 'Click a row for detail';
export const ASK_HINT_ROW = 'Click a row to ask the agent about it (you confirm first)';
