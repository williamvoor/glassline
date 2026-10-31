// The merged explorer catalog (ADR 0002): basic v0.9 + VegaChart + DataGrid + SqlDetails.
// Must match the agent's EXPLORER_CATALOG_ID and catalog/explorer_custom_components.json.
import {basicCatalog} from '@a2ui/lit/v0_9';
import {Catalog, MessageProcessor, type ComponentApi} from '@a2ui/web_core/v0_9';
import {DataGrid} from './components/data-grid.js';
import {SqlDetails} from './components/sql-details.js';
import {A2uiVegaChart} from './vendor/vegachart/index.js';

export const EXPLORER_CATALOG_ID = 'urn:explorer:catalog:v1';

export const CUSTOM_COMPONENTS = [A2uiVegaChart, DataGrid, SqlDetails] as const;

export const explorerCatalog = new Catalog<ComponentApi>(
  EXPLORER_CATALOG_ID,
  '0.9',
  [...basicCatalog.components.values(), ...CUSTOM_COMPONENTS] as ComponentApi[],
  [...basicCatalog.functions.values()],
);

export function createProcessor(): MessageProcessor<ComponentApi> {
  return new MessageProcessor<ComponentApi>([explorerCatalog]);
}
