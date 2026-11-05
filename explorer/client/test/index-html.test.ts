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

// SPEC §22 step 1 (P5): the chart tooltip is styled from the tokens, with no fixed colours.
describe('chart tooltip', () => {
  const style = indexHtml.slice(indexHtml.indexOf('<style>') + '<style>'.length, indexHtml.indexOf('</style>'));
  const rules = [...style.matchAll(/#vg-tooltip-element\.explorer-theme[^{]*\{([^}]*)\}/g)];

  it('has themed rules for the box, keys and values', () => {
    expect(rules.length).toBeGreaterThanOrEqual(3);
    const box = rules[0]![1]!;
    for (const prop of ['background', 'border', 'color', 'font']) expect(box, prop).toMatch(new RegExp(`${prop}[^;]*var\\(--`));
  });

  it('uses no hex colours (the shadow is a fixed translucent black, fine in both schemes)', () => {
    for (const [, decls] of rules) expect(decls).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  });

  it('greys the keys and sets the values in mono', () => {
    const key = style.match(/#vg-tooltip-element\.explorer-theme td\.key\s*\{([^}]*)\}/)?.[1] ?? '';
    const value = style.match(/#vg-tooltip-element\.explorer-theme td\.value\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(key).toMatch(/color:\s*var\(--muted\)/);
    expect(value).toMatch(/font-family:\s*var\(--mono\)/);
  });
});

// SPEC §22 step 2 (P11): the Metric value is a mono KPI figure.
describe('Metric value', () => {
  const style = indexHtml.slice(indexHtml.indexOf('<style>') + '<style>'.length, indexHtml.indexOf('</style>'));
  const rule = style.match(/explorer-app \.a2ui-text h2\s*\{([^}]*)\}/)?.[1] ?? '';

  it('is mono 34 px, weight 600, tabular, tightly tracked', () => {
    expect(rule).toMatch(/font-family:\s*var\(--mono\)/);
    expect(rule).toMatch(/font-size:\s*34px/);
    expect(rule).toMatch(/font-weight:\s*600/);
    expect(rule).toMatch(/font-variant-numeric:\s*tabular-nums/);
    expect(rule).toMatch(/letter-spacing:\s*-0\.03em/);
  });
});

// SPEC §22 step 3 (P11): Card hierarchy.
describe('Card hierarchy', () => {
  const style = indexHtml.slice(indexHtml.indexOf('<style>') + '<style>'.length, indexHtml.indexOf('</style>'));
  const rule = (selector: string): string => {
    const at = style.indexOf(`${selector} {`);
    return at < 0 ? '' : style.slice(at, style.indexOf('}', at));
  };
  const media = (query: string): string => {
    let out = '';
    for (let at = style.indexOf(query); at >= 0; at = style.indexOf(query, at + 1)) out += style.slice(at, style.indexOf('}\n', at));
    return out;
  };

  it('turns only the first h4 of a Card into a mono uppercase muted label', () => {
    const title = rule('explorer-app .a2ui-card > * > :first-child > .a2ui-text-h4 h4');
    expect(title).toMatch(/font:\s*600 11\.5px\/[\d.]+ var\(--mono\)/);
    expect(title).toMatch(/letter-spacing:\s*0\.08em/);
    expect(title).toMatch(/text-transform:\s*uppercase/);
    expect(title).toMatch(/color:\s*var\(--muted\)/);
    expect(rule('explorer-app .a2ui-text h4')).toMatch(/font-size:\s*16px/); // prose h4s keep their style
  });

  it('pads Cards 14px 16px with a soft ink shadow at rest and a stronger one on hover, without moving', () => {
    expect(style).toMatch(/--a2ui-card-padding:\s*14px 16px;/);
    expect(style).toMatch(/--a2ui-card-box-shadow:\s*0 1px 2px color-mix\(in srgb, var\(--ink\) 4%, transparent\),\s*0 1px 3px color-mix\(in srgb, var\(--ink\) 6%, transparent\);/);
    const hover = rule('explorer-app .a2ui-card:hover');
    expect(hover).toMatch(/box-shadow:\s*0 2px 8px color-mix\(in srgb, var\(--ink\) 8%, transparent\)/);
    expect(hover).not.toMatch(/transform|translate|margin/);
    expect(rule('explorer-app .a2ui-card')).toMatch(/transition:\s*box-shadow 150ms/);
  });

  it('has no Card shadow in dark mode, and a strong border on hover there', () => {
    const dark = media('@media (prefers-color-scheme: dark)');
    expect(dark).toMatch(/--a2ui-card-box-shadow:\s*none/);
    expect(dark).toMatch(/explorer-app \.a2ui-card:hover\s*\{[^}]*box-shadow:\s*none[^}]*border-color:\s*var\(--line-strong\)/);
  });

  it('drops the Card transition under reduced motion', () => {
    expect(media('@media (prefers-reduced-motion: reduce)')).toMatch(/explorer-app \.a2ui-card\s*\{\s*transition:\s*none/);
  });

  it('greys the SQL details summary', () => {
    expect(rule('details.sql-details summary')).toMatch(/color:\s*var\(--faint\)/);
  });
});

// SPEC §22 step 5 (P11): DataGrid share bars.
describe('DataGrid share bars', () => {
  const style = indexHtml.slice(indexHtml.indexOf('<style>') + '<style>'.length, indexHtml.indexOf('</style>'));
  const rule = (selector: string): string => {
    const at = style.indexOf(`${selector} {`);
    return at < 0 ? '' : style.slice(at, style.indexOf('}', at));
  };

  it('are 4 px on a --soft track with a --chart-1 fill and 2 px radius', () => {
    const track = rule('table.data-grid .share-bar');
    expect(track).toMatch(/height:\s*4px/);
    expect(track).toMatch(/background:\s*var\(--soft\)/);
    expect(track).toMatch(/border-radius:\s*2px/);
    const fill = rule('table.data-grid .share-bar > span');
    expect(fill).toMatch(/background:\s*var\(--chart-1\)/);
    expect(fill).toMatch(/border-radius:\s*2px/);
  });

  it('grow from 0 over 300 ms, and not under reduced motion', () => {
    expect(rule('table.data-grid .share-bar > span')).toMatch(/animation:\s*share-grow 300ms/);
    expect(style).toMatch(/@keyframes share-grow\s*\{\s*from\s*\{\s*width:\s*0;?\s*\}\s*\}/);
    expect(style).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*table\.data-grid \.share-bar > span\s*\{\s*animation:\s*none/);
  });
});

// Workspaces SPEC phase 3 (ADR 0044): model tokens in both themes, and no motion under reduced motion (N16).
describe('the New Workspace wizard', () => {
  const css = indexHtml.slice(indexHtml.indexOf('<style>'), indexHtml.indexOf('</style>'));

  it('defines the model accents for light and for dark', () => {
    const light = defined(block(css, /^\s*:root\s*\{/m));
    const dark = defined(block(block(css, /@media \(prefers-color-scheme: dark\)\s*\{/), /:root\s*\{/));
    for (const t of ['--model-wonder', '--model-wonder-soft', '--model-spark', '--model-spark-soft']) {
      expect(light, `light ${t}`).toContain(t);
      expect(dark, `dark ${t}`).toContain(t);
    }
  });

  it('turns off every Settings animation and transition, and the chips and cards, under reduced motion (N16)', () => {
    const blocks = [...css.matchAll(/@media \(prefers-reduced-motion: reduce\)\s*\{/g)].map(m => block(css.slice(m.index), /\{/));
    const wizard = blocks.find(b => b.includes('explorer-settings *'));
    expect(wizard, 'a reduced-motion block for Settings').toBeDefined();
    expect(wizard).toMatch(/animation:\s*none\s*!important/);
    expect(wizard).toMatch(/transition:\s*none\s*!important/);
    expect(wizard).toContain('.model-chip .dot');
    expect(wizard).toContain('.confirm-card');
  });
});

// SPEC §22 step 6 (P11): Control chips from the tokens.
describe('Control chips', () => {
  const style = indexHtml.slice(indexHtml.indexOf('<style>') + '<style>'.length, indexHtml.indexOf('</style>'));

  it('map the A2UI chip variables to tokens', () => {
    expect(style).toMatch(/--a2ui-choicepicker-chip-border:\s*1px solid var\(--line-strong\);/);
    expect(style).toMatch(/--a2ui-choicepicker-chip-background-selected:\s*var\(--primary\);/);
  });

  it('are mono 12 px, and a selected chip has --on-primary text', () => {
    const chip = style.match(/explorer-app \.a2ui-chip\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(chip).toMatch(/font:\s*12px\/[\d.]+ var\(--mono\)/);
    const selected = style.match(/explorer-app \.a2ui-chip\.a2ui-chip-selected\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(selected).toMatch(/color:\s*var\(--on-primary\)/);
  });
});

// SPEC §22 P14 (the user, 2026-11-05): Surface Rows stack on phones, so weights apply on wide screens only.
describe('Rows on phones', () => {
  const style = indexHtml.slice(indexHtml.indexOf('<style>') + '<style>'.length, indexHtml.indexOf('</style>'));

  it('stack Rows of Cards below 640 px, with each child full width whatever its weight', () => {
    const at = style.indexOf('@media (width < 640px)');
    expect(at).toBeGreaterThan(-1);
    const block = style.slice(at, style.indexOf('\n      }', at));
    const row = 'explorer-app a2ui-basic-row:has\\(> a2ui-card\\)';
    expect(block).toMatch(new RegExp(`${row}\\s*\\{[^}]*flex-direction:\\s*column`));
    const child = block.match(new RegExp(`${row} > \\*\\s*\\{([^}]*)\\}`))?.[1] ?? '';
    expect(child).toMatch(/flex:\s*0 0 auto !important/);
    expect(child).toMatch(/width:\s*100%/);
  });
});
