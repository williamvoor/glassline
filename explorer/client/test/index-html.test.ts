import {readFileSync} from 'node:fs';
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
