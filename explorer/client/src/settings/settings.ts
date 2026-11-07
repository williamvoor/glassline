// <explorer-settings>: the Workspace list, and the New Workspace wizard behind it (specs/workspaces/SPEC.md
// phases 2 and 3, ADRs 0041 and 0044). `view` is `list` (#/settings) or `wizard` (#/settings/new); it fires
// `settings-view` so the app writes the hash. The wizard stays mounted (hidden) under the list, so a Draft
// survives both the list and the explorer. No pop-ups: Delete asks on a card in its row.
import {LitElement, html, nothing} from 'lit';
import {customElement, property, state} from 'lit/decorators.js';

import {confirmCard} from '../cards.js';
import {Api, type SettingsWorkspace} from './api.js';
import './wizard.js';
import type {ExplorerWizard} from './wizard.js';

export const BOOTSTRAP_URL = import.meta.env.VITE_BOOTSTRAP_URL ?? 'http://127.0.0.1:8765';

export type SettingsView = 'list' | 'wizard';

@customElement('explorer-settings')
export class ExplorerSettings extends LitElement {
  @property({attribute: false}) api = new Api(undefined, BOOTSTRAP_URL);
  /** Milliseconds between progress polls while drafting; tests set 0. */
  @property({type: Number}) pollMs = 500;
  @property() view: SettingsView = 'list';

  @state() workspaces: SettingsWorkspace[] = [];
  @state() deleting = ''; // the Workspace whose Delete card is open
  @state() busy = false;
  @state() error = '';
  @state() notice = '';

  protected override createRenderRoot() {
    return this;
  }

  override connectedCallback() {
    super.connectedCallback();
    void this.load();
  }

  protected override updated(changed: Map<string, unknown>) {
    if (changed.has('view') && this.view === 'wizard') void this.wizard?.resume();
  }

  get wizard(): ExplorerWizard | null {
    return this.querySelector<ExplorerWizard>('explorer-wizard');
  }

  private async guard(fn: () => Promise<void>) {
    this.error = '';
    try {
      await fn();
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    }
  }

  /** Tells the explorer the Workspace list changed (the picker reloads it). */
  private changed() {
    this.dispatchEvent(new CustomEvent('workspaces-changed', {bubbles: true, composed: true}));
  }

  show(view: SettingsView) {
    this.view = view;
    this.notice = '';
    this.dispatchEvent(new CustomEvent('settings-view', {detail: view, bubbles: true, composed: true}));
    if (view === 'list') void this.load();
  }

  async load() {
    await this.guard(async () => {
      this.workspaces = (await this.api.workspaces()).workspaces;
    });
  }

  async deleteWorkspace(ws: SettingsWorkspace) {
    this.deleting = '';
    this.busy = true;
    await this.guard(async () => {
      const r = await this.api.deleteWorkspace(ws.name);
      this.workspaces = r.workspaces;
      this.notice = `Deleted ${ws.name}.`;
      this.changed();
    });
    this.busy = false;
  }

  async regenerate(ws: {name: string}) {
    this.show('wizard');
    await this.updateComplete;
    await this.wizard?.regenerate(ws.name);
  }

  async reloadAgent() {
    await this.guard(async () => {
      const r = await this.api.reload();
      this.notice = r.reloaded
        ? 'The agent reloaded its Workspaces.'
        : `The agent didn't reload${r.error ? `: ${r.error}` : r.status === 0 ? ': it is not running' : ''}.`;
      this.changed();
    });
  }

  private deleteCard(ws: SettingsWorkspace) {
    const what = ws.kind === 'data'
      ? `its Dataset and its saved Conversations (${ws.name} in the data directory)`
      : `its saved Conversations (the repo Dataset's files are not touched)`;
    return html`<tr class="delete-row"><td colspan="4">${confirmCard({
      message: `Delete the Workspace ${ws.name}? This deletes ${what}. It can't be undone.`,
      confirm: `Delete ${ws.name}`, tone: 'danger',
      onConfirm: () => void this.deleteWorkspace(ws),
      onCancel: () => (this.deleting = ''),
    })}</td></tr>`;
  }

  private renderList() {
    const resume = this.wizard?.inProgress || this.wizard?.runningNow;
    return html`<section class="settings-workspaces glass" ?hidden=${this.view !== 'list'}>
      <header class="list-head">
        <div>
          <h1>Workspaces</h1>
          <p class="muted">Each Workspace is one database with its description, notes and Boards.</p>
        </div>
        <button type="button" class="primary new-workspace" ?disabled=${this.busy} @click=${() => this.show('wizard')}>
          ${resume ? 'Continue the New Workspace' : 'New Workspace'}</button>
      </header>
      ${this.notice ? html`<p class="ok notice-settings" role="status">${this.notice}</p>` : nothing}
      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : nothing}
      ${this.workspaces.length
        ? html`<table>
            <thead><tr><th>Name</th><th>Login variable</th><th>Dataset</th><th></th></tr></thead>
            <tbody>
              ${this.workspaces.map(ws => html`<tr>
                  <td>${ws.name}</td>
                  <td>${ws.dsn_env ? html`<code>${ws.dsn_env}</code>` : html`<span class="error">missing</span>`}</td>
                  <td>${ws.kind === 'data' ? 'made in Settings' : html`in the repo <span class="muted">(regenerate it with
                    <code>regen_dataset.sh</code>)</span>`}</td>
                  <td class="actions">
                    ${ws.kind === 'data'
                      ? html`<button class="regenerate" ?disabled=${this.busy || resume}
                          title=${resume ? 'Finish or discard the New Workspace first' : nothing}
                          @click=${() => void this.regenerate(ws)}>Regenerate</button>`
                      : nothing}
                    <button class="delete-workspace" ?disabled=${this.busy} @click=${() => (this.deleting = ws.name)}>Delete</button>
                  </td>
                </tr>
                ${this.deleting === ws.name ? this.deleteCard(ws) : nothing}`)}
            </tbody>
          </table>`
        : html`<p class="muted">No Workspaces yet.</p>`}
      <p><button class="reload-agent" ?disabled=${this.busy} @click=${() => void this.reloadAgent()}>Reload the agent</button>
        <span class="muted">Picks up Workspaces and Dataset files that changed.</span></p>
    </section>`;
  }

  override render() {
    return html`<div class="settings">
      ${this.renderList()}
      <explorer-wizard ?hidden=${this.view !== 'wizard'} .api=${this.api} .pollMs=${this.pollMs}
        @wizard-close=${() => this.show('list')}
        @workspaces-changed=${() => void this.load()}></explorer-wizard>
    </div>`;
  }
}
