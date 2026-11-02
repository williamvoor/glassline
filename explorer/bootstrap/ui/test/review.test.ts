import { afterEach, describe, expect, it } from 'vitest';

import { Api } from '../src/api.js';
import '../src/review.js';
import type { BsReview } from '../src/review.js';
import { exportBlockers } from '../src/review.js';
import type { Claim, Validation } from '../src/types.js';
import { claim, fakeFetch, makeDraft, mount, settle } from './helpers.js';

afterEach(() => (document.body.innerHTML = ''));

const OK: Validation = { errors: [], context_chars: 5000 };

function setup(claims: Claim[], routes: Parameters<typeof fakeFetch>[0] = {}, validation: Validation = OK) {
  const f = fakeFetch({ 'POST /api/validate': () => [200, validation], ...routes });
  const el = mount<BsReview>('bs-review', { api: new Api(f.fn), draft: makeDraft(claims), validation, validateDelay: 0 });
  return { el, calls: f.calls };
}

const exportButton = (el: Element) => el.querySelector('button.export') as HTMLButtonElement;

describe('exportBlockers (U2)', () => {
  const base = { validation: OK, claims: [claim('verified'), claim('needs_review')], edited: new Set<number>(), name: 'demo' };
  it('allows export when everything is in order', () => expect(exportBlockers(base)).toEqual([]));
  it('blocks on each reason', () => {
    expect(exportBlockers({ ...base, name: ' ' })).toEqual(['Give the dataset a name.']);
    expect(exportBlockers({ ...base, validation: { errors: ['bad'], context_chars: null } })).toEqual(['The files do not validate.']);
    expect(exportBlockers({ ...base, validation: null })).toEqual(['The files have not been validated yet.']);
    expect(exportBlockers({ ...base, claims: [claim('failed')] })[0]).toContain('Claim 1 is failed');
    expect(exportBlockers({ ...base, claims: [claim('error')] })[0]).toContain('Claim 1 is error');
    expect(exportBlockers({ ...base, claims: [claim(null)] })[0]).toContain('not checked');
    expect(exportBlockers({ ...base, claims: [claim('failed', { dismissed: true })] })).toEqual([]);
    expect(exportBlockers({ ...base, edited: new Set([0]) })).toEqual(['Re-run the edited claims.']);
  });
});

describe('bs-review (U2)', () => {
  it('enables export for a clean draft and takes the name from dataset.toml', async () => {
    const { el } = setup([claim('verified')]);
    await settle(el);
    expect((el.querySelector('input.name') as HTMLInputElement).value).toBe('demo');
    expect(exportButton(el).disabled).toBe(false);
    expect(el.querySelector('input[value=toml]') as HTMLInputElement).toHaveProperty('checked', true); // can_comment false
  });

  it('blocks export on a failed claim until it is dismissed', async () => {
    const { el } = setup([claim('failed')]);
    await settle(el);
    expect(exportButton(el).disabled).toBe(true);
    expect(el.querySelector('.blockers')!.textContent).toContain('Claim 1 is failed');
    (el.querySelector('button.dismiss') as HTMLButtonElement).click();
    await settle(el);
    expect(exportButton(el).disabled).toBe(false);
  });

  it('blocks export while validation has errors, re-validating after an edit', async () => {
    let n = 0;
    const { el, calls } = setup([claim('verified')], {
      'POST /api/validate': () => [200, ++n === 1 ? { errors: ['dataset.toml is not valid TOML'], context_chars: null } : OK],
    });
    await settle(el);
    const file = el.querySelector('textarea.file') as HTMLTextAreaElement;
    file.value = 'name = ';
    file.dispatchEvent(new Event('input'));
    await settle(el);
    expect(el.querySelector('.errors')!.textContent).toContain('not valid TOML');
    expect(exportButton(el).disabled).toBe(true);
    expect(calls.at(-1)!.body.files.dataset_toml).toBe('name = ');
    expect(calls.at(-1)!.headers['content-type']).toBe('application/json');
  });

  it('blocks export after a claim edit until claims are re-run', async () => {
    const { el, calls } = setup([claim('verified')], {
      'POST /api/claims/verify': (body) => [200, { claims: body.claims.map((c: Claim) => ({ ...c, result: { state: 'verified', message: 'ok', actual: null } })) }],
    });
    await settle(el);
    const sql = el.querySelector('textarea.sql') as HTMLTextAreaElement;
    sql.value = 'SELECT 2';
    sql.dispatchEvent(new Event('input'));
    await settle(el);
    expect(exportButton(el).disabled).toBe(true);
    expect(el.querySelector('li.claim .badge')!.textContent).toBe('edited');
    (el.querySelector('button.rerun') as HTMLButtonElement).click();
    await settle(el);
    expect(calls.find((c) => c.path === 'POST /api/claims/verify')!.body.claims[0].sql).toBe('SELECT 2');
    expect(exportButton(el).disabled).toBe(false);
  });

  it('re-run posts every claim and shows the new states (U3)', async () => {
    const { el, calls } = setup([claim('failed'), claim('error'), claim('needs_review')], {
      'POST /api/claims/verify': (body) => [200, {
        claims: body.claims.map((c: Claim, i: number) => ({ ...c, result: { state: ['verified', 'failed', 'needs_review'][i], message: 'm', actual: null } })),
      }],
    });
    await settle(el);
    (el.querySelector('button.rerun') as HTMLButtonElement).click();
    await settle(el);
    expect(calls.find((c) => c.path === 'POST /api/claims/verify')!.body.claims).toHaveLength(3);
    expect([...el.querySelectorAll('li.claim .badge')].map((b) => b.textContent)).toEqual(['verified', 'failed', 'needs review']);
  });

  it('ignores a validation reply that arrives after a newer one', async () => {
    let resolveFirst: (v: Response) => void = () => {};
    let n = 0;
    const fetchFn = (input: string, init?: RequestInit) => {
      n += 1;
      if (n === 1) return new Promise<Response>((ok) => (resolveFirst = ok)); // the older, slow request
      return Promise.resolve(new Response(JSON.stringify({ errors: ['newer: invalid'], context_chars: null }), { status: 200 }));
    };
    const el = mount<BsReview>('bs-review', { api: new Api(fetchFn), draft: makeDraft([claim('verified')]), validation: OK, validateDelay: 0 });
    await settle(el);
    const first = el.validate();
    await el.validate();
    resolveFirst(new Response(JSON.stringify(OK), { status: 200 }));
    await first;
    await settle(el);
    expect(el.querySelector('.errors')!.textContent).toContain('newer: invalid');
    expect(exportButton(el).disabled).toBe(true);
  });

  it('stays on review when saving the edits fails', async () => {
    const { el } = setup([claim('verified')], { 'POST /api/validate': () => [500, { error: 'unexpected OSError' }] });
    let left = false;
    el.addEventListener('rerun-report', () => (left = true));
    await settle(el);
    await el.leaveForReport();
    await settle(el);
    expect(left).toBe(false);
    expect(el.querySelector('.error')!.textContent).toContain('unexpected OSError');
  });

  it('blocks export with an empty name', async () => {
    const { el } = setup([claim('verified')]);
    await settle(el);
    const name = el.querySelector('input.name') as HTMLInputElement;
    name.value = '';
    name.dispatchEvent(new Event('input'));
    await settle(el);
    expect(exportButton(el).disabled).toBe(true);
  });

  it('sends edits and dismissals on export, and shows the server refusal reasons', async () => {
    const { el, calls } = setup([claim('verified'), claim('error', { dismissed: true })], {
      'POST /api/export': () => [422, { reasons: ['/x/demo already exists'] }],
    });
    await settle(el);
    (el.querySelector('input.accept') as HTMLInputElement).click(); // un-accept the first comment
    await settle(el);
    exportButton(el).click();
    await settle(el);
    const sent = calls.find((c) => c.path === 'POST /api/export')!.body;
    expect(sent.dismissed).toEqual([1]);
    expect(sent.name).toBe('demo');
    expect(sent.target).toBe('toml');
    expect(sent.comments[0].accepted).toBe(false);
    expect(el.querySelector('.refused')!.textContent).toContain('already exists');
  });

  it('tabs switch the file being edited', async () => {
    const { el } = setup([claim('verified')]);
    await settle(el);
    const tabs = el.querySelectorAll('[role=tab]');
    (tabs[1] as HTMLButtonElement).click();
    await settle(el);
    expect(tabs[1].getAttribute('aria-selected')).toBe('true');
    expect((el.querySelector('textarea.file') as HTMLTextAreaElement).value).toBe('- n\n');
  });
});
