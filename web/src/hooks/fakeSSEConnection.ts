// hooks/fakeSSEConnection.ts
//
// Test-only stand-in for the transport useEventStream.ts talks to (the
// `SSEConnect` seam, sseConnection.ts) -- round 4 replaces the EventSource
// double this used to be (fakeEventSource.ts, now removed) with one that
// matches the fetch-based connector's shape instead. Deliberately keeps the
// SAME control-surface method names (open/emit/emitRaw/error/close,
// static reset/latest/instances) the old double had, so the large existing
// hook/App test suites needed only their construction call sites updated,
// not every individual test body.

import type {
  SSEConnect,
  SSEConnectionCallbacks,
  SSEConnectionHandle,
  SSEFrame,
} from "./sseConnection";

export class FakeSSEConnection implements SSEConnectionHandle {
  readonly url: string;
  readonly headers: Record<string, string>;
  private readonly callbacks: SSEConnectionCallbacks;
  closed = false;

  constructor(url: string, headers: Record<string, string>, callbacks: SSEConnectionCallbacks) {
    this.url = url;
    this.headers = headers;
    this.callbacks = callbacks;
    FakeSSEConnection.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  /** Test seam: simulate the connection opening (or reopening, after an
   *  internal retry the real connector would have performed on its own). */
  open(): void {
    this.callbacks.onOpen();
  }

  /** Test seam: simulate the connection dropping. */
  error(): void {
    this.callbacks.onError();
  }

  /** Test seam: simulate one SSE frame `event: <kind>\ndata: <json>`. */
  emit(kind: string, data: unknown): void {
    const frame: SSEFrame = { kind, data: JSON.stringify(data) };
    this.callbacks.onFrame(frame);
  }

  /** Test seam: simulate a frame whose `data:` is not valid JSON (or any
   *  other raw wire text). */
  emitRaw(kind: string, rawData: string): void {
    const frame: SSEFrame = { kind, data: rawData };
    this.callbacks.onFrame(frame);
  }

  static instances: FakeSSEConnection[] = [];

  static reset(): void {
    FakeSSEConnection.instances = [];
  }

  static latest(): FakeSSEConnection {
    const last = FakeSSEConnection.instances.at(-1);
    if (!last) throw new Error("no FakeSSEConnection has been constructed yet");
    return last;
  }
}

/** An `SSEConnect` built from `FakeSSEConnection` -- pass this as
 *  `options.connect` in a test. */
export const fakeConnect: SSEConnect = (url, headers, callbacks) =>
  new FakeSSEConnection(url, headers, callbacks);
