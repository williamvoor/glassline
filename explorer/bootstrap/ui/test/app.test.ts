import { afterEach, describe, expect, it } from 'vitest';

import { Api } from '../src/api.js';
import '../src/app.js';
import type { BootstrapApp } from '../src/app.js';
import { REPORT, claim, fakeFetch, makeDraft, settle } from './helpers.js';

afterEach(() => (document.body.innerHTML = ''));

const current = (el: Element) => el.querySelector('.steps li.current')!.textContent!.trim();

describe('bootstrap-app (U4)', () => {
  it('goes connect -> report -> review with the current step marked', async () => {
    let polls = 0;
    const draft = makeDraft([claim('verified')]);
    const f = fakeFetch({
      'GET /api/connection': () => [200, { host: 'localhost', name: 'db', schemas: [{ name: 's', objects: 2 }] }],
      'POST /api/report': () => [200, REPORT],
      'POST /api/estimate': () => [200, { model: 'm', prompt_chars: 3, input_tokens_est: 1, max_calls: 12, first_call_usd: 0.1, run_ceiling_usd: 1.2 }],
      'POST /api/draft': () => [200, { run_id: 'r1' }],
      'GET /api/draft/r1/events': () =>
        ++polls === 1
          ? [200, { events: [{ type: 'sql', purpose: 'p', sql: 'SELECT 1' }], next: 1, done: false }]
          : [200, { events: [], next: 1, done: true, draft, validation: { errors: [], context_chars: 10 } }],
      'POST /api/validate': () => [200, { errors: [], context_chars: 10 }],
    });
    const el = document.createElement('bootstrap-app') as BootstrapApp;
    el.api = new Api(f.fn);
    el.pollMs = 0;
    document.body.appendChild(el);
    await settle(el);
    expect(current(el)).toBe('Connect');
    expect(el.querySelector('li.current')!.getAttribute('aria-current')).toBe('step');
    expect(el.textContent).toContain('db');
    const run = el.querySelector('bs-connect button.primary') as HTMLButtonElement;
    expect(run.disabled).toBe(true);
    (el.querySelector('input[name=schema]') as HTMLInputElement).click();
    await settle(el);
    (el.querySelector('bs-connect button.primary') as HTMLButtonElement).click();
    await settle(el);
    expect(current(el)).toBe('Report');
    expect(f.calls.find((c) => c.path === 'POST /api/report')!.body).toEqual({ schemas: ['s'] });
    expect(el.querySelector('.estimate')).not.toBeNull();
    (el.querySelector('button.draft') as HTMLButtonElement).click();
    await settle(el, 20);
    expect(current(el)).toBe('Review');
    expect(el.querySelector('bs-review textarea.file')).not.toBeNull();
    expect(f.calls.filter((c) => c.path.startsWith('GET /api/draft/r1/events')).map((c) => c.path)).toEqual([
      'GET /api/draft/r1/events?after=0', 'GET /api/draft/r1/events?after=1',
    ]);
  });

  it('keeps the edited draft across "Re-run report" and "Back to the draft"', async () => {
    const original = makeDraft([claim('verified')]);
    const stored = { ...makeDraft([claim('failed', { sql: 'SELECT 2' })]), files: { ...original.files, notes_md: '- edited\n' } };
    const f = fakeFetch({
      'GET /api/connection': () => [200, { host: 'h', name: 'db', schemas: [{ name: 's', objects: 2 }] }],
      'POST /api/report': () => [200, REPORT],
      'POST /api/estimate': () => [200, { model: 'm', prompt_chars: 3, input_tokens_est: 1, max_calls: 1, first_call_usd: 0, run_ceiling_usd: 0 }],
      'POST /api/validate': () => [200, { errors: [], context_chars: 10 }],
      'GET /api/draft': () => [200, { draft: stored, validation: { errors: [], context_chars: 10 } }],
    });
    const el = document.createElement('bootstrap-app') as BootstrapApp;
    el.api = new Api(f.fn);
    document.body.appendChild(el);
    await settle(el);
    el.selection = { schemas: ['s'] };
    el.draft = original;
    el.validation = { errors: [], context_chars: 10 };
    el.step = 'review';
    await settle(el);
    (el.querySelector('button.rerun-report') as HTMLButtonElement).click();
    await settle(el);
    expect(current(el)).toBe('Report');
    const paths = f.calls.map((c) => c.path);
    expect(paths.indexOf('POST /api/validate')).toBeLessThan(paths.indexOf('POST /api/report')); // edits saved first
    (el.querySelector('button.to-review') as HTMLButtonElement).click();
    await settle(el);
    expect(current(el)).toBe('Review');
    expect(f.calls.some((c) => c.path === 'GET /api/draft')).toBe(true);
    (el.querySelectorAll('[role=tab]')[1] as HTMLButtonElement).click();
    await settle(el);
    expect((el.querySelector('textarea.file') as HTMLTextAreaElement).value).toBe('- edited\n');
    expect(el.querySelector('li.claim .badge')!.textContent).toBe('failed');
  });

  it('shows a draft error and stays on the report', async () => {
    const f = fakeFetch({
      'GET /api/connection': () => [200, { host: 'h', name: 'db', schemas: [{ name: 's', objects: 2 }] }],
      'POST /api/report': () => [200, REPORT],
      'POST /api/estimate': () => [400, { error: 'no prices' }],
      'POST /api/draft': () => [200, { run_id: 'r1' }],
      'GET /api/draft/r1/events': () => [200, { events: [], next: 0, done: true, error: 'model call failed: AuthenticationError' }],
    });
    const el = document.createElement('bootstrap-app') as BootstrapApp;
    el.api = new Api(f.fn);
    el.pollMs = 0;
    document.body.appendChild(el);
    await settle(el);
    await el.runReport({ schemas: ['s'] });
    await settle(el);
    await el.startDraft('m', 'low');
    await settle(el);
    expect(current(el)).toBe('Report');
    expect(el.querySelector('.error')!.textContent).toContain('AuthenticationError');
  });

  it('reports on a server-side dataset without touching the filesystem', async () => {
    const f = fakeFetch({
      'GET /api/connection': () => [200, { host: 'h', name: 'db', schemas: [{ name: 's', objects: 2 }] }],
      'GET /api/datasets': () => [200, { datasets: ['demo'] }],
      'POST /api/report': () => [200, REPORT],
    });
    const el = document.createElement('bootstrap-app') as BootstrapApp;
    el.api = new Api(f.fn);
    document.body.appendChild(el);
    await settle(el);
    // Dataset mode is the default when the server holds datasets; the first is picked.
    const run = el.querySelector('bs-connect button.primary') as HTMLButtonElement;
    expect(run.disabled).toBe(false);
    run.click();
    await settle(el);
    expect(current(el)).toBe('Report');
    expect(f.calls.find((c) => c.path === 'POST /api/report')!.body).toEqual({ dataset: 'demo' });
  });

  it('tests the connection and shows the result on the connect screen', async () => {
    const f = fakeFetch({
      'GET /api/connection': () => [200, { host: 'h', name: 'db', schemas: [{ name: 's', objects: 2 }] }],
      'GET /api/datasets': () => [200, { datasets: [] }],
      'GET /api/models': () => [200, { models: ['m'], default: 'm' }],
    });
    const el = document.createElement('bootstrap-app') as BootstrapApp;
    el.api = new Api(f.fn);
    document.body.appendChild(el);
    await settle(el);
    const btn = [...el.querySelectorAll('bs-connect button')].find((b) =>
      b.textContent!.includes('Test connection'),
    ) as HTMLButtonElement;
    btn.click();
    await settle(el);
    const status = el.querySelector('bs-connect [role=status]')!.textContent!;
    expect(status).toContain('Connection OK');
    expect(status).toContain('db');
    expect(f.calls.filter((c) => c.path === 'GET /api/connection').length).toBe(2);
  });

  it('reads and shows the schema without running a report', async () => {
    const f = fakeFetch({
      'GET /api/connection': () => [200, { host: 'h', name: 'db', schemas: [{ name: 's', objects: 1 }] }],
      'GET /api/datasets': () => [200, { datasets: [] }],
      'GET /api/models': () => [200, { models: ['m'], default: 'm' }],
      'GET /api/schema': () => [200, {
        schemas: [{ name: 's', tables: [{
          schema: 's', name: 'mixed', kind: 'table', comment: 'A table.',
          columns: [{ name: 'id', type: 'integer', nullable: false, comment: null }],
        }] }],
      }],
    });
    const el = document.createElement('bootstrap-app') as BootstrapApp;
    el.api = new Api(f.fn);
    document.body.appendChild(el);
    await settle(el);
    const btn = [...el.querySelectorAll('bs-connect button')].find((b) =>
      b.textContent!.includes('Show schema'),
    ) as HTMLButtonElement;
    btn.click();
    await settle(el);
    expect(el.textContent).toContain('mixed');
    expect(el.textContent).toContain('integer');
    expect(el.textContent).toContain('no comment');
    expect([...el.querySelectorAll('bs-connect button')].some((b) => b.textContent!.includes('Download schema'))).toBe(true);
  });

  it('offers the server models for drafting, starting on the server default', async () => {
    const f = fakeFetch({
      'GET /api/connection': () => [200, { host: 'h', name: 'db', schemas: [{ name: 's', objects: 2 }] }],
      'GET /api/datasets': () => [200, { datasets: [] }],
      'GET /api/models': () => [200, { models: ['meta/muse-spark-1.3'], default: 'meta/muse-spark-1.3' }],
      'POST /api/report': () => [200, REPORT],
      'POST /api/estimate': () => [200, { model: 'm', prompt_chars: 3, input_tokens_est: 1, max_calls: 12, first_call_usd: 0.1, run_ceiling_usd: 1.2 }],
    });
    const el = document.createElement('bootstrap-app') as BootstrapApp;
    el.api = new Api(f.fn);
    document.body.appendChild(el);
    await settle(el);
    (el.querySelector('input[name=schema]') as HTMLInputElement).click();
    await settle(el);
    (el.querySelector('bs-connect button.primary') as HTMLButtonElement).click();
    await settle(el);
    const options = [...el.querySelectorAll('bs-report select.model option')].map((o) => (o as HTMLOptionElement).value);
    expect(options).toEqual(['meta/muse-spark-1.3']);
    expect(f.calls.find((c) => c.path === 'POST /api/estimate')!.body).toEqual({ model: 'meta/muse-spark-1.3', effort: 'low' });
  });

  it('falls back to schemas when the server holds no datasets', async () => {
    const f = fakeFetch({
      'GET /api/connection': () => [200, { host: 'h', name: 'db', schemas: [{ name: 's', objects: 2 }] }],
      'GET /api/datasets': () => [200, { datasets: [] }],
      'POST /api/report': () => [200, REPORT],
    });
    const el = document.createElement('bootstrap-app') as BootstrapApp;
    el.api = new Api(f.fn);
    document.body.appendChild(el);
    await settle(el);
    expect(el.querySelector('input[name=mode]')).toBeNull();
    (el.querySelector('input[name=schema]') as HTMLInputElement).click();
    await settle(el);
    (el.querySelector('bs-connect button.primary') as HTMLButtonElement).click();
    await settle(el);
    expect(f.calls.find((c) => c.path === 'POST /api/report')!.body).toEqual({ schemas: ['s'] });
  });
});
