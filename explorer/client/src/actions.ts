// Actions on Visuals (explorer SPEC §15). The agent sends `action: {source, rows, drillable}` on
// a clickable VegaChart or DataGrid. A click dispatches `drill` (a Drill-down, no model) or
// `ask` (a model run, confirmed first) with context `{source, row}`: references only. The
// label rides along as the client-only `userMessage`, for the confirmation; the app strips it.
import {z} from 'zod';

// `boards` (SPEC §18 slice 3): rows whose click opens an Entity Board; they dispatch `drill` too.
export const ActionPropSchema = z
  .object({
    source: z.string(), rows: z.array(z.number().int()), drillable: z.boolean(),
    boards: z.array(z.number().int()).optional(),
  })
  .strict();

export type ActionProp = z.infer<typeof ActionPropSchema>;

export interface ActionContext {
  dispatchAction(action: Record<string, unknown>): Promise<void>;
}

/** True when `row` is one of the Visual's clickable rows. */
export function clickable(action: ActionProp | undefined, row: unknown): row is number {
  return !!action && typeof row === 'number' && action.rows.includes(row);
}

/** True when a click on `row` needs no model: a Drill-down, or an Entity Board. */
export function instantRow(action: ActionProp, row: number): boolean {
  return action.drillable || !!action.boards?.includes(row);
}

/** True when any clickable row needs no model, for the hint line above the Visual. */
export function instant(action: ActionProp): boolean {
  return action.drillable || !!action.boards?.length;
}

export function dispatchRow(context: ActionContext, action: ActionProp, row: number, label: string) {
  // web_core copies `userMessage` from the event (not the payload) into the dispatched action.
  return context.dispatchAction({
    event: {name: instantRow(action, row) ? 'drill' : 'ask', context: {source: action.source, row}, userMessage: label},
  });
}

// Hint lines above clickable Visuals (SPEC §16). They tell a Drill-down (instant) from an Ask
// (a model run, confirmed first). No dataset wording.
export const DRILL_HINT_CHART = 'Click a bar or point for detail';
export const ASK_HINT_CHART = 'Click a bar or point to ask the agent about it (you confirm first)';
export const DRILL_HINT_ROW = 'Click a row for detail';
export const ASK_HINT_ROW = 'Click a row to ask the agent about it (you confirm first)';
