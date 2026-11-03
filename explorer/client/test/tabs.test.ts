// The pure tab rules (explorer SPEC §18 slice 1b, B12).
import {describe, expect, it} from 'vitest';
import {MAX_TABS, applyCap, closeTab, logOrder, openTab, selectTab, type TabState} from '../src/tabs.js';

const tab = (key: string, seq: number, extra: Partial<TabState> = {}): TabState => ({key, seq, closed: false, isNew: false, ...extra});
const keys = (tabs: TabState[]) => tabs.filter(t => !t.closed).map(t => t.key);

describe('tabs', () => {
  it('opens a tab right after the given one, or at the end', () => {
    const tabs = [tab('x', 0, {fixed: true}), tab('a', 1), tab('b', 2)];
    expect(keys(openTab(tabs, tab('c', 3), 'a'))).toEqual(['x', 'a', 'c', 'b']);
    expect(keys(openTab(tabs, tab('c', 3), undefined))).toEqual(['x', 'a', 'b', 'c']);
    expect(keys(openTab(tabs, tab('c', 3), 'gone'))).toEqual(['x', 'a', 'b', 'c']);
  });

  it(`keeps ${MAX_TABS} open, closing the oldest that is not selected, fixed or running`, () => {
    let tabs = [tab('x', 0, {fixed: true}), tab('old', 1, {running: true})];
    for (let i = 2; i <= MAX_TABS; i++) tabs = openTab(tabs, tab(`t${i}`, i), undefined);
    expect(keys(tabs)).toHaveLength(MAX_TABS);
    tabs = openTab(tabs, tab('new', 99), undefined);
    expect(keys(tabs)).toHaveLength(MAX_TABS);
    expect(tabs.find(t => t.key === 't2')!.closed).toBe(true); // the oldest closable one
    expect(keys(tabs)).toContain('x');
    expect(keys(tabs)).toContain('old');
    expect(tabs.map(t => t.key)).toContain('t2'); // closed, still there for the log
  });

  it('never closes the selected tab to make room', () => {
    let tabs = Array.from({length: MAX_TABS}, (_, i) => tab(`t${i}`, i));
    tabs = applyCap([...tabs, tab('extra', 50)], 't0');
    expect(keys(tabs)).not.toContain('t1');
    expect(keys(tabs)).toContain('t0');
  });

  it('closes a tab and selects its left neighbour, or its right one; fixed and running tabs stay', () => {
    const tabs = [tab('x', 0, {fixed: true}), tab('a', 1), tab('b', 2), tab('r', 3, {running: true})];
    expect(closeTab(tabs, 'b', 'b')).toMatchObject({selected: 'a'});
    expect(closeTab(tabs, 'b', 'a').selected).toBe('a');
    expect(keys(closeTab(tabs, 'x', 'x').tabs)).toEqual(['x', 'a', 'b', 'r']);
    expect(keys(closeTab(tabs, 'r', 'r').tabs)).toEqual(['x', 'a', 'b', 'r']);
    const lone = [tab('a', 1), tab('b', 2)];
    expect(closeTab(lone, 'a', 'a').selected).toBe('b');
  });

  it('selecting reopens a closed tab and takes NEW off the tab selected away from', () => {
    const tabs = [tab('a', 1, {isNew: true}), tab('b', 2, {closed: true, isNew: true})];
    const out = selectTab(tabs, 'a', 'b');
    expect(out.map(t => [t.key, t.closed, t.isNew])).toEqual([['a', false, false], ['b', false, true]]);
    expect(selectTab(tabs, 'a', 'a')[0]!.isNew).toBe(true); // selecting itself changes nothing
  });

  it('lists tabs for the log in the order they opened, closed ones included', () => {
    const tabs = [tab('x', 0), tab('c', 3, {closed: true}), tab('a', 1)];
    expect(logOrder(tabs).map(t => t.key)).toEqual(['x', 'a', 'c']);
  });
});
