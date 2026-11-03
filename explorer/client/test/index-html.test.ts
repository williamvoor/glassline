import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

// Tests run from explorer/client (npm test).
const indexHtml = readFileSync(resolve(process.cwd(), 'index.html'), 'utf-8');

describe('index.html', () => {
  it('loads the Material Symbols font that the basic Icon component renders with', () => {
    // Without it, Icon shows its ligature name ("shopping_cart") as plain text.
    // No icon_names filter: the model may pick any icon in the catalog.
    expect(indexHtml).toMatch(
      /<link rel="stylesheet" href="https:\/\/fonts\.googleapis\.com\/css2\?family=Material\+Symbols\+Outlined[^"&]*"/,
    );
  });
});

// SPEC §16 (L1): one set of tokens, light and dark, drives the page, A2UI and the charts.
const TOKENS = [
  '--ink', '--on-ink', '--fg', '--muted', '--faint', '--line', '--line-strong', '--canvas', '--panel', '--soft',
  '--primary', '--on-primary', '--accent', '--on-accent', '--accent-soft', '--accent-line', '--error', '--error-bg', '--warn-bg',
  '--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5', '--chart-6',
];

/** The declarations of the first rule block whose selector matches. */
function block(css: string, selector: RegExp): string {
  const match = selector.exec(css);
  if (!match) throw new Error(`no block for ${selector}`);
  let depth = 0;
  const start = css.indexOf('{', match.index);
  for (let i = start; i < css.length; i++) {
    if (css[i] === '{') depth++;
    if (css[i] === '}' && --depth === 0) return css.slice(start + 1, i);
  }
  throw new Error('unbalanced');
}

const defined = (decls: string) => new Set([...decls.matchAll(/(--[a-z0-9-]+)\s*:/g)].map(m => m[1]));

describe('theme tokens', () => {
  const css = indexHtml.slice(indexHtml.indexOf('<style>'), indexHtml.indexOf('</style>'));
  const light = defined(block(css, /^\s*:root\s*\{/m));
  const dark = defined(block(block(css, /@media \(prefers-color-scheme: dark\)\s*\{/), /:root\s*\{/));

  it('defines every token for light and for dark', () => {
    for (const t of TOKENS) {
      expect(light, `light ${t}`).toContain(t);
      expect(dark, `dark ${t}`).toContain(t);
    }
    expect(light).toContain('--sans');
    expect(light).toContain('--mono');
  });

  it('maps A2UI variables from the tokens', () => {
    const root = block(css, /^\s*:root\s*\{/m);
    for (const v of ['--a2ui-color-primary', '--a2ui-color-on-background', '--a2ui-color-border', '--a2ui-font-family-title',
      '--a2ui-card-background', '--a2ui-card-border', '--a2ui-card-box-shadow', '--a2ui-text-caption-color']) {
      expect(root, v).toMatch(new RegExp(`${v}\\s*:\\s*[^;]*(var\\(--|none)`));
    }
  });

  it('follows the OS scheme for A2UI too', () => {
    expect(block(css, /^\s*:root\s*\{/m)).toMatch(/color-scheme:\s*light dark/);
  });
});
