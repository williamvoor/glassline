// A Board's Controls on the client (explorer SPEC §18 slice 2). The server sends the Controls
// Card and its data model once; after that every change under `/controls` comes from the user.
// One subscription per Board surface diffs the converted values against the last ones sent, and
// sends the full values with a new `seq`. A `number` (a slider, which writes on every drag tick)
// is debounced; any other change sends at once. "busy" or a rejection writes the displayed values
// back, after setting "last sent" to them, so the write sends nothing.

export const CONTROL_DEBOUNCE_MS = 300;
const ALL = 'all';

export type ControlKind = 'choice' | 'multi' | 'toggle' | 'number' | 'date';

export interface ControlDecl {
  name: string;
  kind: ControlKind;
}

/** What the server accepts per kind: an option index or null, index lists, bool, int, ISO or null. */
export type WireValue = number | null | number[] | boolean | string;
export type WireValues = Record<string, WireValue>;

/** The slice of web_core's DataModel this needs (a fake in tests). */
export interface ControlsDataModel {
  get(path: string): unknown;
  set(path: string, value: unknown): unknown;
  subscribe(path: string, onChange: (value: unknown) => void): {unsubscribe(): void};
}

/** One Control's data-model value, converted for the wire (SPEC §18 "The client"). */
export function toWire(kind: ControlKind, raw: unknown): WireValue {
  switch (kind) {
    case 'choice': {
      const first = Array.isArray(raw) ? raw[0] : raw;
      if (first === undefined || first === null || first === ALL) return null;
      const index = Number(first);
      return Number.isInteger(index) ? index : null;
    }
    case 'multi':
      return (Array.isArray(raw) ? raw : []).map(Number).filter(Number.isInteger);
    case 'toggle':
      return raw === true;
    case 'number':
      return Math.round(Number(raw));
    case 'date':
      return typeof raw === 'string' && raw ? raw : null;
  }
}

export function wireValues(controls: ControlDecl[], raw: Record<string, unknown>): WireValues {
  return Object.fromEntries(controls.map(c => [c.name, toWire(c.kind, raw[c.name])]));
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

interface Timers {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

const realTimers: Timers = {set: (fn, ms) => setTimeout(fn, ms), clear: h => clearTimeout(h as ReturnType<typeof setTimeout>)};

export interface BoardControlsOptions {
  board: string; // the Board instance, b<n>
  controls: ControlDecl[];
  dataModel: ControlsDataModel;
  send: (seq: number, values: WireValues) => void;
  onChange?: () => void; // the pending state changed (to dim the Board)
  debounceMs?: number;
  timers?: Timers;
}

export class BoardControls {
  readonly board: string;
  private readonly controls: ControlDecl[];
  private readonly model: ControlsDataModel;
  private readonly sendFn: BoardControlsOptions['send'];
  private readonly notify: () => void;
  private readonly debounceMs: number;
  private readonly timers: Timers;
  private readonly subscription: {unsubscribe(): void};
  private lastSent: WireValues;
  private lastSeen: WireValues; // the values at the previous notification
  private displayedRaw: Record<string, unknown>;
  private displayedSeq = 0;
  private seq = 0;
  private sentRaw = new Map<number, Record<string, unknown>>();
  private timer: unknown;
  private inflight = new Set<number>();
  private writing = false; // our own revert writes: each one fires the subscription

  constructor(options: BoardControlsOptions) {
    this.board = options.board;
    this.controls = options.controls;
    this.model = options.dataModel;
    this.sendFn = options.send;
    this.notify = options.onChange ?? (() => {});
    this.debounceMs = options.debounceMs ?? CONTROL_DEBOUNCE_MS;
    this.timers = options.timers ?? realTimers;
    this.displayedRaw = this.raw();
    this.lastSent = wireValues(this.controls, this.displayedRaw);
    this.lastSeen = this.lastSent;
    // Fires for any write below /controls, synchronously, with a fresh copy each time.
    this.subscription = this.model.subscribe('/controls', () => this.changed());
  }

  /** A re-query of this Board is waiting for its reply. */
  get pending(): boolean {
    return this.inflight.size > 0;
  }

  private raw(): Record<string, unknown> {
    const value = this.model.get('/controls');
    return value && typeof value === 'object' ? {...(value as Record<string, unknown>)} : {};
  }

  private changed() {
    if (this.writing) return;
    const values = wireValues(this.controls, this.raw());
    // The kind that decides is what this write changed, not what differs from the last send: a
    // toggle flipped while a slider's send is pending goes at once, taking the slider with it.
    const now = this.controls.filter(c => !same(values[c.name], this.lastSeen[c.name]));
    this.lastSeen = values;
    if (same(values, this.lastSent)) {
      this.cancelTimer(); // a slider dragged back to where it was
      return;
    }
    if (now.length && now.every(c => c.kind === 'number')) {
      this.cancelTimer();
      this.timer = this.timers.set(() => this.flush(), this.debounceMs);
      return;
    }
    if (!now.length && this.timer !== undefined) return; // a no-op write during a drag
    this.flush();
  }

  private cancelTimer() {
    if (this.timer !== undefined) this.timers.clear(this.timer);
    this.timer = undefined;
  }

  /** Sends the full values now, if they differ from the last sent. */
  flush() {
    this.cancelTimer();
    const raw = this.raw();
    const values = wireValues(this.controls, raw);
    if (same(values, this.lastSent)) return;
    this.lastSent = values;
    this.sentRaw.set(++this.seq, raw);
    this.inflight.add(this.seq);
    this.notify();
    this.sendFn(this.seq, values);
  }

  /** An in-place reply for `seq`: true when it is newer than what is displayed (apply it). */
  applied(seq: number): boolean {
    if (seq <= this.displayedSeq || !this.sentRaw.has(seq)) return false;
    this.displayedSeq = seq;
    this.displayedRaw = this.sentRaw.get(seq)!;
    for (const s of [...this.sentRaw.keys()]) if (s <= seq) this.sentRaw.delete(s);
    if (!this.hasNewerIntent(seq)) this.restoreDisplayed();
    return true;
  }

  private hasNewerIntent(seq: number): boolean {
    return this.timer !== undefined || [...this.sentRaw.keys()].some(s => s > seq);
  }

  /** A failed request cannot roll back a newer request or an unsent slider edit. */
  revert(seq: number) {
    this.sentRaw.delete(seq);
    if (seq > this.displayedSeq && !this.hasNewerIntent(seq)) this.restoreDisplayed();
  }

  private restoreDisplayed() {
    this.lastSent = wireValues(this.controls, this.displayedRaw); // first, so the writes send nothing
    this.lastSeen = this.lastSent;
    this.writing = true;
    try {
      for (const c of this.controls) this.model.set(`/controls/${c.name}`, this.displayedRaw[c.name]);
    } finally {
      this.writing = false;
    }
  }

  /** A reply of any kind arrived for one sent change. */
  settled(seq: number) {
    this.inflight.delete(seq);
    this.sentRaw.delete(seq);
    this.notify();
  }

  dispose() {
    this.cancelTimer();
    this.subscription.unsubscribe();
  }
}
