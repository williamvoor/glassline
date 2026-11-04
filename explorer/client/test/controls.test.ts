// Board Controls on the client (explorer SPEC §18 slice 2, B13), against web_core's real DataModel,
// so the `/controls` subscription's behaviour is tested, not assumed.
import {describe, expect, it} from 'vitest';
import {DataModel} from '@a2ui/web_core/v0_9';
import {BoardControls, CONTROL_DEBOUNCE_MS, toWire, type ControlDecl, type WireValues} from '../src/controls.js';

const CONTROLS: ControlDecl[] = [
  {name: 'method', kind: 'choice'},
  {name: 'amends', kind: 'multi'},
  {name: 'hide', kind: 'toggle'},
  {name: 'min', kind: 'number'},
  {name: 'since', kind: 'date'},
];
const INITIAL = {method: ['all'], amends: [], hide: false, min: 0, since: ''};

/** Manual timers, so the debounce is tested without waiting. */
function fakeTimers() {
  let next = 1;
  const pending = new Map<number, () => void>();
  return {
    set: (fn: () => void) => {
      pending.set(next, fn);
      return next++;
    },
    clear: (h: unknown) => void pending.delete(h as number),
    fire() {
      const fns = [...pending.values()];
      pending.clear();
      fns.forEach(f => f());
    },
    get count() {
      return pending.size;
    },
  };
}

function setup() {
  const model = new DataModel({});
  model.set('/controls', structuredClone(INITIAL)); // the server's one write, before the subscription
  const sent: {seq: number; values: WireValues}[] = [];
  const timers = fakeTimers();
  const ctrl = new BoardControls({
    board: 'b1', controls: CONTROLS, dataModel: model, send: (seq, values) => sent.push({seq, values}), timers,
  });
  return {model, sent, timers, ctrl};
}

describe('toWire', () => {
  it('converts each kind from the data model', () => {
    expect(toWire('choice', ['all'])).toBeNull();
    expect(toWire('choice', ['2'])).toBe(2);
    expect(toWire('choice', [])).toBeNull();
    expect(toWire('multi', ['3', '1'])).toEqual([3, 1]);
    expect(toWire('toggle', true)).toBe(true);
    expect(toWire('number', 7)).toBe(7);
    expect(toWire('date', '')).toBeNull();
    expect(toWire('date', '2024-01-02')).toBe('2024-01-02');
  });
});

describe('BoardControls', () => {
  it('sends nothing for the server\'s initial data model, then the full values with seq on a user change', () => {
    const {model, sent} = setup();
    expect(sent).toEqual([]);
    model.set('/controls/method', ['1']); // what ChoicePicker writes
    expect(sent).toEqual([{seq: 1, values: {method: 1, amends: [], hide: false, min: 0, since: null}}]);
    model.set('/controls/amends', ['2', '0']);
    model.set('/controls/hide', true);
    model.set('/controls/since', '2024-02-01');
    expect(sent.map(s => s.seq)).toEqual([1, 2, 3, 4]); // monotonic
    expect(sent.at(-1)!.values).toEqual({method: 1, amends: [2, 0], hide: true, min: 0, since: '2024-02-01'});
  });

  it('sends nothing when a write leaves the values unchanged', () => {
    const {model, sent} = setup();
    model.set('/controls/method', ['all']);
    model.set('/controls/hide', false);
    expect(sent).toEqual([]);
  });

  it('debounces a number drag into one action after it settles; another kind sends at once', () => {
    const {model, sent, timers} = setup();
    for (const v of [1, 2, 3, 4]) model.set('/controls/min', v); // a slider writes on every tick
    expect(sent).toEqual([]);
    expect(timers.count).toBe(1);
    timers.fire();
    expect(sent).toEqual([{seq: 1, values: {...{method: null, amends: [], hide: false, since: null}, min: 4}}]);
    model.set('/controls/min', 9);
    model.set('/controls/hide', true); // sends now, with the pending number in it
    expect(sent.at(-1)).toEqual({seq: 2, values: {method: null, amends: [], hide: true, min: 9, since: null}});
    expect(timers.count).toBe(0);
    expect(CONTROL_DEBOUNCE_MS).toBe(300);
  });

  it('a busy revert writes the displayed values back and sends nothing', () => {
    const {model, sent, ctrl} = setup();
    model.set('/controls/method', ['1']);
    expect(ctrl.applied(1)).toBe(true); // displayed: method 1
    model.set('/controls/method', ['2']);
    model.set('/controls/hide', true);
    expect(sent).toHaveLength(3);
    ctrl.revert(); // "busy"
    expect(model.get('/controls')).toEqual({...INITIAL, method: ['1']});
    expect(sent).toHaveLength(3); // the write-back sent nothing
    model.set('/controls/method', ['2']); // and a later change is still a change
    expect(sent).toHaveLength(4);
  });

  it('applies only replies newer than what is displayed; a superseded seq is never applied', () => {
    const {model, ctrl} = setup();
    model.set('/controls/method', ['1']);
    model.set('/controls/method', ['2']);
    expect(ctrl.applied(2)).toBe(true);
    expect(ctrl.applied(1)).toBe(false);
    expect(ctrl.applied(2)).toBe(false);
    expect(ctrl.applied(7)).toBe(false); // never sent
  });

  it('is pending from a send until its reply settles', () => {
    const {model, ctrl} = setup();
    expect(ctrl.pending).toBe(false);
    model.set('/controls/hide', true);
    expect(ctrl.pending).toBe(true);
    ctrl.settled();
    expect(ctrl.pending).toBe(false);
  });

  it('stops listening when disposed', () => {
    const {model, sent, ctrl} = setup();
    ctrl.dispose();
    model.set('/controls/hide', true);
    expect(sent).toEqual([]);
  });
});
