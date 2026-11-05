import { afterEach, describe, expect, it } from 'vitest';

import '../../src/settings/report.js';
import type { BsReport } from '../../src/settings/report.js';
import { visibleObjects } from '../../src/settings/report.js';
import { REPORT, mount, settle } from './helpers.js';

afterEach(() => (document.body.innerHTML = ''));

describe('bs-report (U1)', () => {
  it('shows the summary numbers', async () => {
    const el = mount<BsReport>('bs-report', { report: REPORT });
    await settle(el);
    expect(el.querySelector('.objects')!.textContent).toBe('2');
    expect(el.querySelector('.columns')!.textContent).toBe('3');
    expect(el.querySelector('.coverage')!.textContent).toBe('60%');
    expect(el.querySelector('.context')!.textContent).toContain('1,234 / 20,000');
    expect(el.querySelector('.context')!.textContent).toContain('schema only');
  });

  it('uses real table markup and marks missing comments', async () => {
    const el = mount<BsReport>('bs-report', { report: REPORT });
    await settle(el);
    const tables = el.querySelectorAll('table.object');
    expect(tables.length).toBe(2);
    expect(tables[0].querySelectorAll('thead th[scope=col]').length).toBe(7);
    expect(tables[1].querySelectorAll('tbody th[scope=row]').length).toBe(2);
    expect(tables[1].querySelector('caption .missing')!.textContent).toBe('no comment');
    expect(tables[1].querySelector('caption .badge')!.textContent).toBe('1/3 commented');
    expect(tables[0].querySelector('caption .badge')!.className).toContain('ok');
  });

  it('filters to missing comments only', async () => {
    const el = mount<BsReport>('bs-report', { report: REPORT });
    await settle(el);
    (el.querySelector('.only-missing') as HTMLInputElement).click();
    await settle(el);
    const tables = el.querySelectorAll('table.object');
    expect(tables.length).toBe(1);
    expect(tables[0].querySelector('caption code')!.textContent).toBe('s.part');
    expect([...tables[0].querySelectorAll('tbody th')].map((t) => t.textContent)).toEqual(['b']);
    expect(visibleObjects(REPORT, false).length).toBe(2);
  });

  it('asks for an estimate on load and on model change, and starts a draft', async () => {
    const seen: string[] = [];
    const el = document.createElement('bs-report') as BsReport;
    el.report = REPORT;
    el.addEventListener('estimate', (e) => seen.push(`estimate ${(e as CustomEvent).detail.model}`));
    el.addEventListener('draft', (e) => seen.push(`draft ${(e as CustomEvent).detail.model}`));
    document.body.appendChild(el);
    await settle(el);
    const select = el.querySelector('select.model') as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(['Muse Wonder', 'Muse Spark']); // display names
    select.value = 'meta/muse-spark-1.3';
    select.dispatchEvent(new Event('change'));
    await settle(el);
    // No ceiling yet: the button that spends money stays off (S5).
    expect((el.querySelector('button.draft') as HTMLButtonElement).disabled).toBe(true);
    // N18 (E14): a ceiling for another model (the reply to the first request) never sits on the button.
    el.estimate = { model: 'meta/muse-wonder-1.0', prompt_chars: 3, input_tokens_est: 1, max_calls: 12, first_call_usd: 0.1, run_ceiling_usd: 9 };
    await settle(el);
    expect((el.querySelector('button.draft') as HTMLButtonElement).disabled).toBe(true);
    expect(el.querySelector('button.draft')!.textContent).not.toContain('$9.00');
    expect(el.querySelector('.estimate')!.textContent).not.toContain('$9.00');
    el.estimate = { model: 'meta/muse-spark-1.3', prompt_chars: 3, input_tokens_est: 1, max_calls: 12, first_call_usd: 0.1, run_ceiling_usd: 1.9 };
    await settle(el);
    expect(el.querySelector('button.draft')!.textContent!.trim()).toBe('Draft (at most $1.90)');
    expect(el.querySelector('.draft-panel .model-chip.model-spark')).not.toBeNull();
    (el.querySelector('button.draft') as HTMLButtonElement).click();
    expect(seen).toEqual(['estimate meta/muse-wonder-1.0', 'estimate meta/muse-spark-1.3', 'draft meta/muse-spark-1.3']);
  });

  it('asks on a card before drafting over an existing Draft (N7)', async () => {
    const seen: string[] = [];
    const el = mount<BsReport>('bs-report', {
      report: REPORT, hasDraft: true,
      estimate: { model: 'meta/muse-wonder-1.0', prompt_chars: 3, input_tokens_est: 1, max_calls: 12, first_call_usd: 0.1, run_ceiling_usd: 1.9 },
    });
    el.addEventListener('draft', () => seen.push('draft'));
    await settle(el);
    expect(el.querySelector('button.draft')!.textContent!.trim()).toBe('Draft again (at most $1.90)');
    (el.querySelector('button.draft') as HTMLButtonElement).click();
    await settle(el);
    expect(seen).toEqual([]);
    expect(el.querySelector('.confirm-card')!.textContent).toContain('replaces the Draft and its Boards');
    (el.querySelector('.card-confirm') as HTMLButtonElement).click();
    expect(seen).toEqual(['draft']);
  });

  it('shows the summary line of a finished Draft (N14)', async () => {
    const el = mount<BsReport>('bs-report', {
      report: REPORT, draftModel: 'meta/muse-wonder-1.0',
      doneTotals: { calls: 7, prompt_tokens: 80000, cached_tokens: 49600, completion_tokens: 1000, cost_usd: 0.11 },
    });
    await settle(el);
    // The chip names the model once; the line is the spec's "7 calls · 81k tokens, 62% cached · $0.11".
    expect(el.querySelector('.run-summary')!.textContent!.replace(/\s+/g, ' ').trim())
      .toBe('Muse Wonder 7 calls · 81k tokens, 62% cached · $0.11');
    expect(el.querySelector('.run-summary .model-chip.model-wonder')).not.toBeNull();
  });

  it('shows the estimate as a ceiling and the live run card', async () => {
    const el = mount<BsReport>('bs-report', {
      report: REPORT,
      running: true,
      estimate: { model: 'meta/muse-wonder-1.0', prompt_chars: 300, input_tokens_est: 100, max_calls: 12, first_call_usd: 0.5, run_ceiling_usd: 6 },
      events: [
        { type: 'call', usage: { prompt_tokens: 1000, cached_tokens: 0, completion_tokens: 10, cost_usd: 0.25 } },
        { type: 'sql', purpose: 'count rows', sql: 'SELECT 1' },
        { type: 'call', usage: { prompt_tokens: 1200, cached_tokens: 1000, completion_tokens: 10, cost_usd: 0.05 } },
      ],
    });
    await settle(el);
    expect(el.querySelector('.estimate')!.textContent).toContain('At most $0.50 for the first call');
    expect(el.querySelector('.estimate')!.textContent).toContain('$6.00 if all');
    expect(el.querySelector('.estimate')!.textContent).toContain('These are ceilings');
    const card = el.querySelector('.run-card')!;
    expect(card.querySelector('li.sql')!.textContent).toContain('count rows');
    expect(card.querySelectorAll('li.call')).toHaveLength(2);
    expect(card.querySelector('.run-calls')!.textContent!.replace(/\s+/g, ' ').trim()).toBe('2 calls');
    expect(card.querySelector('.meter-text')!.textContent!.replace(/\s+/g, ' ').trim()).toBe('$0.30 of at most $6.00');
    expect((card.querySelector('.meter-fill') as HTMLElement).style.width).toBe('5%');
    // The second call's bar shows its cached part (1000 of 1200 prompt tokens).
    const bar = card.querySelectorAll('li.call .bar')[1]!;
    expect((bar.querySelector('.cached') as HTMLElement).style.flex).toContain('1000');
    expect((el.querySelector('button.draft') as HTMLButtonElement).disabled).toBe(true);
  });
});
