import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';
import {basicCatalog} from '@a2ui/lit/v0_9';
import type {ZodObject, ZodRawShape} from 'zod';
import {CUSTOM_COMPONENTS, EXPLORER_CATALOG_ID, explorerCatalog} from '../src/catalog.js';

// Tests run from explorer/client (npm test).
const jsonPath = resolve(process.cwd(), '../catalog/explorer_custom_components.json');
type JsonComponent = {allOf: [unknown, {properties: Record<string, unknown>; required: string[]}]};
const jsonComponents = JSON.parse(readFileSync(jsonPath, 'utf-8')).components as Record<string, JsonComponent>;

describe('explorer catalog', () => {
  it('has the agreed ID and every basic component plus the custom ones', () => {
    expect(explorerCatalog.id).toBe(EXPLORER_CATALOG_ID);
    const names = [...explorerCatalog.components.keys()];
    for (const name of basicCatalog.components.keys()) expect(names).toContain(name);
    expect(names).toEqual(expect.arrayContaining(['VegaChart', 'DataGrid', 'SqlDetails']));
  });

  it('custom component names and properties match explorer_custom_components.json', () => {
    expect(CUSTOM_COMPONENTS.map(c => c.name).sort()).toEqual(Object.keys(jsonComponents).sort());
    for (const component of CUSTOM_COMPONENTS) {
      const json = jsonComponents[component.name]!.allOf[1];
      const jsonProps = Object.keys(json.properties).filter(p => p !== 'component').sort();
      const shape = (component.schema as unknown as ZodObject<ZodRawShape>).shape;
      expect(Object.keys(shape).sort(), component.name).toEqual(jsonProps);
      const required = Object.entries(shape).filter(([, s]) => !s.isOptional()).map(([k]) => k).sort();
      expect(required, component.name).toEqual(json.required.filter(p => p !== 'component').sort());
    }
  });
});
