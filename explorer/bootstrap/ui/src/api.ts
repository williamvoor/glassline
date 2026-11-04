import type { BoardFile, BoardsState, Claim, Connection, Draft, DraftComment, Estimate, EventsReply, Files, Report, SchemaDump, Validation } from './types.js';

/** An API error: `message` for one problem, `reasons` when the server lists several. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly reasons: string[] = []) {
    super(message);
  }
}

type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

/** The bootstrap server's API. Same origin; every POST is JSON (the server refuses anything else). */
export class Api {
  constructor(private readonly fetchImpl: FetchFn = (i, init) => fetch(i, init), private readonly base = '') {}

  private async call<T>(path: string, body?: unknown): Promise<T> {
    const init: RequestInit =
      body === undefined
        ? { method: 'GET' }
        : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) };
    const res = await this.fetchImpl(this.base + path, init);
    let data: any = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    if (!res.ok) {
      const reasons: string[] = Array.isArray(data?.reasons) ? data.reasons : [];
      throw new ApiError(data?.error ?? (reasons.length ? reasons.join('; ') : `HTTP ${res.status}`), res.status, reasons);
    }
    return data as T;
  }

  connection() {
    return this.call<Connection>('/api/connection');
  }
  datasets() {
    return this.call<{ datasets: string[] }>('/api/datasets');
  }
  models() {
    return this.call<{ models: string[]; default: string }>('/api/models');
  }
  schema(schemas?: string[]) {
    const q = schemas && schemas.length > 0 ? `?schemas=${schemas.map(encodeURIComponent).join(',')}` : '';
    return this.call<SchemaDump>(`/api/schema${q}`);
  }
  report(sel: { schemas: string[] } | { dataset: string }) {
    return this.call<Report>('/api/report', sel);
  }
  estimate(model: string, effort: string) {
    return this.call<Estimate>('/api/estimate', { model, effort });
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
    return this.call<Validation>('/api/validate', { files, comments });
  }
  export(body: { name: string; target: 'toml' | 'sql'; files: Files; comments: DraftComment[]; dismissed: number[]; force: boolean }) {
    return this.call<{ path: string; overwrote: string[]; backup: string | null }>('/api/export', body);
  }
  boardsEstimate(model: string, effort: string) {
    return this.call<Estimate>('/api/boards/estimate', { model, effort });
  }
  startBoards(model: string, effort: string) {
    return this.call<{ run_id: string }>('/api/boards/draft', { model, effort });
  }
  currentBoards() {
    return this.call<BoardsState>('/api/boards');
  }
  validateBoards(boards: BoardFile[]) {
    return this.call<BoardsState>('/api/boards/validate', { boards });
  }
}
