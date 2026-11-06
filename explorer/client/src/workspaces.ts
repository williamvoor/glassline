// Workspaces and saved Conversations (specs/workspaces/SPEC.md phase 1): the agent's REST routes
// and the hash routes. The explorer has one mode (ADR 0048): this is always on.

export interface WorkspaceInfo {
  name: string;
  description: string;
  status: 'available' | 'unavailable';
  reason?: string;
  url: string | null;
}

export interface SavedSummary {
  id: string;
  title: string;
  updated_at: string;
  turns: number;
}

export interface SavedTab {
  id: string; // t<n>
  kind: 'board' | 'question' | 'drill' | 'ask';
  title: string;
  level: string; // `<turn>-<level>`
  closed: boolean;
  log: {kind?: string; tag?: string; text?: string};
}

export interface SavedLogEntry {
  tab?: string; // a tab's entry
  in_place?: string; // a Control change on a Board
  on_tab?: string | null; // the Board's tab
  kind?: string;
  tag?: string;
  text?: string;
}

export interface SavedView {
  id: string;
  title: string;
  tabs: SavedTab[];
  log: SavedLogEntry[];
  selected: string | null;
  controls: Record<string, Record<string, unknown>>;
  levels: string[];
}

export interface SavedLevel {
  prose: string;
  a2ui: Record<string, unknown>[];
  changed: boolean;
  failed_results: string[];
  board_changed: boolean;
  board: {id: string; controls: {name: string; kind: string}[]} | null;
  tab: {id: string; kind: string; title: string};
  /** The model that answered (null: none, database only); absent for Levels saved before E10. */
  model?: string | null;
}

export const DATA_CHANGED = 'The data has changed since this answer; its wording may be out of date.';
export const BOARD_CHANGED = 'This Board has changed since.';

/** The agent's Workspace routes. Replaceable in tests. */
export interface WorkspaceApi {
  list(): Promise<WorkspaceInfo[]>;
  reload(): Promise<WorkspaceInfo[]>;
  conversations(ws: WorkspaceInfo): Promise<SavedSummary[]>;
  conversation(ws: WorkspaceInfo, id: string): Promise<SavedView | undefined>;
  level(ws: WorkspaceInfo, id: string, level: string, defaults?: boolean): Promise<SavedLevel>;
  remove(ws: WorkspaceInfo, id: string): Promise<void>;
  removeAll(ws: WorkspaceInfo): Promise<void>;
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as {error?: string};
      if (body?.error) message = body.error;
    } catch {
      // not JSON
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

function base(ws: WorkspaceInfo): string {
  if (!ws.url) throw new Error(`Workspace ${ws.name} is unavailable`);
  return ws.url.replace(/\/$/, '');
}

const enc = encodeURIComponent;

export function httpApi(agentUrl: string, fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis)): WorkspaceApi {
  const root = agentUrl.replace(/\/$/, '');
  const post = {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'};
  return {
    list: async () => json(await fetchImpl(`${root}/workspaces`)),
    reload: async () => {
      const r = await fetchImpl(`${root}/workspaces/reload`, post);
      if (r.status === 409) return ((await r.json()) as {workspaces: WorkspaceInfo[]}).workspaces;
      return json(r);
    },
    conversations: async ws => json(await fetchImpl(`${base(ws)}/conversations`)),
    conversation: async (ws, id) => {
      const r = await fetchImpl(`${base(ws)}/conversations/${enc(id)}`);
      return r.status === 404 ? undefined : json(r);
    },
    level: async (ws, id, level, defaults) => {
      const url = `${base(ws)}/conversations/${enc(id)}/levels/${enc(level)}`;
      return json(await (defaults ? fetchImpl(`${url}/defaults`, post) : fetchImpl(url)));
    },
    remove: async (ws, id) => {
      const r = await fetchImpl(`${base(ws)}/conversations/${enc(id)}`, {method: 'DELETE'});
      if (!r.ok && r.status !== 404) await json(r);
    },
    removeAll: async ws => {
      const r = await fetchImpl(`${base(ws)}/conversations`, {method: 'DELETE'});
      if (!r.ok) await json(r);
    },
  };
}

export interface Route {
  workspace?: string;
  conversation?: string;
}

/** `#/w/<name>` opens a Workspace, `#/w/<name>/c/<id>` a saved Conversation. */
export function parseRoute(hash: string): Route {
  const m = /^#\/w\/([a-z0-9][a-z0-9_-]*)(?:\/c\/([^/]+))?\/?$/.exec(hash);
  if (!m) return {};
  return {workspace: m[1], ...(m[2] ? {conversation: decodeURIComponent(m[2])} : {})};
}

export function routeHash(route: Route): string {
  if (!route.workspace) return '';
  return `#/w/${route.workspace}${route.conversation ? `/c/${enc(route.conversation)}` : ''}`;
}

/** The Workspace a route names when it is available, else the first available one. */
export function pickWorkspace(list: WorkspaceInfo[], wanted: string | undefined): WorkspaceInfo | undefined {
  const ok = list.filter(w => w.status === 'available' && w.url);
  return ok.find(w => w.name === wanted) ?? ok[0];
}
