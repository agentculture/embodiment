// hooks/fakeEventSource.ts
//
// Test-only stand-in for the browser's native EventSource (jsdom does not
// implement it). Implements exactly the subset useEventStream.ts consumes:
// addEventListener(kind, handler), onopen/onerror assignment, readyState,
// and close(). Not a test file itself (no *.test.ts suffix), so vitest's
// `include: ["src/**/*.test.{ts,tsx}"]` never tries to run it as a suite —
// it is imported BY test files instead.

export const CONNECTING = 0;
export const OPEN = 1;
export const CLOSED = 2;

type Listener = (event: MessageEvent<string>) => void;

/** One fake instance per `new FakeEventSource(url)` call — mirrors the real
 *  EventSource contract closely enough for useEventStream.ts's needs, and
 *  exposes `emit`/`error`/`open` test seams the real class has no
 *  equivalent for. */
export class FakeEventSource {
  readonly url: string;
  readyState: number = CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private readonly listeners = new Map<string, Set<Listener>>();
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(kind: string, listener: EventListener): void {
    let set = this.listeners.get(kind);
    if (!set) {
      set = new Set();
      this.listeners.set(kind, set);
    }
    set.add(listener as Listener);
  }

  removeEventListener(kind: string, listener: EventListener): void {
    this.listeners.get(kind)?.delete(listener as Listener);
  }

  close(): void {
    this.closed = true;
    this.readyState = CLOSED;
  }

  /** Test seam: simulate the connection opening. */
  open(): void {
    this.readyState = OPEN;
    this.onopen?.();
  }

  /** Test seam: simulate the connection failing/closing. */
  error(): void {
    this.readyState = CLOSED;
    this.onerror?.();
  }

  /** Test seam: simulate one SSE frame `event: <kind>\ndata: <json>`. */
  emit(kind: string, data: unknown): void {
    const listeners = this.listeners.get(kind);
    if (!listeners) return;
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    for (const listener of Array.from(listeners)) listener(event);
  }

  /** Test seam: simulate a frame whose `data:` is not valid JSON. */
  emitRaw(kind: string, rawData: string): void {
    const listeners = this.listeners.get(kind);
    if (!listeners) return;
    const event = { data: rawData } as MessageEvent<string>;
    for (const listener of Array.from(listeners)) listener(event);
  }

  static instances: FakeEventSource[] = [];

  static reset(): void {
    FakeEventSource.instances = [];
  }

  static latest(): FakeEventSource {
    const last = FakeEventSource.instances.at(-1);
    if (!last) throw new Error("no FakeEventSource has been constructed yet");
    return last;
  }
}
