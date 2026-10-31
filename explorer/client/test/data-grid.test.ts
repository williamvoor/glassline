import {describe, expect, it} from 'vitest';
import '@a2ui/lit/v0_9';
import type {A2uiMessage} from '@a2ui/web_core/v0_9';
import {EXPLORER_CATALOG_ID, createProcessor} from '../src/catalog.js';

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
});
