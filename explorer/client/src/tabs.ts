// The tab rules of the investigation (explorer SPEC §18, slice 1b), kept pure so they test alone.
// The array is the strip's order; `seq` is the order tabs were opened in, which the log follows.
// A closed tab stays in the array (and the log), so it reopens in place from memory.

export const MAX_TABS = 8;

export interface TabState {
  key: string; // the client's own key: the server's t<n> arrives only with the reply
  seq: number;
  closed: boolean;
  isNew: boolean; // shows "NEW" until first selected away from
  fixed?: boolean; // the Explorer: never closed, never closed by the cap
  running?: boolean; // waiting for its reply: never closed
}

/** Opens `tab` right after the tab `after` (or at the end) and applies `MAX_TABS`. */
export function openTab<T extends TabState>(tabs: T[], tab: T, after: string | undefined): T[] {
  const at = after === undefined ? -1 : tabs.findIndex(t => t.key === after);
  const next = at < 0 ? [...tabs, tab] : [...tabs.slice(0, at + 1), tab, ...tabs.slice(at + 1)];
  return applyCap(next, tab.key);
}

/** While more than `MAX_TABS` are open, closes the oldest open tab that is neither selected,
 * fixed nor running. */
export function applyCap<T extends TabState>(tabs: T[], selected: string | undefined): T[] {
  let out = tabs;
  while (out.filter(t => !t.closed).length > MAX_TABS) {
    const victim = out
      .filter(t => !t.closed && !t.fixed && !t.running && t.key !== selected)
      .sort((a, b) => a.seq - b.seq)[0];
    if (!victim) break;
    out = out.map(t => (t === victim ? {...t, closed: true} : t));
  }
  return out;
}

/** Closes `key` (never a fixed or running tab). When it was selected, the nearest open tab to its
 * left is selected instead, or else the one to its right. */
export function closeTab<T extends TabState>(tabs: T[], key: string, selected: string | undefined): {tabs: T[]; selected: string | undefined} {
  const index = tabs.findIndex(t => t.key === key);
  const tab = tabs[index];
  if (!tab || tab.closed || tab.fixed || tab.running) return {tabs, selected};
  const out = tabs.map(t => (t === tab ? {...t, closed: true} : t));
  if (selected !== key) return {tabs: out, selected};
  const left = out.slice(0, index).reverse().find(t => !t.closed);
  const right = out.slice(index + 1).find(t => !t.closed);
  return {tabs: out, selected: (left ?? right)?.key};
}

/** Selects `to`, reopening it if closed (then applying the cap). The tab selected away from loses
 * its "NEW" tag. */
export function selectTab<T extends TabState>(tabs: T[], from: string | undefined, to: string): T[] {
  const out = tabs.map(t => {
    if (t.key === to && t.closed) return {...t, closed: false};
    if (t.key === from && from !== to && t.isNew) return {...t, isNew: false};
    return t;
  });
  return applyCap(out, to);
}

/** Tabs in the order they were opened: the investigation log. */
export function logOrder<T extends TabState>(tabs: T[]): T[] {
  return [...tabs].sort((a, b) => a.seq - b.seq);
}
