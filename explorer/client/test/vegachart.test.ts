// Clickable VegaChart marks (explorer SPEC §15: A14).
import {afterEach, beforeAll, describe, expect, it} from 'vitest';
import '@a2ui/lit/v0_9';
import type {A2uiMessage} from '@a2ui/web_core/v0_9';
import {EXPLORER_CATALOG_ID, createProcessor} from '../src/catalog.js';
import {A2uiVegaChartElement, themedChartConfig} from '../src/vendor/vegachart/vegaChart.js';
import {ASK_HINT_CHART, DRILL_HINT_CHART} from '../src/actions.js';

type Click = (event: unknown, item: {datum?: Record<string, unknown>} | null) => void;
let clicks: Click[] = [];
let specs: Record<string, unknown>[] = [];

beforeAll(() => {
  // happy-dom lays nothing out: give the chart container a size so the element embeds.
  HTMLElement.prototype.getBoundingClientRect = function () {
    return {x: 0, y: 0, top: 0, left: 0, right: 600, bottom: 290, width: 600, height: 290, toJSON() {}} as DOMRect;
  };
  // No canvas in happy-dom: record the spec and the view's click listener instead of drawing.
  A2uiVegaChartElement.defaultEmbedFn = (async (_el: HTMLElement, spec: Record<string, unknown>) => {
    specs.push(spec);
    return {
      view: {finalize() {}, addEventListener: (type: string, handler: Click) => type === 'click' && clicks.push(handler)},
      finalize() {},
    };
  }) as unknown as typeof A2uiVegaChartElement.defaultEmbedFn;
});

afterEach(() => {
  document.body.innerHTML = '';
  clicks = [];
  specs = [];
});

async function settle(root: Element) {
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 10));
    await Promise.all(
      [...root.querySelectorAll('*')].map(el => (el as unknown as {updateComplete?: Promise<unknown>}).updateComplete).filter(Boolean),
    );
  }
}

async function chart(action?: Record<string, unknown>, mark: Record<string, unknown> = {type: 'bar'}) {
  const processor = createProcessor();
  const actions: Record<string, unknown>[] = [];
  processor.model.onAction.subscribe(a => void actions.push(a as unknown as Record<string, unknown>));
  processor.processMessages([
    {version: 'v0.9', createSurface: {surfaceId: 's', catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: 's', components: [{
      id: 'root', component: 'VegaChart',
      spec: {mark, data: {values: [{name: 'A', v: 1, _row: 0}, {name: 'Other', v: 2}]},
             encoding: {x: {field: 'name', type: 'nominal'}, y: {field: 'v', type: 'quantitative'}}},
      ...(action ? {action} : {}),
    }]}},
  ] as unknown as A2uiMessage[]);
  const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
  el.surface = processor.model.surfacesMap.get('s');
  document.body.appendChild(el);
  await settle(el);
  return actions;
}

describe('VegaChart actions', () => {
  it('gives a line chart\'s points the pointer, and ignores clicks on the line itself', async () => {
    const actions = await chart({source: 'v1', rows: [0], drillable: true}, {type: 'line', point: true});
    const mark = specs.at(-1)!['mark'] as Record<string, any>;
    expect(mark.point).toEqual({cursor: 'pointer'}); // the points carry `_row` (SPEC §16 L9)
    expect(mark.cursor).toBeUndefined();
    // A line item's datum is one of its points, so a click on the line would pick an arbitrary row.
    clicks[0]!({}, {datum: {name: 'A', v: 1, _row: 0}, mark: {marktype: 'line'}} as never);
    await new Promise(r => setTimeout(r, 0));
    expect(actions).toEqual([]);
    clicks[0]!({}, {datum: {name: 'A', v: 1, _row: 0}, mark: {marktype: 'symbol'}} as never);
    await new Promise(r => setTimeout(r, 0));
    expect(actions.map(a => a['name'])).toEqual(['drill']);
  });

  it('dispatches drill for a drillable mark, with its label', async () => {
    const actions = await chart({source: 'v1', rows: [0], drillable: true});
    expect(clicks).toHaveLength(1);
    clicks[0]!({}, {datum: {name: 'A', v: 1, _row: 0}});
    await new Promise(r => setTimeout(r, 0));
    expect(actions).toEqual([expect.objectContaining({
      name: 'drill', surfaceId: 's', sourceComponentId: 'root', context: {source: 'v1', row: 0}, userMessage: 'A',
    })]);
    expect((specs.at(-1)!['mark'] as Record<string, unknown>)['cursor']).toBe('pointer');
    expect(document.querySelector('.visual-hint')?.textContent).toBe(DRILL_HINT_CHART);
  });

  it('dispatches ask for an Ask-only chart', async () => {
    const actions = await chart({source: 'v1', rows: [0], drillable: false});
    clicks[0]!({}, {datum: {name: 'A', v: 1, _row: 0}});
    await new Promise(r => setTimeout(r, 0));
    expect(actions.map(a => a['name'])).toEqual(['ask']);
    // SPEC §16: every clickable mark gets a pointer; the hint line tells the two kinds apart.
    expect((specs.at(-1)!['mark'] as Record<string, unknown>)['cursor']).toBe('pointer');
    expect(document.querySelector('.visual-hint')?.textContent).toBe(ASK_HINT_CHART);
  });

  it('ignores marks without a clickable _row, and charts without an action', async () => {
    const actions = await chart({source: 'v1', rows: [0], drillable: true});
    clicks[0]!({}, {datum: {name: 'Other', v: 2}});
    clicks[0]!({}, {datum: {name: 'X', v: 2, _row: 5}});
    clicks[0]!({}, null);
    await new Promise(r => setTimeout(r, 0));
    expect(actions).toEqual([]);
    document.body.innerHTML = '';
    await chart();
    expect(clicks).toHaveLength(1); // no listener for the chart without an action
    expect(document.querySelector('.visual-hint')).toBeNull();
    expect((specs.at(-1)!['mark'] as Record<string, unknown>)['cursor']).toBeUndefined();
  });
});

// SPEC §16 (L8): the chart's look comes from the page's CSS tokens.
describe('VegaChart theme', () => {
  it('builds its config from the CSS tokens, falling back per key', () => {
    const tokens: Record<string, string> = {
      '--sans': '"Public Sans Variable", sans-serif', '--fg': '#111111', '--muted': '#222222',
      '--line': '#333333', '--chart-1': '#aa0000', '--chart-2': '#00bb00',
    };
    const config = themedChartConfig(name => tokens[name] ?? '') as Record<string, any>;
    expect(config.font).toBe('"Public Sans Variable", sans-serif');
    expect(config.axis.labelColor).toBe('#222222');
    expect(config.axis.titleColor).toBe('#111111');
    expect(config.axis.domainColor).toBe('#333333');
    expect(config.legend.labelColor).toBe('#222222');
    expect(config.title.color).toBe('#111111');
    expect(config.range.category).toEqual(['#aa0000', '#00bb00']); // only the tokens that are set
    expect(config.mark.color).toBe('#aa0000'); // a single series uses the first chart colour
    const fallback = themedChartConfig(() => '') as Record<string, any>;
    expect(fallback.font).toBeTruthy();
    expect(fallback.range.category.length).toBeGreaterThan(0);
  });

  it('renders with the tokens on the page, and again when the colour scheme changes', async () => {
    const listeners: (() => void)[] = [];
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: false, media: query,
      addEventListener: (_t: string, l: () => void) => listeners.push(l),
      removeEventListener: (_t: string, l: () => void) => listeners.splice(listeners.indexOf(l), 1),
    })) as unknown as typeof window.matchMedia;
    document.documentElement.style.setProperty('--chart-1', '#123456');
    try {
      await chart();
      const config = specs.at(-1)!['config'] as Record<string, any>;
      expect(config.range.category[0]).toBe('#123456');
      expect(listeners).toHaveLength(1);
      document.documentElement.style.setProperty('--chart-1', '#654321');
      const before = specs.length;
      listeners[0]!();
      await new Promise(r => setTimeout(r, 20));
      expect(specs.length).toBe(before + 1);
      expect((specs.at(-1)!['config'] as Record<string, any>).range.category[0]).toBe('#654321');
      document.body.innerHTML = '';
      expect(listeners).toHaveLength(0); // removed on disconnect
    } finally {
      window.matchMedia = original;
      document.documentElement.style.removeProperty('--chart-1');
    }
  });
});
