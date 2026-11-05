// Model chips (Workspaces SPEC N12) and no pop-ups anywhere in the client (N11, ADR 0044).
import {readFileSync, readdirSync, statSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {html, render} from 'lit';
import {describe, expect, it} from 'vitest';

import {modelChip, modelName} from '../src/model-chip.js';

function chip(id: string | null) {
  const host = document.createElement('div');
  render(html`${modelChip(id)}`, host);
  return host.querySelector('.model-chip')!;
}

describe('N12: model chips', () => {
  it('gives each allowed model its name and token colour class, with the raw ID as the tooltip', () => {
    const wonder = chip('meta/muse-wonder-1.0');
    expect(wonder.textContent!.trim()).toBe('Muse Wonder');
    expect(wonder.classList.contains('model-wonder')).toBe(true);
    expect(wonder.getAttribute('title')).toBe('meta/muse-wonder-1.0');
    const spark = chip('meta/muse-spark-1.3');
    expect(spark.textContent!.trim()).toBe('Muse Spark');
    expect(spark.classList.contains('model-spark')).toBe(true);
  });

  it('shows an unknown model as a neutral chip with its ID, and no model as SQL', () => {
    const other = chip('meta/other-9');
    expect(other.textContent!.trim()).toBe('meta/other-9');
    expect(other.classList.contains('model-other')).toBe(true);
    expect(modelName('meta/other-9')).toBe('meta/other-9');
    const sql = chip(null);
    expect(sql.textContent!.trim()).toBe('SQL');
    expect(sql.classList.contains('model-sql')).toBe(true);
  });
});

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap(name => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === 'vendor' ? [] : sources(p);
    return /\.(ts|html)$/.test(name) ? [p] : [];
  });
}

describe('N11: no pop-ups', () => {
  it('uses no window.confirm, alert, prompt or <dialog> in the client', () => {
    const files = [...sources(resolve(process.cwd(), 'src')), resolve(process.cwd(), 'index.html')];
    expect(files.length).toBeGreaterThan(10);
    for (const f of files) {
      const text = readFileSync(f, 'utf-8');
      expect(text, f).not.toMatch(/\b(?:window\.)?(?:confirm|alert|prompt)\s*\(/);
      expect(text, f).not.toMatch(/<dialog\b|showModal\s*\(/);
    }
  });
});
