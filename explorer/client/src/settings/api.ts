import type { BoardFile, BoardsState, Claim, Draft, DraftComment, EventsReply, Files, Report, SchemaDump, Session, Validation } from './types.js';
import {ConnectionSchema, EstimateSchema, ValidationSchema} from './types.js';
import {z} from 'zod';

const ModelsSchema = z.object({models: z.array(z.string()), default: z.string()}).passthrough();
const ConnectionsSchema = z.object({connections: z.array(z.string()), current: z.string().nullable(), declared: z.boolean().optional()}).passthrough();
const WorkspaceSchema = z.object({name: z.string(), dsn_env: z.string().nullable(), kind: z.enum(['data', 'repo'])}).passthrough();
const WorkspacesSchema = z.object({workspaces: z.array(WorkspaceSchema)}).passthrough();
const ErrorSchema = z.object({error: z.string().optional(), reasons: z.array(z.string()).optional()}).passthrough();

/** An API error: `message` for one problem, `reasons` when the server lists several. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly reasons: string[] = []) {
    super(message);
  }
}

type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

// Copied from the retired explorer/bootstrap/ui (api.ts) for the explorer's Settings (Workspaces SPEC phase 2,
// D6); the Settings-mode routes are added at the end.

export type SettingsWorkspace = z.infer<typeof WorkspaceSchema>;

/** The bootstrap server's API. Every POST is JSON (the server refuses anything else). */
export class Api {
  constructor(private readonly fetchImpl: FetchFn = (i, init) => fetch(i, init), private readonly base = '') {}

  private async call<T>(path: string, body?: unknown, schema?: z.ZodType<T>): Promise<T> {
    const init: RequestInit =
      body === undefined
        ? { method: 'GET' }
        : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) };
    const res = await this.fetchImpl(this.base + path, init);
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      if (res.ok) throw new ApiError('The server returned invalid JSON.', res.status);
      data = null;
    }
    if (!res.ok) {
      const error = ErrorSchema.safeParse(data);
      const reasons = error.success ? error.data.reasons ?? [] : [];
      throw new ApiError((error.success ? error.data.error : undefined) ?? (reasons.length ? reasons.join('; ') : `HTTP ${res.status}`), res.status, reasons);
    }
    if (schema) {
      const parsed = schema.safeParse(data);
      if (!parsed.success) throw new ApiError('The server returned an invalid response.', res.status);
      return parsed.data;
    }
    return data as T;
  }

  connection() {
    return this.call('/api/connection', undefined, ConnectionSchema);
  }
  datasets() {
    return this.call<{ datasets: string[] }>('/api/datasets');
  }
  models() {
    return this.call('/api/models', undefined, ModelsSchema);
  }
  schema(schemas?: string[]) {
    const q = schemas && schemas.length > 0 ? `?schemas=${schemas.map(encodeURIComponent).join(',')}` : '';
    return this.call<SchemaDump>(`/api/schema${q}`);
  }
  report(sel: { schemas: string[] } | { dataset: string }) {
    return this.call<Report>('/api/report', sel);
  }
  estimate(model: string, effort: string) {
    return this.call('/api/estimate', { model, effort }, EstimateSchema);
  }
  startDraft(model: string, effort: string) {
    return this.call<{ run_id: string }>('/api/draft', { model, effort });
  }
  events(runId: string, after: number) {
    return this.call<EventsReply>(`/api/draft/${encodeURIComponent(runId)}/events?after=${after}`);
  }
  currentDraft() {
    return this.call<{ draft: Draft; validation: Validation }>('/api/draft');
  }
  verify(claims: Claim[]) {
    return this.call<{ claims: Claim[] }>('/api/claims/verify', { claims });
  }
  validate(files: Files, comments: DraftComment[]) {
    return this.call('/api/validate', { files, comments }, ValidationSchema);
  }
  export(body: { name: string; target: 'toml' | 'sql'; files: Files; comments: DraftComment[]; dismissed: number[]; force: boolean }) {
    return this.call<{ path: string; overwrote: string[]; backup: string | null; workspace?: string; reloaded?: boolean }>('/api/export', body);
  }
  boardsEstimate(model: string, effort: string) {
    return this.call('/api/boards/estimate', { model, effort }, EstimateSchema);
  }
  startBoards(model: string, effort: string) {
    return this.call<{ run_id: string }>('/api/boards/draft', { model, effort });
  }
  /** null before any Boards. */
  currentBoards() {
    return this.call<BoardsState | null>('/api/boards');
  }
  validateBoards(boards: BoardFile[]) {
    return this.call<BoardsState>('/api/boards/validate', { boards });
  }
  // Settings mode (D2–D5)
  connections() {
    return this.call('/api/connections', undefined, ConnectionsSchema);
  }
  connect(dsnEnv: string) {
    return this.call('/api/connect', { dsn_env: dsnEnv }, ConnectionSchema.extend({dsn_env: z.string()}));
  }
  workspaces() {
    return this.call('/api/workspaces', undefined, WorkspacesSchema);
  }
  deleteWorkspace(name: string) {
    return this.call<{ deleted: string; reloaded: boolean; workspaces: SettingsWorkspace[] }>(
      `/api/workspaces/${encodeURIComponent(name)}/delete`, {});
  }
  reload() {
    return this.call<{ reloaded: boolean; status: number; error?: string }>('/api/reload', {});
  }
  regenerate(name: string) {
    return this.call<Report>(`/api/workspaces/${encodeURIComponent(name)}/regenerate`, {});
  }
  // The New Workspace wizard (Workspaces SPEC phase 3, E6–E8)
  session() {
    return this.call<Session>('/api/session');
  }
  discard() {
    return this.call<{ discarded: boolean }>('/api/discard', {});
  }
  keepBoards() {
    return this.call<BoardsState>('/api/boards/keep', {});
  }
}
