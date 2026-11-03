import {describe, expect, it} from 'vitest';
import '@a2ui/lit/v0_9';
import type {A2uiMessage} from '@a2ui/web_core/v0_9';
import {EXPLORER_CATALOG_ID, createProcessor} from '../src/catalog.js';
import {ASK_HINT_ROW, DRILL_HINT_ROW} from '../src/actions.js';

async function grid(action?: Record<string, unknown>) {
  const processor = createProcessor();
  processor.processMessages([
    {version: 'v0.9', createSurface: {surfaceId: 's', catalogId: EXPLORER_CATALOG_ID}},
    {version: 'v0.9', updateComponents: {surfaceId: 's', components: [{
      id: 'root', component: 'DataGrid', columns: [{key: 'a', label: 'A'}], rows: [['x'], ['y']],
      ...(action ? {action} : {}),
    }]}},
  ] as unknown as A2uiMessage[]);
  const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
  el.surface = processor.model.surfacesMap.get('s');
  document.body.replaceChildren(el);
  for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 10));
  return el;
}

describe('DataGrid', () => {
  it('renders cell and caption HTML as text', async () => {
    const processor = createProcessor();
    processor.processMessages([
      {version: 'v0.9', createSurface: {surfaceId: 's', catalogId: EXPLORER_CATALOG_ID}},
      {version: 'v0.9', updateComponents: {surfaceId: 's', components: [{
        id: 'root', component: 'DataGrid',
        columns: [{key: 'a', label: '<i>A</i>'}],
        rows: [['<b>x</b><img src=x onerror=alert(1)>']],
        caption: '<script>alert(1)</script>',
      }]}},
    ] as unknown as A2uiMessage[]);
    const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
    el.surface = processor.model.surfacesMap.get('s');
    document.body.appendChild(el);
    for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 10));

    const td = el.querySelector('td')!;
    expect(td.textContent).toBe('<b>x</b><img src=x onerror=alert(1)>');
    expect(el.querySelector('b, img, i, script')).toBeNull();
    expect(el.querySelector('th')!.textContent).toBe('<i>A</i>');
    expect(el.querySelector('caption')!.textContent).toBe('<script>alert(1)</script>');
  });

  it('right-aligns end columns', async () => {
    const processor = createProcessor();
    processor.processMessages([
      {version: 'v0.9', createSurface: {surfaceId: 's', catalogId: EXPLORER_CATALOG_ID}},
      {version: 'v0.9', updateComponents: {surfaceId: 's', components: [{
        id: 'root', component: 'DataGrid',
        columns: [{key: 'a', label: 'A'}, {key: 'n', label: 'N', align: 'end'}],
        rows: [['x', '1']],
      }]}},
    ] as unknown as A2uiMessage[]);
    const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
    el.surface = processor.model.surfacesMap.get('s');
    document.body.appendChild(el);
    for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 10));

    const [left, right] = [...el.querySelectorAll('td')] as HTMLElement[];
    expect(left!.style.textAlign).toBe('left');
    expect(right!.style.textAlign).toBe('right');
    expect(el.querySelector('caption')).toBeNull();
  });

  // SPEC §16 (L9): a hint line says what a click does; Ask-only rows carry an Ask tag.
  it('shows the Drill-down hint and no Ask tag on drillable rows', async () => {
    const el = await grid({source: 'v1', rows: [0], drillable: true});
    expect(el.querySelector('.visual-hint')!.textContent).toBe(DRILL_HINT_ROW);
    expect(el.querySelectorAll('.ask-tag')).toHaveLength(0);
  });

  it('shows the Ask hint and an Ask tag on each Ask-only row', async () => {
    const el = await grid({source: 'v1', rows: [1], drillable: false});
    expect(el.querySelector('.visual-hint')!.textContent).toBe(ASK_HINT_ROW);
    const tags = [...el.querySelectorAll('.ask-tag')];
    expect(tags).toHaveLength(1);
    expect(tags[0]!.closest('tr')!.textContent).toContain('y'); // only on the clickable row
    expect(tags[0]!.getAttribute('aria-hidden')).toBe('true'); // the hint says it in words
  });

  it('shows no hint without an action', async () => {
    const el = await grid();
    expect(el.querySelector('.visual-hint')).toBeNull();
  });

  it('draws share bars under the formatted text, none for a null cell (SPEC §18, B16)', async () => {
    const processor = createProcessor();
    processor.processMessages([
      {version: 'v0.9', createSurface: {surfaceId: 's', catalogId: EXPLORER_CATALOG_ID}},
      {version: 'v0.9', updateComponents: {surfaceId: 's', components: [{
        id: 'root', component: 'DataGrid',
        columns: [{key: 'n', label: 'Name'}, {key: 'v', label: 'Value', align: 'end', bars: [1, 0.25, null]}],
        rows: [['a', '$200'], ['b', '$50'], ['c', '—']],
      }]}},
    ] as unknown as A2uiMessage[]);
    const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
    el.surface = processor.model.surfacesMap.get('s');
    document.body.replaceChildren(el);
    for (let i = 0; i < 10; i++) await new Promise(r => setTimeout(r, 10));

    const cells = [...el.querySelectorAll('tbody tr')].map(tr => tr.querySelectorAll('td')[1]!);
    expect(cells.map(td => td.textContent)).toEqual(['$200', '$50', '—']);
    const widths = cells.map(td => (td.querySelector('.share-bar > span') as HTMLElement | null)?.style.width ?? null);
    expect(widths).toEqual(['100.0%', '25.0%', null]);
    expect(cells[0]!.querySelector('.share-bar')!.getAttribute('aria-hidden')).toBe('true');
    expect(el.querySelector('tbody tr td:first-child .share-bar')).toBeNull(); // the Name column has none
  });
});
