import {existsSync, readFileSync} from 'node:fs';
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

// M1 (2026-11-05): the selected Workspace and Settings buttons showed white text on a pale fill.
describe('selected top bar buttons', () => {
  const style = indexHtml.slice(indexHtml.indexOf('<style>') + '<style>'.length, indexHtml.indexOf('</style>'));
  const darkTokens = block(block(style, /@media \(prefers-color-scheme: dark\)\s*\{/), /:root\s*\{/);

  const luminance = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a: string, b: string) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };

  for (const theme of ['light', 'dark'] as const) {
    it(`have readable text in the ${theme} theme`, () => {
      document.head.innerHTML = `<style>${style}${theme === 'dark' ? `:root{${darkTokens}}` : ''}</style>`;
      document.body.innerHTML = `<explorer-app><header class="topbar">
        <div class="workspaces"><button class="workspace selected" aria-pressed="true">alpha</button></div>
        <button class="settings-toggle selected" aria-pressed="true">Settings</button></header></explorer-app>`;
      for (const button of document.querySelectorAll('button')) {
        const s = getComputedStyle(button);
        expect(s.color, button.textContent!).toMatch(/^#[0-9a-f]{6}$/i);
        expect(s.backgroundColor, button.textContent!).toMatch(/^#[0-9a-f]{6}$/i);
        expect(contrast(s.color, s.backgroundColor), button.textContent!).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
});

// M1 (2026-11-05): Review's dataset.toml / notes.md / glossary.md tabs sat on top of each other:
// they took the Explorer's `explorer-app .tab { display: flex }` and nothing laid out their strip.
describe('Settings tab strips', () => {
  it('lay their tabs out in a row', () => {
    const style = indexHtml.slice(indexHtml.indexOf('<style>') + '<style>'.length, indexHtml.indexOf('</style>'));
    document.head.innerHTML = `<style>${style}</style>`;
    document.body.innerHTML = `<explorer-app><explorer-settings><div role="tablist" aria-label="Drafted files">
      <button role="tab" class="tab" aria-selected="true">dataset.toml</button>
      <button role="tab" class="tab" aria-selected="false">notes.md</button></div></explorer-settings></explorer-app>`;
    const strip = getComputedStyle(document.querySelector('[role=tablist]')!);
    expect(strip.display).toBe('flex');
    expect(strip.flexDirection === '' || strip.flexDirection === 'row').toBe(true);
  });
});

describe('branding', () => {
  it('names the page Glassline and uses the mark as its icon', () => {
    expect(indexHtml).toMatch(/<title>Glassline<\/title>/);
    for (const file of ['favicon-32.png', 'favicon-64.png', 'apple-touch-icon.png', 'glassline-mark.png']) {
      expect(existsSync(resolve(process.cwd(), 'public', file)), file).toBe(true);
    }
    expect(indexHtml).toContain('<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png" />');
    expect(indexHtml).toContain('<link rel="apple-touch-icon" href="/apple-touch-icon.png" />');
  });
});
