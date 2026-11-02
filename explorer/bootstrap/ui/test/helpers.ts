import type { Claim, ClaimState, Draft, Report } from '../src/types.js';

export const REPORT: Report = {
  schemas: ['s'],
  summary: {
    objects: 2, columns: 3, objects_with_comment: 1, columns_with_comment: 2, coverage_pct: 60,
    can_comment: false, context_chars: 1234, context_target: 20000, context_scope: 'schema_only',
  },
  objects: [
    {
      schema: 's', name: 'full', kind: 'table', comment: 'All commented.', est_rows: 10, can_comment: false,
      columns: [{ name: 'id', type: 'integer', nullable: false, comment: 'Key.', null_frac: 0, distinct_estimate: 10, most_common: [] }],
    },
    {
      schema: 's', name: 'part', kind: 'view', comment: null, est_rows: null, can_comment: false,
      columns: [
        { name: 'a', type: 'text', nullable: true, comment: 'Has one.', null_frac: 0.5, distinct_estimate: 2,
          most_common: [{ value: 'x', freq: 0.4 }] },
        { name: 'b', type: 'date', nullable: true, comment: null, null_frac: null, distinct_estimate: null, most_common: [] },
      ],
    },
  ],
};

export function claim(state: ClaimState | null, extra: Partial<Claim> = {}): Claim {
  return {
    text: `claim ${state ?? 'unchecked'}`, file: 'notes.md', sql: 'SELECT 1', expect: { kind: 'none' }, dismissed: false,
    result: state ? { state, message: `message ${state}`, actual: null } : null, ...extra,
  };
}

export function makeDraft(claims: Claim[]): Draft {
  return {
    model: 'meta/muse-wonder-1.0',
    report: REPORT,
    files: { dataset_toml: 'name = "demo"\n', notes_md: '- n\n', glossary_md: '- g\n' },
    comments: [
      { object: 's.part', column: '', comment: 'A view.', kind: 'new', accepted: true },
      { object: 's.part', column: 'a', comment: 'Better.', kind: 'rewrite', accepted: false },
    ],
    claims,
    usage: { totals: { calls: 1, prompt_tokens: 1, cached_tokens: 0, completion_tokens: 1, cost_usd: 0.01 } },
  };
}

type Handler = (body: any) => [number, unknown];

/** A fetch stand-in that routes by method and path, and records every call. */
export function fakeFetch(routes: Record<string, Handler>) {
  const calls: { path: string; body: any; headers: Record<string, string> }[] = [];
  const fn = async (input: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const path = input.split('?')[0];
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: `${method} ${input}`, body, headers: (init?.headers ?? {}) as Record<string, string> });
    const handler = routes[`${method} ${path}`];
    if (!handler) return new Response(JSON.stringify({ error: `no route ${method} ${path}` }), { status: 404 });
    const [status, data] = handler(body);
    return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } });
  };
  return { fn, calls };
}

export async function settle(el: Element, rounds = 10) {
  for (let i = 0; i < rounds; i++) {
    await new Promise((ok) => setTimeout(ok, 5));
    const all = [el, ...el.querySelectorAll('*')] as any[];
    await Promise.all(all.filter((e) => e.updateComplete).map((e) => e.updateComplete));
  }
}

export function mount<T extends HTMLElement>(tag: string, props: Partial<T> = {}): T {
  const el = document.createElement(tag) as T;
  Object.assign(el, props);
  document.body.appendChild(el);
  return el;
}
