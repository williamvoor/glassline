import {afterEach, beforeAll, describe, expect, it} from 'vitest';
import '@a2ui/lit/v0_9';
import type {A2uiMessage} from '@a2ui/web_core/v0_9';
import {createProcessor} from '../src/catalog.js';
import {A2uiVegaChartElement} from '../src/vendor/vegachart/vegaChart.js';
import transport from './fixtures/slice_transport.json';

// Recorded by the agent's tests/test_ui.py::test_client_fixture_is_current.
const embedded: unknown[] = [];

beforeAll(() => {
  // No canvas in happy-dom: record the spec instead of drawing it.
  A2uiVegaChartElement.defaultEmbedFn = (async (_el: HTMLElement, spec: unknown) => {
    embedded.push(spec);
    return {view: {finalize() {}}, finalize() {}};
  }) as unknown as typeof A2uiVegaChartElement.defaultEmbedFn;
});

afterEach(() => {
  document.body.innerHTML = '';
});

async function settle(root: Element) {
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 10));
    const pending = [...root.querySelectorAll('*')]
      .map(el => (el as unknown as {updateComplete?: Promise<unknown>}).updateComplete)
      .filter(Boolean);
    await Promise.all(pending);
  }
}

describe('fixture transport', () => {
  it('renders text, a chart, a table and the SQL', async () => {
    const processor = createProcessor();
    processor.processMessages(transport as unknown as A2uiMessage[]);
    const surface = processor.model.surfacesMap.get('turn-1');
    expect(surface).toBeDefined();

    const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
    el.surface = surface;
    document.body.appendChild(el);
    await settle(el);

    const text = el.textContent ?? '';
    expect(text).toContain('Top supplier value');
    expect(text).toContain('Value by supplier');
    expect(el.querySelector('a2ui-vega-chart')).not.toBeNull();

    const table = el.querySelector('table')!;
    expect(table).not.toBeNull();
    expect([...table.querySelectorAll('th')].map(th => th.textContent)).toEqual(['Supplier', 'Contracts', 'Value']);
    expect(table.querySelectorAll('tbody tr')).toHaveLength(3);
    expect(table.querySelector('tbody td')!.textContent).toBe('Supplier A');

    const details = el.querySelectorAll('details');
    expect(details.length).toBeGreaterThanOrEqual(3); // one per visual
    expect(details[0]!.querySelector('pre')!.textContent).toContain('FROM fixture.contracts');
    expect(details[0]!.querySelector('summary')!.textContent).toBe('SQL — top suppliers (fixture)');
  });
});
