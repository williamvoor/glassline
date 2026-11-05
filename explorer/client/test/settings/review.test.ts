import { afterEach, describe, expect, it } from 'vitest';

import { Api } from '../../src/settings/api.js';
import '../../src/settings/review.js';
import type { BsReview } from '../../src/settings/review.js';
import { reviewBlockers, suggestedName } from '../../src/settings/review.js';
import type { Claim, Validation } from '../../src/settings/types.js';
import { claim, fakeFetch, makeDraft, mount, settle } from './helpers.js';

afterEach(() => (document.body.innerHTML = ''));

const OK: Validation = { errors: [], context_chars: 5000 };

function setup(claims: Claim[], routes: Parameters<typeof fakeFetch>[0] = {}, validation: Validation = OK) {
  const f = fakeFetch({ 'POST /api/validate': () => [200, validation], ...routes });
  const el = mount<BsReview>('bs-review', { api: new Api(f.fn), draft: makeDraft(claims), validation, validateDelay: 0 });
  const blockers: string[][] = [];
  el.addEventListener('review-blockers', (e) => blockers.push((e as CustomEvent<string[]>).detail));
  return { el, calls: f.calls, blockers, last: () => blockers.at(-1) ?? [] };
}

describe('reviewBlockers (U2, Workspaces SPEC E3)', () => {
  const base = { validation: OK, claims: [claim('verified'), claim('needs_review')], edited: new Set<number>() };
  it('allows leaving Review when everything is in order', () => expect(reviewBlockers(base)).toEqual([]));
  it('blocks on each reason; the name and Boards are no longer Review’s', () => {
    expect(reviewBlockers({ ...base, validation: { errors: ['bad'], context_chars: null } })).toEqual(['The files do not validate.']);
    expect(reviewBlockers({ ...base, validation: null })).toEqual(['The files have not been validated yet.']);
    expect(reviewBlockers({ ...base, claims: [claim('failed')] })[0]).toContain('Claim 1 is failed');
    expect(reviewBlockers({ ...base, claims: [claim('error')] })[0]).toContain('Claim 1 is error');
    expect(reviewBlockers({ ...base, claims: [claim(null)] })[0]).toContain('not checked');
    expect(reviewBlockers({ ...base, claims: [claim('failed', { dismissed: true })] })).toEqual([]);
    expect(reviewBlockers({ ...base, edited: new Set([0]) })).toEqual(['Re-run the edited claims.']);
  });
  it('suggests the name from dataset.toml', () => {
    expect(suggestedName(makeDraft([]))).toBe('demo');
    expect(suggestedName(null)).toBe('');
  });
});

describe('bs-review (U2)', () => {
  it('has no Export section of its own any more (E3)', async () => {
    const { el, last } = setup([claim('verified')]);
    await settle(el);
    expect(el.querySelector('button.export, input.name, input.force, fieldset.target, .rerun-report, .to-boards')).toBeNull();
    expect(last()).toEqual([]);
  });

  it('reports a failed claim as a blocker until it is dismissed', async () => {
    const { el, last } = setup([claim('failed')]);
    await settle(el);
    expect(last()[0]).toContain('Claim 1 is failed');
    (el.querySelector('button.dismiss') as HTMLButtonElement).click();
    await settle(el);
    expect(last()).toEqual([]);
  });

  it('reports validation errors, re-validating after an edit', async () => {
    let n = 0;
    const { el, calls, last } = setup([claim('verified')], {
      'POST /api/validate': () => [200, ++n === 1 ? { errors: ['dataset.toml is not valid TOML'], context_chars: null } : OK],
    });
    await settle(el);
    const file = el.querySelector('textarea.file') as HTMLTextAreaElement;
    file.value = 'name = ';
    file.dispatchEvent(new Event('input'));
    await settle(el);
    expect(el.querySelector('.errors')!.textContent).toContain('not valid TOML');
    expect(last()).toEqual(['The files do not validate.']);
    expect(calls.at(-1)!.body.files.dataset_toml).toBe('name = ');
    expect(calls.at(-1)!.headers['content-type']).toBe('application/json');
  });

  it('blocks after a claim edit until claims are re-run', async () => {
    const { el, calls, last } = setup([claim('verified')], {
      'POST /api/claims/verify': (body) => [200, { claims: body.claims.map((c: Claim) => ({ ...c, result: { state: 'verified', message: 'ok', actual: null } })) }],
    });
    await settle(el);
    const sql = el.querySelector('textarea.sql') as HTMLTextAreaElement;
    sql.value = 'SELECT 2';
    sql.dispatchEvent(new Event('input'));
    await settle(el);
    expect(last()).toEqual(['Re-run the edited claims.']);
    expect(el.querySelector('li.claim .badge')!.textContent).toBe('edited');
    (el.querySelector('button.rerun') as HTMLButtonElement).click();
    await settle(el);
    expect(calls.find((c) => c.path === 'POST /api/claims/verify')!.body.claims[0].sql).toBe('SELECT 2');
    expect(last()).toEqual([]);
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
    const fetchFn = (_input: string, _init?: RequestInit) => {
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
  });

  it('leave() saves pending edits, and says false (with the error shown) when saving fails', async () => {
    const { el, calls } = setup([claim('verified')]);
    await settle(el);
    expect(await el.leave()).toBe(true);
    expect(calls.at(-1)!.path).toBe('POST /api/validate');
    const bad = setup([claim('verified')], { 'POST /api/validate': () => [500, { error: 'unexpected OSError' }] });
    await settle(bad.el);
    expect(await bad.el.leave()).toBe(false);
    await settle(bad.el);
    expect(bad.el.querySelector('.error')!.textContent).toContain('unexpected OSError');
  });

  it('exportPayload() carries the edited comments and the dismissed claims', async () => {
    const { el } = setup([claim('verified'), claim('error', { dismissed: true })]);
    await settle(el);
    (el.querySelector('input.accept') as HTMLInputElement).click(); // un-accept the first comment
    await settle(el);
    const p = el.exportPayload();
    expect(p.dismissed).toEqual([1]);
    expect(p.comments[0]!.accepted).toBe(false);
    expect(p.files.dataset_toml).toBe('name = "demo"\n');
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
