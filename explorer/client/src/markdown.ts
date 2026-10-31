// Markdown for Text components and the turn's prose. @a2ui/markdown-it already disables raw
// HTML and sanitizes with DOMPurify; we also drop images (explorer SPEC §9).
import {renderMarkdown} from '@a2ui/markdown-it';

export function stripImages(html: string): string {
  const template = document.createElement('template');
  template.innerHTML = html;
  template.content.querySelectorAll('img').forEach(img => img.remove());
  return template.innerHTML;
}

export async function renderSafeMarkdown(value: string, options?: unknown): Promise<string> {
  const html = await renderMarkdown(value, options as Parameters<typeof renderMarkdown>[1]);
  return stripImages(html);
}
