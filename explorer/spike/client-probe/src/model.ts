// Renders a real model reply (Express, after macro expansion) with the merged catalog.
import {basicCatalog} from '@a2ui/lit/v0_9';
import '@a2ui/lit/v0_9';
import {Catalog, MessageProcessor, type A2uiMessage, type ComponentApi} from '@a2ui/web_core/v0_9';
import {A2uiVegaChart} from './vegachart/index.js';
import transport from './model_transport.json';

const merged = new Catalog<ComponentApi>(
  'urn:explorer:catalog:basic-vegachart:v0_9', '0.9',
  [...basicCatalog.components.values(), A2uiVegaChart] as ComponentApi[],
  [...basicCatalog.functions.values()],
);
const proc = new MessageProcessor<ComponentApi>([merged]);
proc.onSurfaceCreated(surface => {
  const el = document.createElement('a2ui-surface') as HTMLElement & {surface: unknown};
  el.surface = surface;
  document.getElementById('out')!.appendChild(el);
});
proc.processMessages(transport as unknown as A2uiMessage[]);
