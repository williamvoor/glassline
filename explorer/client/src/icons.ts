// Stroke icons from the §24 design mock-up (docs/explorer-redesign). Decorative only: each is
// aria-hidden, so a button's accessible name stays its text. They take the text colour.
import {svg} from 'lit';

const icon = (body: ReturnType<typeof svg>, size = 16, width = 2) => svg`<svg class="icon" width=${size} height=${size}
  viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width=${width} stroke-linecap="round" stroke-linejoin="round"
  aria-hidden="true" focusable="false">${body}</svg>`;

export const plusIcon = () => icon(svg`<path d="M12 5v14M5 12h14"></path>`, 16, 2.2);

export const settingsIcon = () => icon(svg`<circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"></path>`);

/** The Explorer Board's tab: a dashboard of panels. */
export const boardIcon = () => icon(svg`<rect x="3" y="3" width="7" height="9" rx="1.5"></rect><rect x="14" y="3" width="7" height="5" rx="1.5"></rect><rect x="14" y="12" width="7" height="9" rx="1.5"></rect><rect x="3" y="16" width="7" height="5" rx="1.5"></rect>`, 15);

export const chatIcon = () => icon(svg`<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>`);

export const chevronIcon = () => icon(svg`<path d="m9 18 6-6-6-6"></path>`, 14);

export const arrowIcon = () => icon(svg`<path d="M5 12h14"></path><path d="m13 6 6 6-6 6"></path>`, 16, 2.2);
