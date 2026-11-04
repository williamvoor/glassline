// Shapes of the bootstrap server's JSON (explorer/bootstrap/SPEC.md, phases 1 and 2b-2c).

export interface Connection {
  host: string;
  name: string;
  schemas: { name: string; objects: number }[];
}

export interface SchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
  comment: string | null;
}

export interface SchemaTable {
  schema: string;
  name: string;
  kind: string;
  comment: string | null;
  columns: SchemaColumn[];
}

export interface SchemaDump {
  schemas: { name: string; tables: SchemaTable[] }[];
}

export interface ColumnReport {
  name: string;
  type: string;
  nullable: boolean;
  comment: string | null;
  null_frac: number | null;
  distinct_estimate: number | null;
  most_common: { value: string; freq: number }[];
}

export interface ObjectReport {
  schema: string;
  name: string;
  kind: string;
  comment: string | null;
  est_rows: number | null;
  can_comment: boolean;
  columns: ColumnReport[];
}

export interface Summary {
  objects: number;
  columns: number;
  objects_with_comment: number;
  columns_with_comment: number;
  coverage_pct: number;
  can_comment: boolean;
  context_chars: number;
  context_target: number;
  context_scope: string;
}

export interface Report {
  schemas: string[];
  summary: Summary;
  objects: ObjectReport[];
}

export interface Estimate {
  model: string;
  prompt_chars: number;
  input_tokens_est: number;
  max_calls: number;
  first_call_usd: number;
  run_ceiling_usd: number;
}

export type Expect =
  | { kind: 'scalar'; value: number; abs_tol: number; rel_tol: number }
  | { kind: 'set'; values: string[] }
  | { kind: 'none' };

export type ClaimState = 'verified' | 'failed' | 'error' | 'needs_review';

export interface Claim {
  text: string;
  file: string;
  sql: string;
  expect: Expect;
  dismissed: boolean;
  result: { state: ClaimState; message: string; actual: unknown } | null;
}

export interface DraftComment {
  object: string;
  column: string;
  comment: string;
  kind: 'new' | 'rewrite';
  accepted: boolean;
}

export interface Files {
  dataset_toml: string;
  notes_md: string;
  glossary_md: string;
}

export interface Draft {
  model: string;
  report: Report;
  files: Files;
  comments: DraftComment[];
  claims: Claim[];
  usage: { totals: { calls: number; prompt_tokens: number; cached_tokens: number; completion_tokens: number; cost_usd: number } };
}

export interface Validation {
  errors: string[];
  context_chars: number | null;
}

export type DraftEvent =
  | { type: 'sql'; purpose: string; sql: string }
  | { type: 'call'; usage: { prompt_tokens: number; cached_tokens: number; completion_tokens: number; cost_usd: number } }
  | { type: 'invalid_draft'; problems: string[] }
  | { type: 'invalid_boards'; problems: string[] };

export interface EventsReply {
  events: DraftEvent[];
  next: number;
  done: boolean;
  error?: string;
  draft?: Draft;
  validation?: Validation;
  boards?: BoardsState;
}

/** Phase 3: one drafted Board file. */
export interface BoardFile {
  name: string;
  toml: string;
  why: string;
}

export type QueryPreview =
  | { ms: number; rows: number; truncated: boolean; columns: string[]; first_rows: string[][] }
  | { ms: number; error: string };

export interface VisualVerdict {
  id: string;
  macro: string;
  query: string;
  verdict: 'drawn' | 'no_rows' | 'failed';
}

/** One cold render of a Board (no model). `skipped` or `error` replace the timings. */
export interface BoardPreview {
  target_s: number;
  key: { value: string; source: string } | null;
  skipped?: string;
  error?: string;
  queries?: Record<string, QueryPreview>;
  visuals?: VisualVerdict[];
  seconds?: number;
  verdict?: 'ok' | 'slow';
}

export interface BoardsState {
  boards: BoardFile[];
  problems: Record<string, string[]>;
  set_problems: string[];
  preview: Record<string, BoardPreview>;
  usage: Draft['usage'] | null;
}

/** Every problem that blocks export, set problems first. */
export function boardProblems(state: BoardsState | null): string[] {
  if (!state) return [];
  return [...state.set_problems, ...Object.values(state.problems).flat()];
}

/** Fallback model list until the server's arrives; the server (/api/models) is the source of truth. */
export const MODELS = ['meta/muse-wonder-1.0', 'meta/muse-spark-1.3'] as const;
export const EFFORTS = ['minimal', 'low', 'medium', 'high'] as const;
