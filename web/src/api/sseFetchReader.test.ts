import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createFetchSSEConnect } from "./sseFetchReader";
import type { SSEFrame } from "../hooks/sseConnection";

/** A controllable ReadableStream<Uint8Array> + a matching fetch mock. Each
 *  call records itself; `push`/`end`/`fail` drive the stream from the
 *  test. */
function fakeStreamingFetch() {
  const calls: { url: RequestInfo | URL; init: RequestInit }[] = [];
  let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
  let responseOk = true;
  let responseStatus = 200;
  let hasBody = true;
  let rejectNext: Error | null = null;

  const fetchFn = vi.fn(async (url: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url, init });
    if (rejectNext) {
      const err = rejectNext;
      rejectNext = null;
      throw err;
    }
    const body = hasBody
      ? new ReadableStream<Uint8Array>({
          start(c) {
            controller = c;
          },
        })
      : null;
    return {
      ok: responseOk,
      status: responseStatus,
      body,
    } as unknown as Response;
  });

  return {
    fetchFn,
    calls,
    push(text: string) {
      controller?.enqueue(new TextEncoder().encode(text));
    },
    end() {
      controller?.close();
    },
    setNotOk(status: number) {
      responseOk = false;
      responseStatus = status;
    },
    setOk() {
      responseOk = true;
      responseStatus = 200;
    },
    setNoBody() {
      hasBody = false;
    },
    failNextFetchWith(err: Error) {
      rejectNext = err;
    },
  };
}

describe("createFetchSSEConnect", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("sends the Authorization header and Accept: text/event-stream, never a cookie", () => {
    const fake = fakeStreamingFetch();
    const connect = createFetchSSEConnect({ fetchFn: fake.fetchFn });
    const handle = connect(
      "/api/events",
      { Authorization: "Bearer s3cr3t" },
      { onOpen: vi.fn(), onFrame: vi.fn(), onError: vi.fn() },
    );
    expect(fake.calls).toHaveLength(1);
    const headers = fake.calls[0].init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer s3cr3t");
    expect(headers.Accept).toBe("text/event-stream");
    expect(fake.calls[0].init.credentials).not.toBe("include");
    handle.close();
  });

  it("calls onOpen once the response arrives ok with a body", async () => {
    const fake = fakeStreamingFetch();
    const onOpen = vi.fn();
    const connect = createFetchSSEConnect({ fetchFn: fake.fetchFn });
    const handle = connect("/api/events", {}, { onOpen, onFrame: vi.fn(), onError: vi.fn() });
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));
    handle.close();
  });

  it("delivers parsed frames to onFrame as they stream in", async () => {
    const fake = fakeStreamingFetch();
    const frames: SSEFrame[] = [];
    const connect = createFetchSSEConnect({ fetchFn: fake.fetchFn });
    const handle = connect(
      "/api/events",
      {},
      { onOpen: vi.fn(), onFrame: (f) => frames.push(f), onError: vi.fn() },
    );
    await vi.waitFor(() => expect(fake.calls).toHaveLength(1));
    fake.push('event: state\ndata: {"a":1}\n\n');
    await vi.waitFor(() => expect(frames).toHaveLength(1));
    expect(frames[0]).toEqual({ kind: "state", data: '{"a":1}', id: undefined });
    handle.close();
  });

  it("calls onError (not onOpen) when the response is not ok, then retries with backoff", async () => {
    const fake = fakeStreamingFetch();
    fake.setNotOk(401);
    const onError = vi.fn();
    const onOpen = vi.fn();
    const connect = createFetchSSEConnect({
      fetchFn: fake.fetchFn,
      baseRetryDelayMs: 1000,
      maxRetryDelayMs: 8000,
    });
    const handle = connect("/api/events", {}, { onOpen, onFrame: vi.fn(), onError });
    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onOpen).not.toHaveBeenCalled();
    expect(fake.calls).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(fake.calls).toHaveLength(2));
    handle.close();
  });

  it("resets the backoff delay to the base after a successful open", async () => {
    const fake = fakeStreamingFetch();
    fake.setNotOk(500);
    const connect = createFetchSSEConnect({
      fetchFn: fake.fetchFn,
      baseRetryDelayMs: 1000,
      maxRetryDelayMs: 8000,
    });
    const onOpen = vi.fn();
    const handle = connect("/api/events", {}, { onOpen, onFrame: vi.fn(), onError: vi.fn() });
    await vi.waitFor(() => expect(fake.calls).toHaveLength(1)); // attempt 1, fails
    await vi.advanceTimersByTimeAsync(1000); // backoff after attempt 1: base (1000)
    await vi.waitFor(() => expect(fake.calls).toHaveLength(2)); // attempt 2, fails

    // attempt 3 will use a DOUBLED backoff (2000) unless it succeeds and
    // resets it -- let it succeed this time.
    fake.setOk();
    await vi.advanceTimersByTimeAsync(2000);
    await vi.waitFor(() => expect(fake.calls).toHaveLength(3));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));

    // now the (open) stream drops -- if the backoff reset to base (1000) on
    // that successful open, the NEXT attempt fires 1000ms later, not
    // 4000ms (which is what an un-reset exponential sequence would demand).
    fake.setNotOk(500);
    fake.end();
    await vi.waitFor(() => expect(fake.calls).toHaveLength(3)); // onError, no new call yet
    await vi.advanceTimersByTimeAsync(999);
    expect(fake.calls).toHaveLength(3); // not yet -- confirms the delay reset to base, not 4000ms
    await vi.advanceTimersByTimeAsync(1);
    await vi.waitFor(() => expect(fake.calls).toHaveLength(4));
    handle.close();
  });

  it("bounds the retry delay at maxRetryDelayMs however many attempts fail", async () => {
    const fake = fakeStreamingFetch();
    fake.setNotOk(500);
    const connect = createFetchSSEConnect({
      fetchFn: fake.fetchFn,
      baseRetryDelayMs: 1000,
      maxRetryDelayMs: 3000,
    });
    const handle = connect("/api/events", {}, { onOpen: vi.fn(), onFrame: vi.fn(), onError: vi.fn() });
    await vi.waitFor(() => expect(fake.calls).toHaveLength(1));
    await vi.advanceTimersByTimeAsync(1000); // attempt 2 (delay was base*2^0=1000)
    await vi.waitFor(() => expect(fake.calls).toHaveLength(2));
    await vi.advanceTimersByTimeAsync(2000); // attempt 3 (delay was base*2^1=2000)
    await vi.waitFor(() => expect(fake.calls).toHaveLength(3));
    await vi.advanceTimersByTimeAsync(3000); // attempt 4 (delay capped at max=3000, not base*2^2=4000)
    await vi.waitFor(() => expect(fake.calls).toHaveLength(4));
    handle.close();
  });

  it("retries when the fetch itself rejects (network error), not just a bad response", async () => {
    const fake = fakeStreamingFetch();
    fake.failNextFetchWith(new TypeError("Failed to fetch"));
    const onError = vi.fn();
    const connect = createFetchSSEConnect({
      fetchFn: fake.fetchFn,
      baseRetryDelayMs: 500,
      maxRetryDelayMs: 2000,
    });
    const handle = connect("/api/events", {}, { onOpen: vi.fn(), onFrame: vi.fn(), onError });
    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(500);
    await vi.waitFor(() => expect(fake.calls).toHaveLength(2));
    handle.close();
  });

  it("retries when the stream ends unexpectedly (server closed it)", async () => {
    const fake = fakeStreamingFetch();
    const onError = vi.fn();
    const connect = createFetchSSEConnect({
      fetchFn: fake.fetchFn,
      baseRetryDelayMs: 500,
      maxRetryDelayMs: 2000,
    });
    const handle = connect("/api/events", {}, { onOpen: vi.fn(), onFrame: vi.fn(), onError });
    await vi.waitFor(() => expect(fake.calls).toHaveLength(1));
    fake.end();
    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(500);
    await vi.waitFor(() => expect(fake.calls).toHaveLength(2));
    handle.close();
  });

  it("sends Last-Event-ID on the reconnect after an id-bearing frame", async () => {
    const fake = fakeStreamingFetch();
    const connect = createFetchSSEConnect({
      fetchFn: fake.fetchFn,
      baseRetryDelayMs: 500,
      maxRetryDelayMs: 2000,
    });
    const handle = connect("/api/events", {}, { onOpen: vi.fn(), onFrame: vi.fn(), onError: vi.fn() });
    await vi.waitFor(() => expect(fake.calls).toHaveLength(1));
    fake.push("event: heartbeat\nid: 7\ndata: {}\n\n");
    fake.end();
    await vi.advanceTimersByTimeAsync(500);
    await vi.waitFor(() => expect(fake.calls).toHaveLength(2));
    const secondHeaders = fake.calls[1].init.headers as Record<string, string>;
    expect(secondHeaders["Last-Event-ID"]).toBe("7");
    handle.close();
  });

  it("close() stops further retries and does not call onError for the abort itself", async () => {
    const fake = fakeStreamingFetch();
    const onError = vi.fn();
    const connect = createFetchSSEConnect({
      fetchFn: fake.fetchFn,
      baseRetryDelayMs: 500,
      maxRetryDelayMs: 2000,
    });
    const handle = connect("/api/events", {}, { onOpen: vi.fn(), onFrame: vi.fn(), onError });
    await vi.waitFor(() => expect(fake.calls).toHaveLength(1));
    handle.close();
    fake.end();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fake.calls).toHaveLength(1); // never retried after close()
  });

  it("close() is idempotent", () => {
    const fake = fakeStreamingFetch();
    const connect = createFetchSSEConnect({ fetchFn: fake.fetchFn });
    const handle = connect("/api/events", {}, { onOpen: vi.fn(), onFrame: vi.fn(), onError: vi.fn() });
    expect(() => {
      handle.close();
      handle.close();
    }).not.toThrow();
  });
});
