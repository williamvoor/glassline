// Traces (explorer/specs/trace/SPEC.md, ADR 0053): the switch, and the plain-text view of Trace
// events. Every string here (prompts, messages, SQL, errors) is model or database text: it is
// rendered through Lit text bindings only, never as markdown or HTML.
import {html, nothing} from 'lit';

/** Request metadata (true: record a Trace) and final metadata ({level}). */
export const TRACE_KEY = 'explorerTrace';
/** Status-update metadata: one live Trace event. */
export const TRACE_EVENT_KEY = 'explorerTraceEvent';
export const TRACE_STORAGE_KEY = 'explorer.trace';

export type TraceEvent = Record<string, unknown> & {kind: string; seq?: number; at?: string};
export type Prompts = Record<string, string>;

/** The switch, per browser. Storage can be missing or throw (private windows): then off. */
export function traceOn(): boolean {
  try {
    return globalThis.localStorage?.getItem(TRACE_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

export function setTraceOn(on: boolean): void {
  try {
    if (on) globalThis.localStorage?.setItem(TRACE_STORAGE_KEY, '1');
    else globalThis.localStorage?.removeItem(TRACE_STORAGE_KEY);
  } catch {
    // not remembered; the switch still works for this page
  }
}

/** Live `prompt` events go into `prompts`; the rest are returned for the Tab or run. */
export function takePrompt(event: TraceEvent, prompts: Prompts): boolean {
  if (event.kind !== 'prompt') return false;
  if (typeof event['hash'] === 'string' && typeof event['text'] === 'string') prompts[event['hash']] = event['text'];
  return true;
}

const json = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v, null, 2));
const ms = (v: unknown) => (typeof v === 'number' ? `${v} ms` : '');

function messageText(m: Record<string, unknown>): string {
  const parts = [String(m['role'] ?? '?') + ':'];
  if (m['content'] != null && m['content'] !== '') parts.push(json(m['content']));
  if (Array.isArray(m['tool_calls'])) parts.push(json(m['tool_calls']));
  return parts.join('\n');
}

function modelEvent(e: TraceEvent, prompts: Prompts) {
  const tokens = (e['tokens'] ?? {}) as Record<string, unknown>;
  const reply = (e['reply'] ?? null) as Record<string, unknown> | null;
  const messages = (Array.isArray(e['messages']) ? e['messages'] : []) as Record<string, unknown>[];
  const hash = typeof e['prompt_hash'] === 'string' ? e['prompt_hash'] : '';
  const head = [String(e['model'] ?? 'model'), ms(e['latency_ms']),
    tokens['prompt'] != null ? `${tokens['prompt']} in` : tokens['prompt_tokens'] != null ? `${tokens['prompt_tokens']} in` : '',
    e['error'] ? `failed: ${e['error']}` : ''].filter(Boolean).join(' · ');
  return html`<li class="trace-model">
    <details><summary>Model call · ${head}</summary>
      ${hash && prompts[hash] ? html`<details class="trace-prompt"><summary>System prompt · ${hash}</summary><pre>${prompts[hash]}</pre></details>` : nothing}
      <p class="trace-label">Sent (messages ${Number(e['first_message'] ?? 0) + 1}–${e['messages_total'] ?? messages.length})</p>
      ${messages.map(m => html`<pre>${messageText(m)}</pre>`)}
      ${reply ? html`<p class="trace-label">Reply</p><pre>${messageText(reply)}</pre>` : nothing}
      ${Object.keys(tokens).length ? html`<p class="trace-label">Tokens</p><pre>${json(tokens)}</pre>` : nothing}
    </details>
  </li>`;
}

function sqlEvent(e: TraceEvent) {
  const head = [String(e['source'] ?? 'query'), ms(e['elapsed_ms']),
    e['ok'] ? `${e['row_count'] ?? 0} rows${e['truncated'] ? ' (truncated)' : ''}` : `failed${e['sqlstate'] ? ` (${e['sqlstate']})` : ''}`,
  ].filter(Boolean).join(' · ');
  return html`<li class=${e['ok'] ? 'trace-sql' : 'trace-sql failed'}>
    <details><summary>SQL · ${head}</summary>
      <pre>${String(e['sql'] ?? '')}</pre>
      ${Array.isArray(e['params']) && e['params'].length ? html`<p class="trace-label">Values</p><pre>${json(e['params'])}</pre>` : nothing}
      ${e['error'] ? html`<p class="trace-label">Error</p><pre>${String(e['error'])}</pre>` : nothing}
    </details>
  </li>`;
}

/** The Trace of one Tab or run, oldest first. */
export function renderTrace(events: TraceEvent[] | undefined, prompts: Prompts) {
  const shown = (events ?? []).filter(e => e.kind === 'model' || e.kind === 'sql');
  if (!shown.length) return nothing;
  const calls = shown.filter(e => e.kind === 'model').length;
  return html`<details class="trace">
    <summary>Trace · ${calls} model call${calls === 1 ? '' : 's'} · ${shown.length - calls} quer${shown.length - calls === 1 ? 'y' : 'ies'}</summary>
    <ol class="trace-events">${shown.map(e => (e.kind === 'model' ? modelEvent(e, prompts) : sqlEvent(e)))}</ol>
  </details>`;
}
