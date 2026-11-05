// Inline confirmation cards (Workspaces SPEC phase 3, ADR 0044): the client asks in the flow, next to
// the action, never with window.confirm or a modal. The host keeps the "asking" state.
import {html} from 'lit';

export interface ConfirmCard {
  message: string;
  confirm: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** `danger` for an action that can't be undone. */
  tone?: 'danger' | 'warn';
}

export function confirmCard(c: ConfirmCard) {
  return html`<div class="confirm-card ${c.tone ?? 'warn'}" role="group" aria-label="Confirm">
    <p>${c.message}</p>
    <div class="actions">
      <button type="button" class="card-confirm" @click=${c.onConfirm}>${c.confirm}</button>
      <button type="button" class="card-cancel" @click=${c.onCancel}>Cancel</button>
    </div>
  </div>`;
}
