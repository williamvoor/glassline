// The run card (Workspaces SPEC phase 3, "Models on show"): a Draft or Boards run as it happens — the
// model, each call's tokens (cached part lighter), each SQL step, and the cost against the ceiling —
// then one summary line once it's done.
import {html, nothing} from 'lit';

import {modelChip, modelName} from '../model-chip.js';
import {renderTrace, traceOn, type Prompts, type TraceEvent} from '../trace.js';
import type {DraftEvent, Estimate} from './types.js';

type Totals = {calls: number; prompt_tokens: number; cached_tokens: number; completion_tokens: number; cost_usd: number};

const fmt = new Intl.NumberFormat('en');
const compact = new Intl.NumberFormat('en', {notation: 'compact', maximumFractionDigits: 1});

/** Cents from $0.10 up; more digits below, where a few calls cost fractions of a cent. */
export function usd(n: number): string {
  return `$${n >= 0.1 ? n.toFixed(2) : n.toFixed(n < 0.01 ? 4 : 3)}`;
}

/** Totals over a run's `call` events. */
export function totalsOf(events: DraftEvent[]): Totals {
  const t: Totals = {calls: 0, prompt_tokens: 0, cached_tokens: 0, completion_tokens: 0, cost_usd: 0};
  for (const e of events) {
    if (e.type !== 'call') continue;
    t.calls += 1;
    t.prompt_tokens += e.usage.prompt_tokens;
    t.cached_tokens += e.usage.cached_tokens;
    t.completion_tokens += e.usage.completion_tokens;
    t.cost_usd += e.usage.cost_usd;
  }
  return t;
}

/** "7 calls · 81k tokens, 62% cached · $0.11"; the model's chip sits before it. */
export function summaryLine(t: Totals): string {
  const tokens = t.prompt_tokens + t.completion_tokens;
  const cached = t.prompt_tokens ? Math.round((t.cached_tokens / t.prompt_tokens) * 100) : 0;
  return `${t.calls} call${t.calls === 1 ? '' : 's'} · ${compact.format(tokens).toLowerCase()} tokens, ${cached}% cached · ${usd(t.cost_usd)}`;
}

export interface RunCard {
  model: string;
  events: DraftEvent[];
  running: boolean;
  estimate: Estimate | null;
  /** The finished run's totals, from the server (survives a reload); else the events' own. */
  done?: Totals | null;
  what: string; // "Drafting the Dataset"
}

/** The run's Trace (specs/trace), when the switch is on and the run was traced. */
function runTrace(events: DraftEvent[]) {
  if (!traceOn()) return nothing;
  const prompts: Prompts = {};
  const traced: TraceEvent[] = [];
  for (const e of events) {
    if (e.type !== 'trace') continue;
    if (e.event.kind === 'prompt') prompts[String(e.event['hash'])] = String(e.event['text'] ?? '');
    else traced.push(e.event);
  }
  return renderTrace(traced, prompts);
}

export function runCard(c: RunCard) {
  if (!c.running) {
    const t = c.done ?? (c.events.length ? totalsOf(c.events) : null);
    if (!t) return nothing;
    return html`<p class="run-summary" aria-label=${`${modelName(c.model)}: ${summaryLine(t)}`}>${modelChip(c.model)}
      <span>${summaryLine(t)}</span></p>${runTrace(c.events)}`;
  }
  const trace = runTrace(c.events);
  c = {...c, events: c.events.filter(e => e.type !== 'trace')};
  const t = totalsOf(c.events);
  const calls = c.events.filter(e => e.type === 'call');
  const widest = Math.max(1, ...calls.map(e => (e.type === 'call' ? e.usage.prompt_tokens + e.usage.completion_tokens : 0)));
  const ceiling = c.estimate?.run_ceiling_usd ?? 0;
  const fill = ceiling ? Math.min(100, (t.cost_usd / ceiling) * 100) : 0;
  return html`<section class="run-card" aria-live="polite" aria-label=${c.what}>
    <header>
      ${modelChip(c.model, {live: true})}
      <span class="run-what">${c.what}</span>
      <span class="run-calls"><strong>${t.calls}</strong> call${t.calls === 1 ? '' : 's'}</span>
    </header>
    <ol class="run-steps">
      ${c.events.map(e =>
        e.type === 'call'
          ? html`<li class="call">
              <span class="bar" style="width:${((e.usage.prompt_tokens + e.usage.completion_tokens) / widest) * 100}%">
                <span class="cached" style="flex:${e.usage.cached_tokens}"></span>
                <span class="fresh" style="flex:${e.usage.prompt_tokens - e.usage.cached_tokens}"></span>
                <span class="out" style="flex:${e.usage.completion_tokens}"></span>
              </span>
              <span class="call-text">${fmt.format(e.usage.prompt_tokens)} in (${fmt.format(e.usage.cached_tokens)} cached),
                ${fmt.format(e.usage.completion_tokens)} out · ${usd(e.usage.cost_usd)}</span>
            </li>`
          : e.type === 'sql'
            ? html`<li class="sql">${modelChip(null)}<span>${e.purpose}</span></li>`
            : e.type === 'trace'
              ? nothing
              : html`<li class="rejected">Rejected (${e.problems.length} problems): the model is fixing it</li>`,
      )}
    </ol>
    <div class="meter" role="img" aria-label=${`${usd(t.cost_usd)} spent of at most ${usd(ceiling)}`}>
      <span class="meter-fill" style="width:${fill}%"></span>
    </div>
    <p class="meter-text"><strong>${usd(t.cost_usd)}</strong> of at most ${usd(ceiling)}</p>
    ${trace}
  </section>`;
}
