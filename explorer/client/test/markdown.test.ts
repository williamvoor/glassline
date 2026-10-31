import {describe, expect, it} from 'vitest';
import {renderSafeMarkdown} from '../src/markdown.js';

describe('markdown', () => {
  it('drops images, keeps links and emphasis, and renders no raw HTML', async () => {
    const html = await renderSafeMarkdown(
      '**Bold** and [a link](https://example.com) ![pic](https://example.com/x.png) <b onclick="x()">raw</b>',
    );
    const div = document.createElement('div');
    div.innerHTML = html;
    expect(div.querySelector('strong')?.textContent).toBe('Bold');
    expect(div.querySelector('a')?.getAttribute('href')).toBe('https://example.com');
    expect(div.querySelector('img')).toBeNull();
    expect(div.querySelector('b')).toBeNull();
    expect(div.textContent).toContain('<b onclick="x()">raw</b>');
  });
});
