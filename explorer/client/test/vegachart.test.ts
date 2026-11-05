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
let options: Record<string, unknown>[] = [];

beforeAll(() => {
  // happy-dom lays nothing out: give the chart container a size so the element embeds.
  HTMLElement.prototype.getBoundingClientRect = function () {
    return {x: 0, y: 0, top: 0, left: 0, right: 600, bottom: 290, width: 600, height: 290, toJSON() {}} as DOMRect;
  };
  // No canvas in happy-dom: record the spec and the view's click listener instead of drawing.
  A2uiVegaChartElement.defaultEmbedFn = (async (_el: HTMLElement, spec: Record<string, unknown>, opts?: Record<string, unknown>) => {
    specs.push(spec);
    options.push(opts ?? {});
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
  options = [];
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
    // SPEC §22: a single-series line is layered over its area; the line layer keeps the cursor.
    const layers = specs.at(-1)!['layer'] as {mark: Record<string, any>}[];
    const mark = layers[1]!.mark;
    expect(mark.point).toEqual({cursor: 'pointer'}); // the points carry `_row` (SPEC §16 L9)
    expect(mark.cursor).toBeUndefined();
    expect(layers[0]!.mark.cursor).toBeUndefined();
    // A line or area item's datum is one of its points, so a click there would pick an arbitrary row.
    clicks[0]!({}, {datum: {name: 'A', v: 1, _row: 0}, mark: {marktype: 'line'}} as never);
    clicks[0]!({}, {datum: {name: 'A', v: 1, _row: 0}, mark: {marktype: 'area'}} as never);
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
  it('gives text marks the card colour, so treemap labels read in both themes', () => {
    const tokens: Record<string, string> = {'--panel': '#101418'};
    expect(themedChartConfig(name => tokens[name] ?? '')['text']).toEqual({fill: '#101418'});
    expect(themedChartConfig(() => '')['text']).toEqual({fill: '#ffffff'});
  });

  it('builds its config from the CSS tokens, falling back per key', () => {
    const tokens: Record<string, string> = {
      '--sans': '"Public Sans Variable", sans-serif', '--fg': '#111111', '--muted': '#222222',
      '--line': '#333333', '--chart-1': '#aa0000', '--chart-2': '#00bb00',
    };
    const config = themedChartConfig(name => tokens[name] ?? '') as Record<string, any>;
    expect(config.font).toBe('"Public Sans Variable", sans-serif');
    expect(config.axis.labelColor).toBe('#222222');
    expect(config.axis.titleColor).toBe('#222222'); // quieter axis titles (SPEC §22)
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

// SPEC §18: the treemap is full Vega, built by the agent (fixture from agent tests/test_macros.py).
import * as vega from 'vega';
import treemapSpec from './fixtures/treemap_spec.json';
import {FULL_VEGA_FALLBACK_WIDTH, transformSpec, withTileStroke} from '../src/vendor/vegachart/vegaChart.js';

describe('VegaChart labels and full Vega (SPEC §18)', () => {
  it('takes the confirmation label from _label', async () => {
    const actions = await chart({source: 'v1', rows: [0], drillable: false});
    clicks[0]!({}, {datum: {name: 'A', v: 1, _row: 0, _label: 'A · 2024'}});
    await new Promise(r => setTimeout(r, 0));
    expect(actions[0]!['userMessage']).toBe('A · 2024');
  });

  it('gives full Vega a numeric width when the container is unmeasured', () => {
    const out = transformSpec(treemapSpec as Record<string, unknown>, 290, 0);
    expect(out['width']).toBe(FULL_VEGA_FALLBACK_WIDTH);
    expect(transformSpec({mark: 'bar'}, 290, 0)['width']).toBe('container');
  });

  it('renders the agent treemap in real Vega: tiles keep _row and _label, only they get the pointer, no root mark', async () => {
    const spec = transformSpec(treemapSpec as Record<string, unknown>, 290, 600);
    const view = new vega.View(vega.parse(spec as vega.Spec), {renderer: 'none'});
    await view.runAsync();
    const tiles = view.data('tiles') as Record<string, unknown>[];
    expect(tiles.map(t => [t['_name'], t['_row'], t['_label']])).toEqual([
      ['Alpha', 0, 'Alpha'], ['Beta', 1, 'Beta'], ['Gamma', 2, 'Gamma'],
    ]);
    expect(tiles.every(t => (t['x1'] as number) > (t['x0'] as number) && (t['y1'] as number) > (t['y0'] as number))).toBe(true);
    // Rect items: one per tile (the root isn't drawn), each with the clickable cursor.
    type Item = {datum: Record<string, unknown>; cursor?: string};
    const group = (view.scenegraph() as unknown as {root: {items: {items: {marktype: string; items: Item[]}[]}[]}}).root.items[0]!;
    const rects = group.items.find(m => m.marktype === 'rect')!.items;
    expect(rects).toHaveLength(3);
    expect(rects.every(r => r.cursor === 'pointer' && typeof r.datum['_row'] === 'number')).toBe(true);
    view.finalize();
  });
});

// SPEC §18 slice 3: rows that open an Entity Board, and the Timeline's Vega-Lite `layer`.
import * as vegaLite from 'vega-lite';
import timelineSpec from './fixtures/timeline_spec.json';

describe('VegaChart Entity Board rows and Timeline (SPEC §18 slice 3)', () => {
  it('dispatches drill for a row that opens a Board, ask for the others, with the Drill-down hint', async () => {
    const actions = await chart({source: 'v1', rows: [0, 1], drillable: false, boards: [0]});
    clicks[0]!({}, {datum: {name: 'A', v: 1, _row: 0}});
    clicks[0]!({}, {datum: {name: 'B', v: 1, _row: 1}});
    await new Promise(r => setTimeout(r, 0));
    expect(actions.map(a => a['name'])).toEqual(['drill', 'ask']);
    expect(document.querySelector('.visual-hint')?.textContent).toBe(DRILL_HINT_CHART);
  });

  it('leaves a layer spec\'s cursor to the agent and dispatches a bar click', async () => {
    const processor = createProcessor();
    const actions: Record<string, unknown>[] = [];
    processor.model.onAction.subscribe(a => void actions.push(a as unknown as Record<string, unknown>));
    processor.processMessages([
      {version: 'v0.9', createSurface: {surfaceId: 's', catalogId: EXPLORER_CATALOG_ID}},
      {version: 'v0.9', updateComponents: {surfaceId: 's', components: [{
        id: 'root', component: 'VegaChart', spec: timelineSpec, action: {source: 'v1', rows: [0, 1, 2], drillable: false, boards: [1]},
      }]}},
    ] as unknown as A2uiMessage[]);
    const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
    el.surface = processor.model.surfacesMap.get('s');
    document.body.appendChild(el);
    await settle(el);
    const embedded = specs.at(-1)!;
    expect(embedded['mark']).toBeUndefined(); // no top-level mark was added to the layer spec
    const layers = embedded['layer'] as {mark: Record<string, unknown>}[];
    expect(layers.filter(l => l.mark['cursor'] === 'pointer').map(l => l.mark['type'])).toEqual(['bar']);
    clicks[0]!({}, {datum: {_lane: 'C1', _row: 1, _label: 'C1'}});
    clicks[0]!({}, {datum: {_today: '2026-01-31'}}); // the rule: no row
    await new Promise(r => setTimeout(r, 0));
    expect(actions).toEqual([expect.objectContaining({name: 'drill', context: {source: 'v1', row: 1}, userMessage: 'C1'})]);
  });

  it('compiles the agent timeline in real Vega-Lite: only bars carry _row, _label and the pointer', async () => {
    const compiled = vegaLite.compile(timelineSpec as never).spec;
    const view = new vega.View(vega.parse(compiled), {renderer: 'none'});
    await view.runAsync();
    type Item = {datum: Record<string, unknown>; cursor?: string};
    type Mark = {marktype: string; items: Item[]};
    const marks: Mark[] = [];
    const walk = (node: {items?: unknown[]; marktype?: string}) => {
      if (node.marktype) marks.push(node as Mark);
      for (const child of (node.items ?? []) as {items?: unknown[]; marktype?: string}[]) walk(child);
    };
    walk((view.scenegraph() as unknown as {root: {items?: unknown[]}}).root);
    const bars = marks.filter(m => m.marktype === 'rect' && m.items.some(i => '_row' in i.datum)).flatMap(m => m.items);
    expect(bars.map(b => [b.datum['_row'], b.datum['_label'], b.cursor])).toEqual([
      [0, 'C2', 'pointer'], [1, 'C1', 'pointer'], [2, 'C3', 'pointer'],
    ]);
    const others = marks.filter(m => !m.items.some(i => '_row' in (i.datum ?? {}))).flatMap(m => m.items);
    expect(others.every(i => i.cursor !== 'pointer')).toBe(true);
    view.finalize();
  });
});

// SPEC §22 step 1: chart polish from the tokens and spec transforms in the client.
import {AREA_OPACITY, HOVER_FADED_OPACITY, HOVER_PARAM, TOOLTIP_THEME, withArea, withHover} from '../src/vendor/vegachart/vegaChart.js';

const COLORS = {line: '#aa0000', card: '#ffffff'};
const lineSpec = (values: Record<string, unknown>[], extra: Record<string, unknown> = {}) => ({
  mark: {type: 'line', point: {cursor: 'pointer'}},
  data: {values},
  encoding: {
    x: {field: 'fy', type: 'ordinal'}, y: {field: 'v', type: 'quantitative'},
    tooltip: [{field: 'fy', type: 'ordinal'}, {field: 'v', type: 'quantitative'}], ...extra,
  },
});
const barSpec = (extra: Record<string, unknown> = {}) => ({
  mark: {type: 'bar'}, data: {values: [{name: 'A', v: 1, _row: 0}, {name: 'B', v: 2, _row: 1}]},
  encoding: {x: {field: 'name', type: 'nominal'}, y: {field: 'v', type: 'quantitative'}}, ...extra,
});

type SceneItem = {datum?: Record<string, unknown>; cursor?: string; tooltip?: unknown; opacity?: number; fill?: unknown};
type SceneMark = {marktype: string; items: SceneItem[]};
function sceneMarks(view: vega.View): SceneMark[] {
  const marks: SceneMark[] = [];
  const walk = (node: {items?: unknown[]; marktype?: string}) => {
    if (node.marktype) marks.push(node as SceneMark);
    for (const child of (node.items ?? []) as {items?: unknown[]; marktype?: string}[]) walk(child);
  };
  walk((view.scenegraph() as unknown as {root: {items?: unknown[]}}).root);
  return marks;
}

describe('VegaChart polish (SPEC §22 step 1)', () => {
  it('P1: rounds bar ends, drops domain and tick lines, dashed gridlines on value axes only, mono labels, card-coloured rect seams', () => {
    const tokens: Record<string, string> = {'--mono': '"JetBrains Mono Variable", monospace', '--muted': '#222222', '--line': '#333333', '--panel': '#101418'};
    const c = themedChartConfig(name => tokens[name] ?? '') as Record<string, any>;
    expect(c.bar).toEqual({cornerRadiusEnd: 3});
    expect(c.axis).toMatchObject({domain: false, ticks: false, gridDash: [2, 3], gridColor: '#333333', labelColor: '#222222',
      labelFont: '"JetBrains Mono Variable", monospace', labelFontSize: 11, titleColor: '#222222', titleFontWeight: 'normal'});
    expect(c.axisQuantitative).toEqual({grid: true});
    for (const k of ['axisTemporal', 'axisBand', 'axisPoint']) expect(c[k], k).toEqual({grid: false});
    expect(c.axisX).toBeUndefined(); // screen-side keys would put gridlines on a horizontal bar's categories
    expect(c.rect).toEqual({stroke: '#101418', strokeWidth: 2, cornerRadius: 3});
    expect(c.legend.labelFont).toBe('"JetBrains Mono Variable", monospace');
    const fallback = themedChartConfig(() => '') as Record<string, any>;
    expect(fallback.axis.labelFont).toBeUndefined(); // no token: the font stays the chart's
    expect(fallback.rect.stroke).toBe('#ffffff');
  });

  it('P2: layers a gradient area without a tooltip under a single-series line', () => {
    const out = withArea(lineSpec([{fy: '1', v: 1}, {fy: '2', v: 3}]), COLORS) as Record<string, any>;
    expect(out.mark).toBeUndefined();
    expect(out.data.values).toHaveLength(2);
    expect(out.encoding.y.field).toBe('v'); // shared by both layers
    const [area, line] = out.layer;
    expect(area.mark.type).toBe('area');
    expect(area.mark.opacity).toBe(AREA_OPACITY);
    expect(area.mark.color.gradient).toBe('linear');
    expect(area.mark.color.stops).toEqual([{offset: 0, color: '#ffffff'}, {offset: 1, color: '#aa0000'}]);
    expect(area.encoding).toEqual({tooltip: null});
    expect(line).toEqual({mark: {type: 'line', point: {cursor: 'pointer'}}});
  });

  it('P2: leaves multi-series lines, negative values, other marks and layered specs alone', () => {
    const multi = lineSpec([{fy: '1', v: 1, s: 'a'}], {color: {field: 's', type: 'nominal'}});
    expect(withArea(multi, COLORS)).toBe(multi);
    const negative = lineSpec([{fy: '1', v: 1}, {fy: '2', v: -1}]);
    expect(withArea(negative, COLORS)).toBe(negative);
    const bar = barSpec();
    expect(withArea(bar, COLORS)).toBe(bar);
    const layered = {layer: [{mark: 'line'}], encoding: {y: {field: 'v', type: 'quantitative'}}, data: {values: []}};
    expect(withArea(layered, COLORS)).toBe(layered);
    const escaped = lineSpec([{'a.b': -2}], {y: {field: 'a\\.b', type: 'quantitative'}});
    expect(withArea(escaped, COLORS)).toBe(escaped); // the field name is unescaped before the check
    for (const data of [{url: 'x.csv'}, {name: 'table'}]) {
      const remote = {...lineSpec([]), data};
      expect(withArea(remote, COLORS)).toBe(remote); // values it can't see might be negative
    }
    const ordinalY = lineSpec([{fy: '1', v: 1}], {y: {field: 'v', type: 'ordinal'}});
    expect(withArea(ordinalY, COLORS)).toBe(ordinalY);
    const noType = lineSpec([{fy: '1', v: 1}], {y: {field: 'v'}});
    expect(withArea(noType, COLORS)).toBe(noType);
  });

  it('P3: gives bar unit specs the hover fade, and nothing else', () => {
    const out = withHover(barSpec()) as Record<string, any>;
    expect(out.params).toEqual([{name: HOVER_PARAM, select: {type: 'point', on: 'pointerover', clear: 'pointerout'}}]);
    expect(out.encoding.opacity).toEqual({condition: {param: HOVER_PARAM, empty: true, value: 1}, value: HOVER_FADED_OPACITY});
    const withParams = barSpec({params: [{name: 'p', value: 1}]});
    expect(withHover(withParams)).toBe(withParams);
    const opacity = {...barSpec(), encoding: {...barSpec().encoding, opacity: {value: 0.5}}};
    expect(withHover(opacity)).toBe(opacity);
    const line = lineSpec([{fy: '1', v: 1}]);
    expect(withHover(line)).toBe(line);
    expect(withHover(treemapSpec as Record<string, unknown>)).toBe(treemapSpec);
  });

  it('P4: the layered line compiles in real Vega-Lite: points keep _row and the pointer, the area has no tooltip', async () => {
    const spec = transformSpec(withArea(lineSpec([{fy: '1', v: 1, _row: 0}, {fy: '2', v: 3, _row: 1}]), COLORS), 290, 600,
      undefined, themedChartConfig(() => ''));
    const view = new vega.View(vega.parse(vegaLite.compile(spec as never).spec), {renderer: 'none'});
    await view.runAsync();
    const marks = sceneMarks(view);
    const area = marks.find(m => m.marktype === 'area')!;
    expect(area.items.every(i => i.tooltip === undefined && i.cursor !== 'pointer')).toBe(true);
    const points = marks.filter(m => m.marktype === 'symbol').flatMap(m => m.items);
    expect(points.map(p => [p.datum!['_row'], p.cursor])).toEqual([[0, 'pointer'], [1, 'pointer']]);
    expect(points.every(p => p.tooltip !== undefined)).toBe(true);
    view.finalize();
  });

  it('P3: in real Vega, hovering a bar fades the others and leaving restores them', async () => {
    const spec = transformSpec(withHover({...barSpec(), data: {values: [{name: 'A', v: 1}, {name: 'B', v: 2}, {name: 'C', v: 3}]}}),
      290, 600, undefined, themedChartConfig(() => ''));
    const host = document.createElement('div');
    document.body.appendChild(host);
    const view = new vega.View(vega.parse(vegaLite.compile(spec as never).spec), {renderer: 'svg', container: host, hover: false});
    await view.runAsync();
    const bars = () => sceneMarks(view).filter(m => m.marktype === 'rect').flatMap(m => m.items);
    expect(bars().map(b => b.opacity)).toEqual([1, 1, 1]); // nothing hovered: all opaque
    const paths = [...host.querySelectorAll('path[aria-roledescription="bar"]')];
    expect(paths).toHaveLength(3);
    paths[1]!.dispatchEvent(new PointerEvent('pointerover', {bubbles: true}));
    await view.runAsync();
    expect(bars().map(b => b.opacity)).toEqual([HOVER_FADED_OPACITY, 1, HOVER_FADED_OPACITY]);
    paths[1]!.dispatchEvent(new PointerEvent('pointerout', {bubbles: true}));
    await view.runAsync();
    expect(bars().map(b => b.opacity)).toEqual([1, 1, 1]);
    view.finalize();
  });

  it('P1: full Vega treemap tiles take the radius from the config (the seam colour is withTileStroke, step 5)', async () => {
    const spec = transformSpec(treemapSpec as Record<string, unknown>, 290, 600, undefined,
      themedChartConfig(name => (name === '--panel' ? '#101418' : '')));
    const view = new vega.View(vega.parse(spec as vega.Spec), {renderer: 'none'});
    await view.runAsync();
    const tiles = sceneMarks(view).filter(m => m.marktype === 'rect').flatMap(m => m.items) as (SceneItem & {cornerRadius?: number})[];
    expect(tiles.length).toBeGreaterThan(0);
    view.finalize();
    expect(tiles.every(t => t.cornerRadius === 3)).toBe(true);
  });

  it('P5: embeds with the explorer tooltip theme', async () => {
    await chart();
    expect(options.at(-1)).toMatchObject({tooltip: {theme: TOOLTIP_THEME}});
    expect(TOOLTIP_THEME).toBe('explorer');
  });
});

// Refuter finding (2026-11-05): axisX/axisY grids put gridlines on the category axis of horizontal
// bars and Timelines. Gridlines belong to the quantitative axis only, whichever side it is on.
describe('VegaChart gridlines follow the value axis (SPEC §22 step 1)', () => {
  type VgAxis = {orient: string; grid?: boolean; scale: string};
  const gridAxes = (spec: Record<string, unknown>) => {
    const compiled = vegaLite.compile(transformSpec(spec, 290, 600, undefined, themedChartConfig(() => '')) as never).spec;
    const axes: VgAxis[] = [];
    const walk = (node: Record<string, unknown>) => {
      for (const a of (node['axes'] ?? []) as VgAxis[]) axes.push(a);
      for (const m of (node['marks'] ?? []) as Record<string, unknown>[]) walk(m);
    };
    walk(compiled as unknown as Record<string, unknown>);
    return axes.filter(a => a.grid).map(a => a.orient);
  };

  it('draws gridlines across a vertical bar chart (from the left axis)', () => {
    expect(gridAxes(barSpec())).toEqual(['left']);
  });

  it('draws gridlines along the value axis of a horizontal bar chart, not through its categories', () => {
    const horizontal = {...barSpec(), encoding: {y: {field: 'name', type: 'nominal'}, x: {field: 'v', type: 'quantitative'}}};
    expect(gridAxes(horizontal)).toEqual(['bottom']);
  });

  it('draws no gridlines through the Timeline lanes', () => {
    expect(gridAxes(timelineSpec as Record<string, unknown>)).not.toContain('left');
  });
});

// Checked 2026-11-05: Vega-Lite stacks every quantitative bar and rounds a clip group around each
// stack, not the rects, so a stacked bar is rounded at its outer end only, never between segments.
describe('VegaChart bar ends (SPEC §22 step 1)', () => {
  type Group = {clip?: boolean; cornerRadiusTopLeft?: number; cornerRadiusTopRight?: number; cornerRadiusBottomLeft?: number};
  const clipGroups = async (spec: Record<string, unknown>) => {
    const compiled = vegaLite.compile(transformSpec(spec, 290, 600, undefined, themedChartConfig(() => '')) as never).spec;
    const view = new vega.View(vega.parse(compiled), {renderer: 'none'});
    await view.runAsync();
    const out = sceneMarks(view).filter(m => m.marktype === 'group').flatMap(m => m.items as Group[]).filter(g => g.clip);
    view.finalize();
    return out;
  };

  it('rounds the top corners of each bar, not the base', async () => {
    const groups = await clipGroups(barSpec());
    expect(groups).toHaveLength(2);
    expect(groups.every(g => g.cornerRadiusTopLeft === 3 && g.cornerRadiusTopRight === 3 && !g.cornerRadiusBottomLeft)).toBe(true);
  });

  it('rounds a stacked bar once, at the end of the stack', async () => {
    const stacked = {
      mark: {type: 'bar'},
      data: {values: [{name: 'A', s: 'x', v: 1}, {name: 'A', s: 'y', v: 2}, {name: 'B', s: 'x', v: 2}]},
      encoding: {x: {field: 'name', type: 'nominal'}, y: {field: 'v', type: 'quantitative'}, color: {field: 's', type: 'nominal'}},
    };
    const groups = await clipGroups(stacked);
    expect(groups).toHaveLength(2); // one per category, not one per segment
    expect(groups.every(g => g.cornerRadiusTopLeft === 3)).toBe(true);
  });
});

// SPEC §22 step 5 (P12): with the agent's stroke gone, the theme's rect config separates tiles.
describe('treemap look (SPEC §22 step 5)', () => {
  it('draws tiles with the panel-coloured stroke, the config width and full opacity', async () => {
    const config = themedChartConfig(name => ({'--panel': '#fafbfc'})[name] ?? '');
    const spec = transformSpec(withTileStroke(treemapSpec as Record<string, unknown>, '#fafbfc'), 290, 600, null, config as never);
    const view = new vega.View(vega.parse(spec as vega.Spec), {renderer: 'none'});
    await view.runAsync();
    type Item = {stroke?: string; strokeWidth?: number; fillOpacity?: number};
    const group = (view.scenegraph() as unknown as {root: {items: {items: {marktype: string; items: Item[]}[]}[]}}).root.items[0]!;
    const rects = group.items.find(m => m.marktype === 'rect')!.items;
    expect(rects.every(r => r.stroke === '#fafbfc' && r.strokeWidth === 2 && r.fillOpacity === 1)).toBe(true);
    view.finalize();
  });

  it('withTileStroke strokes only full-Vega rects without a stroke, and leaves the input alone', () => {
    const spec = {
      $schema: 'https://vega.github.io/schema/vega/v5.json',
      marks: [
        {type: 'rect', encode: {update: {fill: {value: 'x'}}}},
        {type: 'rect', encode: {update: {stroke: {value: 'red'}}}},
        {type: 'text', encode: {update: {}}},
        {type: 'rect'},
      ],
    };
    const before = structuredClone(spec);
    const out = withTileStroke(spec, '#123456') as typeof spec & {marks: {encode?: {update?: Record<string, unknown>}}[]};
    expect(out.marks[0]!.encode!.update!['stroke']).toEqual({value: '#123456'});
    expect(out.marks[1]!.encode!.update!['stroke']).toEqual({value: 'red'});
    expect(out.marks[2]!.encode!.update!['stroke']).toBeUndefined();
    expect(out.marks[3]!.encode!.update!['stroke']).toEqual({value: '#123456'});
    expect(spec).toEqual(before);
    const lite = {mark: 'rect', encoding: {}};
    expect(withTileStroke(lite, '#123456')).toBe(lite);
  });
});
