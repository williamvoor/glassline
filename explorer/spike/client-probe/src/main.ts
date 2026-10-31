// Renders the same transport messages three ways to see which catalog setup
// the Lit 0.14.0 renderer actually draws.
import {basicCatalog} from '@a2ui/lit/v0_9';
import '@a2ui/lit/v0_9';
import {Catalog, MessageProcessor, type A2uiMessage, type ComponentApi} from '@a2ui/web_core/v0_9';
import {A2uiVegaChart, VEGACHART_CATALOG_ID, vegachartCatalog} from './vegachart/index.js';
import transport from './transport.json';

const MERGED_ID = 'urn:explorer:catalog:basic-vegachart:v0_9';
const merged = new Catalog<ComponentApi>(
  MERGED_ID, '0.9',
  [...basicCatalog.components.values(), A2uiVegaChart] as ComponentApi[],
  [...basicCatalog.functions.values()],
);

const cases: Array<[string, Catalog<ComponentApi>[], string]> = [
  ['A: basic ID, [basic, vega]', [basicCatalog as Catalog<ComponentApi>, vegachartCatalog as Catalog<ComponentApi>], basicCatalog.id],
  ['B: vega ID, [basic, vega]', [basicCatalog as Catalog<ComponentApi>, vegachartCatalog as Catalog<ComponentApi>], VEGACHART_CATALOG_ID],
  ['C: merged ID, [merged]', [merged], MERGED_ID],
];

const out = document.getElementById('out')!;
for (const [label, catalogs, catalogId] of cases) {
  const section = document.createElement('section');
  section.dataset.case = label;
  section.innerHTML = `<h3>${label}</h3>`;
  out.appendChild(section);
  const proc = new MessageProcessor<ComponentApi>(catalogs);
  proc.onSurfaceCreated(surface => {
    const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
    el.surface = surface;
    section.appendChild(el);
  });
  const msgs = structuredClone(transport) as Array<Record<string, any>>;
  msgs[0].createSurface.catalogId = catalogId;
  msgs[0].createSurface.surfaceId = 's' + label[0];
  msgs[1].updateComponents.surfaceId = 's' + label[0];
  try {
    proc.processMessages(msgs as unknown as A2uiMessage[]);
  } catch (e) {
    section.insertAdjacentHTML('beforeend', `<pre>THROW ${(e as Error).message}</pre>`);
  }
}
