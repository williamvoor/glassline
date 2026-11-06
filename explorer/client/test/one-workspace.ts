// The app always runs with Workspaces (ADR 0048). Tests that aren't about Workspaces mount it with
// this API: one available Workspace, no saved Conversations.
import type {WorkspaceApi, WorkspaceInfo} from '../src/workspaces.js';

export const ONE: WorkspaceInfo = {name: 'one', description: 'One', status: 'available', url: 'http://agent/w/one'};

export function oneWorkspace(): WorkspaceApi {
  return {
    list: async () => [ONE],
    reload: async () => [ONE],
    conversations: async () => [],
    conversation: async () => undefined,
    level: async () => { throw new Error('no saved Levels'); },
    remove: async () => {},
    removeAll: async () => {},
  };
}
