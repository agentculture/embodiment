// hooks/sseConnection.ts
//
// The transport abstraction useEventStream.ts depends on. Round 4: replaces
// EventSource entirely (see api/sseFetchReader.ts's module docstring for
// why) with a `connect` seam that both the real fetch-based reader and the
// test double (hooks/fakeSSEConnection.ts) implement identically, so
// useEventStream.ts's own logic never has to know which one it's talking
// to.

export interface SSEFrame {
  /** The SSE `event:` field -- one of embodiment/bus.py's EVENT_KINDS on
   *  the wire, or "message" for a frame with no `event:` line (never
   *  emitted by embodiment/bus.py's projection, but the generic SSE
   *  default per the WHATWG event-stream spec). */
  kind: string;
  /** The frame's `data:` field(s), already joined (multiple `data:` lines
   *  are joined with `\n` per the SSE spec) -- NOT yet JSON-parsed. */
  data: string;
  /** The frame's `id:` field, if it sent one. */
  id?: string;
}

export interface SSEConnectionCallbacks {
  /** The connection is open and frames are being delivered. */
  onOpen(): void;
  /** One complete, framed SSE event arrived. */
  onFrame(frame: SSEFrame): void;
  /** The connection just dropped -- whether or not an internal retry
   *  follows. Fired once per drop, not once per retry attempt. */
  onError(): void;
}

export interface SSEConnectionHandle {
  /** Idempotent; stops any in-flight request and any pending retry. */
  close(): void;
}

export type SSEConnect = (
  url: string,
  headers: Record<string, string>,
  callbacks: SSEConnectionCallbacks,
) => SSEConnectionHandle;
