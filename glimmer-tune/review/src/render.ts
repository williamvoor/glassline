// Headless render page for the validator (glimmer-tune a2ui_eval/render.py, layer 2).
// `window.a2uiRender(messages)` renders the surfaces, waits for them to update, and returns
// what the page can see: an error, empty containers and horizontal overflow.
import {LitElement, html} from 'lit';
import {customElement, property} from 'lit/decorators.js';
import {provide} from '@lit/context';
import {Context, basicCatalog} from '@a2ui/lit/v0_9';
import '@a2ui/lit/v0_9';
import {MessageProcessor, type A2uiMessage, type ComponentApi} from '@a2ui/web_core/v0_9';
import {renderMarkdown} from '@a2ui/markdown-it';

interface Report {
  error: string | null;
  surfaces: string[];
  empty: string[]; // container tags with nothing visible inside
  overflow: number; // px past the viewport's right edge (0 when it fits)
}

declare global {
  interface Window {
    a2uiRender: (messages: Record<string, unknown>[]) => Promise<Report>;
    a2uiReady: boolean;
  }
}

@customElement('render-root')
export class RenderRoot extends LitElement {
  @provide({context: Context.markdown})
  markdownRenderer = (value: string, options?: unknown) =>
    renderMarkdown(value, options as Parameters<typeof renderMarkdown>[1]);

  @property({attribute: false}) surfaces: unknown[] = [];

  override createRenderRoot() {
    return this; // light DOM, so layout can be measured from the document
  }

  override render() {
    return html`${this.surfaces.map(s => html`<a2ui-surface .surface=${s}></a2ui-surface>`)}`;
  }
}

const frames = (n: number) =>
  new Promise<void>(r => {
    const step = (k: number) => (k ? requestAnimationFrame(() => step(k - 1)) : r());
    step(n);
  });

// Components that exist to hold others. Modal and Tabs show only part of their content by
// design, so they aren't checked.
const CONTAINER = /^a2ui-(basic-)?(row|column|card|list)$/;

const visible = (el: Element) => {
  const b = el.getBoundingClientRect();
  return b.width > 0 && b.height > 0;
};

function emptyContainers(root: Element): string[] {
  const out: string[] = [];
  for (const el of root.querySelectorAll('*')) {
    const tag = el.tagName.toLowerCase();
    if (!CONTAINER.test(tag)) continue;
    const inner = [...el.querySelectorAll('*')].some(visible) || (el.textContent ?? '').trim() !== '';
    if (!inner) out.push(tag);
  }
  return out;
}

window.a2uiRender = async messages => {
  const root = document.querySelector('render-root') as RenderRoot;
  root.surfaces = [];
  await root.updateComplete;
  const processor = new MessageProcessor<ComponentApi>([basicCatalog]);
  try {
    processor.processMessages(messages as unknown as A2uiMessage[]);
  } catch (e) {
    return {error: `Renderer: ${String(e)}`, surfaces: [], empty: [], overflow: 0};
  }
  const ids = messages.flatMap(m => ('createSurface' in m ? [(m.createSurface as {surfaceId: string}).surfaceId] : []));
  const found = ids.map(id => processor.model.surfacesMap.get(id)).filter(s => s !== undefined);
  if (!found.length) return {error: 'no surface in the messages', surfaces: [], empty: [], overflow: 0};
  root.surfaces = found;
  await root.updateComplete;
  await frames(3);
  let right = document.documentElement.scrollWidth;
  for (const el of root.querySelectorAll('*')) right = Math.max(right, el.getBoundingClientRect().right);
  return {error: null, surfaces: ids, empty: emptyContainers(root), overflow: Math.max(0, Math.round(right - window.innerWidth))};
};
window.a2uiReady = true;
