import { afterEach, describe, expect, it } from 'vitest';

import '../../src/settings/claims.js';
import type { BsClaims } from '../../src/settings/claims.js';
import { claimState } from '../../src/settings/claims.js';
import { claim, mount, settle } from './helpers.js';

afterEach(() => (document.body.innerHTML = ''));

describe('bs-claims (U3)', () => {
  it('shows a badge for each state', async () => {
    const claims = [claim('verified'), claim('failed'), claim('error'), claim('needs_review'), claim(null)];
    const el = mount<BsClaims>('bs-claims', { claims, edited: new Set([4]) });
    await settle(el);
    const badges = [...el.querySelectorAll('li.claim .badge')].map((b) => [b.textContent, b.className]);
    expect(badges.map(([t]) => t)).toEqual(['verified', 'failed', 'error', 'needs review', 'edited']);
    expect(badges[1][1]).toContain('state-failed');
    expect(el.querySelector('li.claim .message')!.textContent).toBe('message verified');
    expect(claimState(claim(null), false)).toBe('not checked');
  });

  it('fires dismiss, edit and rerun events without changing claims itself', async () => {
    const claims = [claim('failed')];
    const el = mount<BsClaims>('bs-claims', { claims });
    const seen: unknown[] = [];
    for (const name of ['claim-dismiss', 'claim-edit', 'claims-rerun']) {
      el.addEventListener(name, (e) => seen.push([name, (e as CustomEvent).detail]));
    }
    await settle(el);
    (el.querySelector('button.dismiss') as HTMLButtonElement).click();
    const sql = el.querySelector('textarea.sql') as HTMLTextAreaElement;
    sql.value = 'SELECT 2';
    sql.dispatchEvent(new Event('input'));
    (el.querySelector('button.rerun') as HTMLButtonElement).click();
    expect(seen).toEqual([
      ['claim-dismiss', { index: 0, dismissed: true }],
      ['claim-edit', { index: 0, sql: 'SELECT 2' }],
      ['claims-rerun', null],
    ]);
    expect(claims[0].dismissed).toBe(false);
  });

  it('shows dismissed claims and blocks rerun while an expectation is invalid JSON', async () => {
    const el = mount<BsClaims>('bs-claims', { claims: [claim('error', { dismissed: true })], badExpect: new Set([0]) });
    await settle(el);
    expect(el.querySelector('button.dismiss')!.textContent!.trim()).toBe('Undismiss');
    expect(el.querySelector('li.claim')!.className).toContain('dismissed');
    expect((el.querySelector('button.rerun') as HTMLButtonElement).disabled).toBe(true);
    expect(el.querySelector('textarea.expect')!.getAttribute('aria-invalid')).toBe('true');
  });
});
