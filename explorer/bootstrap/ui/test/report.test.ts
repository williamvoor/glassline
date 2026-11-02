import { afterEach, describe, expect, it } from 'vitest';

import '../src/report.js';
import type { BsReport } from '../src/report.js';
import { visibleObjects } from '../src/report.js';
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
    select.value = 'meta/muse-spark-1.3';
    select.dispatchEvent(new Event('change'));
    await settle(el);
    (el.querySelector('button.draft') as HTMLButtonElement).click();
    expect(seen).toEqual(['estimate meta/muse-wonder-1.0', 'estimate meta/muse-spark-1.3', 'draft meta/muse-spark-1.3']);
  });

  it('shows the estimate as a rough ceiling and progress with totals', async () => {
    const el = mount<BsReport>('bs-report', {
      report: REPORT,
      running: true,
      estimate: { model: 'm', prompt_chars: 300, input_tokens_est: 100, max_calls: 12, first_call_usd: 0.5, run_ceiling_usd: 6 },
      events: [
        { type: 'call', usage: { prompt_tokens: 1000, cached_tokens: 0, completion_tokens: 10, cost_usd: 0.25 } },
        { type: 'sql', purpose: 'count rows', sql: 'SELECT 1' },
        { type: 'call', usage: { prompt_tokens: 1200, cached_tokens: 1000, completion_tokens: 10, cost_usd: 0.05 } },
      ],
    });
    await settle(el);
    expect(el.querySelector('.estimate')!.textContent).toContain('Rough ceiling: $0.5000');
    expect(el.querySelector('.estimate')!.textContent).toContain('$6.00 if all');
    expect(el.querySelector('.progress')!.textContent).toContain('run_sql: count rows');
    expect(el.querySelector('.totals')!.textContent).toBe('2 calls, $0.3000 so far');
    expect((el.querySelector('button.draft') as HTMLButtonElement).disabled).toBe(true);
  });
});
