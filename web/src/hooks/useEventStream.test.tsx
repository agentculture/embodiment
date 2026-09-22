import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEventStream } from "./useEventStream";
import { FakeSSEConnection, fakeConnect } from "./fakeSSEConnection";
import { DISCONNECTED_AFTER_MS, EVENT_KINDS, HEARTBEAT_INTERVAL_S } from "../api/events";

import stateFixture from "../../../tests/fixtures/events/state.json";
import micFixture from "../../../tests/fixtures/events/mic.json";
import turnFixture from "../../../tests/fixtures/events/turn.json";
import transcriptFixture from "../../../tests/fixtures/events/transcript.json";
import replyFixture from "../../../tests/fixtures/events/reply.json";
import degradationFixture from "../../../tests/fixtures/events/degradation.json";
import featuresFixture from "../../../tests/fixtures/events/features.json";
import clientsFixture from "../../../tests/fixtures/events/clients.json";
import heartbeatFixture from "../../../tests/fixtures/events/heartbeat.json";

// Round 5: reconstructed from reading embodiment/daemon/app.py directly
// (see src/fixtures/daemon/README.md for full provenance -- not a literal
// curl capture, since that would require opening the live daemon's
// install-secret file, forbidden by the task-agent preamble).
import statusEarAttachedFixture from "../fixtures/daemon/status-ear-attached.json";
import statusEarDetachedFixture from "../fixtures/daemon/status-ear-detached.json";

const FIXTURES_DIR = join(__dirname, "../../../tests/fixtures/events");
const TEST_SECRET = "s3cr3t-test";

/** Build a synthetic-but-wire-shaped envelope for tests that need arbitrary
 *  data (bounded-log stress tests) rather than a committed fixture. */
function envelope(kind: string, data: Record<string, unknown>, seq = 1) {
  return { v: 1, kind, ts: "2026-09-22T12:00:00.000Z", seq, source: "app://embodiment", data };
}

/** A no-op GET /api/status stub -- every existing test that doesn't care
 *  about round 5's status-seeding gets one by default, so `source.open()`
 *  never issues (or waits on) a real network call. `daemon: null` makes
 *  `seedFromStatus` skip every dispatch (falsy `response.daemon` short-
 *  circuits before touching `.ear`/`.clients`/`.recall`), so it is a true
 *  no-op, not merely "resolves to something".
 */
const NOOP_STATUS_FN = async () => ({ daemon: null }) as unknown as Awaited<
  ReturnType<typeof import("../api/control").fetchStatus>
>;

function setUp() {
  FakeSSEConnection.reset();
  const { result, unmount } = renderHook(() =>
    useEventStream("/api/events", TEST_SECRET, { connect: fakeConnect, fetchStatusFn: NOOP_STATUS_FN }),
  );
  const source = FakeSSEConnection.latest();
  return { result, unmount, source };
}

function setUpWithReconnectKey(initialKey: number) {
  FakeSSEConnection.reset();
  const { result, rerender, unmount } = renderHook(
    ({ reconnectKey }: { reconnectKey: number }) =>
      useEventStream("/api/events", TEST_SECRET, {
        connect: fakeConnect,
        reconnectKey,
        fetchStatusFn: NOOP_STATUS_FN,
      }),
    { initialProps: { reconnectKey: initialKey } },
  );
  const source = FakeSSEConnection.latest();
  return { result, rerender, unmount, source };
}

describe("useEventStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts in the connecting state before anything arrives", () => {
    const { result } = setUp();
    expect(result.current.status).toBe("connecting");
  });

  it("mirrors HEARTBEAT_INTERVAL_S from embodiment/bus.py", () => {
    expect(HEARTBEAT_INTERVAL_S).toBe(15.0);
    expect(DISCONNECTED_AFTER_MS).toBe(30_000);
  });

  it("becomes connected once the EventSource opens", () => {
    const { result, source } = setUp();
    act(() => source.open());
    expect(result.current.status).toBe("connected");
  });

  // Round 2: every fixture is emitted as the WHOLE envelope, exactly as the
  // wire carries it ({v, kind, ts, seq, source, data}) — never
  // `fixture.data` alone. Emitting only the inner object is what let the
  // round-1 bug (the hook mis-reading the outer envelope as if it were
  // `data`) slip past every test.

  it("applies a state event fixture and exposes the envelope's data, not the envelope itself", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("state", stateFixture);
    });
    expect(result.current.state?.data).toEqual(stateFixture.data);
    expect(result.current.state?.v).toBe(1);
    expect(result.current.state?.seq).toBe(stateFixture.seq);
  });

  it("applies a mic event fixture", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("mic", micFixture);
    });
    expect(result.current.mic?.data).toEqual(micFixture.data);
  });

  it("applies a turn event fixture, including its degradation codes", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("turn", turnFixture);
    });
    expect(result.current.turn?.data).toEqual(turnFixture.data);
  });

  it("appends transcript and reply events to one speech log, in arrival order", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("transcript", transcriptFixture);
      source.emit("reply", replyFixture);
    });
    expect(result.current.transcript.map((e) => e.data)).toEqual([
      transcriptFixture.data,
      replyFixture.data,
    ]);
  });

  it("appends degradation events to a bounded log", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("degradation", degradationFixture);
    });
    expect(result.current.degradations.map((e) => e.data)).toEqual([degradationFixture.data]);
  });

  it("applies a features event fixture (the waveform's own source)", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("features", featuresFixture);
    });
    expect(result.current.features?.data).toEqual(featuresFixture.data);
  });

  it("applies a clients event fixture (the remote-viewer indicator's source)", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("clients", clientsFixture);
    });
    expect(result.current.clients?.data).toEqual(clientsFixture.data);
  });

  it("records a heartbeat and stays connected while within the missed-heartbeat window", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("heartbeat", heartbeatFixture);
    });
    expect(result.current.status).toBe("connected");
    act(() => {
      vi.advanceTimersByTime(DISCONNECTED_AFTER_MS - 1000);
    });
    expect(result.current.status).toBe("connected");
  });

  it("goes disconnected once the heartbeat has been missing for 2 intervals", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("heartbeat", heartbeatFixture);
    });
    expect(result.current.status).toBe("connected");
    act(() => {
      vi.advanceTimersByTime(DISCONNECTED_AFTER_MS + 1000);
    });
    expect(result.current.status).toBe("disconnected");
  });

  it("recovers to connected once a new heartbeat arrives after disconnecting", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("heartbeat", heartbeatFixture);
      vi.advanceTimersByTime(DISCONNECTED_AFTER_MS + 1000);
    });
    expect(result.current.status).toBe("disconnected");
    act(() => {
      source.emit("heartbeat", heartbeatFixture);
    });
    expect(result.current.status).toBe("connected");
  });

  it("goes disconnected on error() when readyState reports CLOSED", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.error();
    });
    expect(result.current.status).toBe("disconnected");
  });

  it("drops a frame whose data is not valid JSON rather than throwing, and counts it", () => {
    const { result, source } = setUp();
    expect(() =>
      act(() => {
        source.open();
        source.emitRaw("state", "{not json");
      }),
    ).not.toThrow();
    expect(result.current.state).toBeNull();
    expect(result.current.droppedFrames).toBe(1);
  });

  it("drops a frame whose body is the inner `data` object, not the whole envelope, and counts it (round 1's exact bug)", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      // This is what round 1 accepted and mis-rendered: no `v`, no `data`
      // wrapper -- just the fixture's inner object straight on the wire.
      source.emit("state", stateFixture.data);
    });
    expect(result.current.state).toBeNull();
    expect(result.current.droppedFrames).toBe(1);
  });

  it("drops a frame with the wrong schema version and counts it", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("state", { ...stateFixture, v: 2 });
    });
    expect(result.current.state).toBeNull();
    expect(result.current.droppedFrames).toBe(1);
  });

  it("ignores an envelope for a kind it never registered a listener for", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      // "unknown-kind" was never wired via addEventListener, so emit() is a
      // silent no-op (mirrors the real EventSource: no wildcard listener).
      source.emit("unknown-kind", envelope("unknown-kind", { x: 1 }));
    });
    expect(result.current.state).toBeNull();
    expect(result.current.droppedFrames).toBe(0);
  });

  it("closes the underlying connection on unmount", () => {
    const { unmount, source } = setUp();
    act(() => source.open());
    unmount();
    expect(source.closed).toBe(true);
  });

  // Round 4 item #1: the credential now travels ONLY as
  // Authorization: Bearer <secret> on the stream request -- never a cookie
  // (see api/secret.ts's module docstring for why the cookie path was
  // removed: t16's guard refuses a cookie credential off-loopback/off-https,
  // which the live probe hit over Tailscale).
  it("carries the secret as an Authorization: Bearer header on the connection", () => {
    const { source } = setUp();
    expect(source.headers.Authorization).toBe(`Bearer ${TEST_SECRET}`);
  });

  it("never writes document.cookie for the stream credential", () => {
    const before = document.cookie;
    setUp();
    expect(document.cookie).toBe(before);
    expect(document.cookie).not.toContain("embodiment_secret");
  });

  it("bounds the transcript log so an unbounded stream cannot grow memory forever", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      for (let i = 0; i < 5000; i += 1) {
        source.emit("transcript", envelope("transcript", { role: "user", text: `line ${i}` }, i));
      }
    });
    expect(result.current.transcript.length).toBeLessThan(5000);
    expect(result.current.transcript.length).toBeGreaterThan(0);
    // the most recent line must survive, never silently dropped in favour
    // of stale ones
    expect(result.current.transcript.at(-1)?.data.text).toBe("line 4999");
  });

  it("bounds the degradation log the same way", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      for (let i = 0; i < 5000; i += 1) {
        source.emit(
          "degradation",
          envelope("degradation", { source: "test", code: `code-${i}`, reason: "r" }, i),
        );
      }
    });
    expect(result.current.degradations.length).toBeLessThan(5000);
    expect(result.current.degradations.at(-1)?.data.code).toBe("code-4999");
  });

  // Brief's round-2 item #1: feed each of the 9 fixture FILES' raw text
  // (exactly the bytes on disk, not a re-serialized JS object) through
  // emitRaw, proving the hook parses the actual wire bytes correctly.
  describe("every one of the 9 committed fixture files, fed as raw file text", () => {
    for (const kind of EVENT_KINDS) {
      it(`parses tests/fixtures/events/${kind}.json's raw file text correctly`, () => {
        const raw = readFileSync(join(FIXTURES_DIR, `${kind}.json`), "utf8");
        const fixture = JSON.parse(raw) as { data: Record<string, unknown> };
        const { result, source } = setUp();
        act(() => {
          source.open();
          source.emitRaw(kind, raw);
        });

        const field = (result.current as unknown as Record<string, unknown>)[kind] as
          | { data: Record<string, unknown> }
          | undefined;

        if (kind === "transcript" || kind === "reply") {
          expect(result.current.transcript).toHaveLength(1);
          expect(result.current.transcript[0]?.data).toEqual(fixture.data);
        } else if (kind === "degradation") {
          expect(result.current.degradations).toHaveLength(1);
          expect(result.current.degradations[0]?.data).toEqual(fixture.data);
        } else if (kind === "heartbeat") {
          expect(result.current.lastHeartbeatAtMs).not.toBeNull();
        } else {
          expect(field?.data).toEqual(fixture.data);
        }
        expect(result.current.droppedFrames).toBe(0);
      });
    }
  });

  // Round 3 item #1: t16's guard refuses GET /api/events until the
  // embodiment_secret cookie is set; the app sets that cookie only after
  // the operator submits the secret, which happens strictly after this
  // hook's own connect effect has already fired once. EventSource has no
  // "reconnect now" method, so a `reconnectKey` option lets the caller force
  // a close+reopen without touching `url`.
  describe("reconnectKey — forcing a reconnect after the secret cookie is set", () => {
    it("opens a fresh EventSource when reconnectKey changes", () => {
      const { rerender } = setUpWithReconnectKey(0);
      expect(FakeSSEConnection.instances).toHaveLength(1);
      rerender({ reconnectKey: 1 });
      expect(FakeSSEConnection.instances).toHaveLength(2);
    });

    it("closes the previous EventSource when reconnecting", () => {
      const { rerender } = setUpWithReconnectKey(0);
      const first = FakeSSEConnection.latest();
      rerender({ reconnectKey: 1 });
      expect(first.closed).toBe(true);
    });

    it("does NOT reconnect merely on re-render when reconnectKey is unchanged", () => {
      const { rerender } = setUpWithReconnectKey(0);
      rerender({ reconnectKey: 0 });
      rerender({ reconnectKey: 0 });
      expect(FakeSSEConnection.instances).toHaveLength(1);
    });

    it("resets status to connecting on the new connection until it opens", () => {
      const { result, rerender, source } = setUpWithReconnectKey(0);
      act(() => source.open());
      expect(result.current.status).toBe("connected");
      rerender({ reconnectKey: 1 });
      expect(result.current.status).toBe("connecting");
    });
  });

  // Round 3 item #1: distinguish "never got to open at all" (very likely the
  // guard refusing the secret -- REFUSED_SECRET_CODE / status 401) from
  // "was connected, then the connection dropped" (a real disconnect). The
  // browser's EventSource never exposes an HTTP status code to JS, so this
  // is the best signal available: an error before the FIRST successful open.
  describe("unauthorized vs disconnected (round 3 item #1)", () => {
    it("reports 'unauthorized' when the connection errors before ever opening", () => {
      const { result, source } = setUp();
      act(() => source.error());
      expect(result.current.status).toBe("unauthorized");
    });

    it("reports 'disconnected', not 'unauthorized', when the connection opened first and then errored", () => {
      const { result, source } = setUp();
      act(() => {
        source.open();
        source.error();
      });
      expect(result.current.status).toBe("disconnected");
    });

    it("reports 'disconnected', not 'unauthorized', once the heartbeat stops after a successful open", () => {
      const { result, source } = setUp();
      act(() => {
        source.open();
        source.emit("heartbeat", heartbeatFixture);
        vi.advanceTimersByTime(DISCONNECTED_AFTER_MS + 1000);
      });
      expect(result.current.status).toBe("disconnected");
    });

    it("clears 'unauthorized' once a reconnect actually opens", () => {
      const { result, rerender, source: firstSource } = setUpWithReconnectKey(0);
      act(() => firstSource.error());
      expect(result.current.status).toBe("unauthorized");
      rerender({ reconnectKey: 1 });
      const secondSource = FakeSSEConnection.latest();
      act(() => secondSource.open());
      expect(result.current.status).toBe("connected");
    });
  });

  // Round 5 [LIVE, MAJOR]: a viewer connecting after the ear was already
  // attached/muted/etc received NO snapshot over SSE, only future changes
  // -- mic/clients/recall stayed "unknown" forever. GET /api/status is
  // fetched on every successful open and reseeds them from the daemon's
  // REAL status()['ear']/['clients']/['recall'] shape (read directly from
  // /home/spark/git/.worktrees.embodiment/realtime-t15/embodiment/daemon/
  // app.py, cross-checked against that file's own test assertions).
  describe("GET /api/status seeding on connect (round 5)", () => {
    function daemonStatus(overrides: {
      ear?: { active: string | null; muted: boolean };
      clients?: { count: number; remote: number };
      recall?: { mode: string | null; semantic?: boolean };
    }) {
      // Cast: a real status() body always has ear/clients/recall present
      // (embodiment/daemon/app.py's `_status`), but these tests also want
      // to exercise "the field is missing/null" defensively -- hence the
      // loosened test-only shape rather than widening the production type.
      return {
        daemon: {
          ear: overrides.ear ?? null,
          clients: overrides.clients ?? null,
          recall: overrides.recall ? { semantic: false, ...overrides.recall } : null,
        },
        http: {},
      } as unknown as Awaited<ReturnType<typeof import("../api/control").fetchStatus>>;
    }

    function setUpWithStatus(status: ReturnType<typeof daemonStatus>) {
      FakeSSEConnection.reset();
      const fetchStatusFn = vi.fn(async () => status);
      const { result, unmount } = renderHook(() =>
        useEventStream("/api/events", TEST_SECRET, { connect: fakeConnect, fetchStatusFn }),
      );
      const source = FakeSSEConnection.latest();
      return { result, unmount, source, fetchStatusFn };
    }

    it("calls fetchStatusFn with the current secret exactly once per successful open", async () => {
      const { source, fetchStatusFn } = setUpWithStatus(daemonStatus({}));
      await act(async () => {
        source.open();
      });
      expect(fetchStatusFn).toHaveBeenCalledTimes(1);
      expect(fetchStatusFn).toHaveBeenCalledWith(TEST_SECRET);
    });

    // Against the reconstructed real fixture files (src/fixtures/daemon/),
    // not a synthetic shape built by this test file's own daemonStatus()
    // helper -- proves the hook reads the actual GET /api/status envelope
    // shape (`{"daemon": {...}, "http": {...}}`), not merely a JS object
    // this test file was free to invent to match its own code.
    it("seeds correctly from the real status-ear-attached.json fixture", async () => {
      FakeSSEConnection.reset();
      const fetchStatusFn = vi.fn(async () => statusEarAttachedFixture as unknown as Awaited<ReturnType<typeof import("../api/control").fetchStatus>>);
      const { result } = renderHook(() =>
        useEventStream("/api/events", TEST_SECRET, { connect: fakeConnect, fetchStatusFn }),
      );
      await act(async () => {
        FakeSSEConnection.latest().open();
      });
      expect(result.current.mic?.data).toEqual({ hot: true, ear: "host" });
      expect(result.current.clients?.data).toEqual({ count: 1, remote: 1 });
      expect(result.current.recallStatusMode).toBe("lexical");
    });

    it("seeds correctly from the real status-ear-detached.json fixture", async () => {
      FakeSSEConnection.reset();
      const fetchStatusFn = vi.fn(async () => statusEarDetachedFixture as unknown as Awaited<ReturnType<typeof import("../api/control").fetchStatus>>);
      const { result } = renderHook(() =>
        useEventStream("/api/events", TEST_SECRET, { connect: fakeConnect, fetchStatusFn }),
      );
      await act(async () => {
        FakeSSEConnection.latest().open();
      });
      expect(result.current.mic?.data).toEqual({ hot: false, ear: null });
      expect(result.current.clients?.data).toEqual({ count: 0, remote: 0 });
      expect(result.current.recallStatusMode).toBeNull();
    });

    it("seeds mic as hot=true, ear=<name> when an ear is attached and unmuted", async () => {
      const { result, source } = setUpWithStatus(daemonStatus({ ear: { active: "host", muted: false } }));
      await act(async () => {
        source.open();
      });
      expect(result.current.mic?.data).toEqual({ hot: true, ear: "host" });
    });

    it("seeds mic as hot=false, ear=null when no ear is attached", async () => {
      const { result, source } = setUpWithStatus(daemonStatus({ ear: { active: null, muted: false } }));
      await act(async () => {
        source.open();
      });
      expect(result.current.mic?.data).toEqual({ hot: false, ear: null });
    });

    it("seeds mic as hot=false when an ear is attached but muted", async () => {
      const { result, source } = setUpWithStatus(daemonStatus({ ear: { active: "host", muted: true } }));
      await act(async () => {
        source.open();
      });
      expect(result.current.mic?.data).toEqual({ hot: false, ear: "host" });
    });

    it("treats the STRING ear name \"null\" as attached, not as JS null (the daemon's own null-endpoint name)", async () => {
      const { result, source } = setUpWithStatus(daemonStatus({ ear: { active: "null", muted: false } }));
      await act(async () => {
        source.open();
      });
      expect(result.current.mic?.data).toEqual({ hot: true, ear: "null" });
    });

    it("seeds clients from status()['clients']", async () => {
      const { result, source } = setUpWithStatus(daemonStatus({ clients: { count: 3, remote: 1 } }));
      await act(async () => {
        source.open();
      });
      expect(result.current.clients?.data).toEqual({ count: 3, remote: 1 });
    });

    it("seeds recallStatusMode from status()['recall']['mode']", async () => {
      const { result, source } = setUpWithStatus(daemonStatus({ recall: { mode: "lexical" } }));
      await act(async () => {
        source.open();
      });
      expect(result.current.recallStatusMode).toBe("lexical");
    });

    it("leaves recallStatusMode null when the daemon has never completed a recall call", async () => {
      const { result, source } = setUpWithStatus(daemonStatus({ recall: { mode: null } }));
      await act(async () => {
        source.open();
      });
      expect(result.current.recallStatusMode).toBeNull();
    });

    it("does not throw and leaves state unchanged when GET /api/status rejects", async () => {
      FakeSSEConnection.reset();
      const fetchStatusFn = vi.fn(async () => {
        throw new Error("network error");
      });
      const { result, unmount } = renderHook(() =>
        useEventStream("/api/events", TEST_SECRET, { connect: fakeConnect, fetchStatusFn }),
      );
      const source = FakeSSEConnection.latest();
      await expect(
        act(async () => {
          source.open();
        }),
      ).resolves.not.toThrow();
      expect(result.current.mic).toBeNull();
      expect(result.current.status).toBe("connected"); // the STREAM itself is fine
      unmount();
    });

    it("re-seeds on every reconnect, not just the first connect", async () => {
      FakeSSEConnection.reset();
      const fetchStatusFn = vi
        .fn()
        .mockResolvedValueOnce(daemonStatus({ ear: { active: "host", muted: false } }))
        .mockResolvedValueOnce(daemonStatus({ ear: { active: null, muted: false } }));
      const { result, rerender } = renderHook(
        ({ reconnectKey }: { reconnectKey: number }) =>
          useEventStream("/api/events", TEST_SECRET, {
            connect: fakeConnect,
            fetchStatusFn,
            reconnectKey,
          }),
        { initialProps: { reconnectKey: 0 } },
      );
      await act(async () => {
        FakeSSEConnection.latest().open();
      });
      expect(result.current.mic?.data.ear).toBe("host");

      rerender({ reconnectKey: 1 });
      await act(async () => {
        FakeSSEConnection.latest().open();
      });
      expect(result.current.mic?.data.ear).toBeNull();
      expect(fetchStatusFn).toHaveBeenCalledTimes(2);
    });

    // Brief's explicit ask: refreshStatus() re-fetches and reseeds on
    // demand -- App.tsx calls this after a control POST resolves 200.
    it("refreshStatus() re-fetches and reseeds without requiring a reconnect", async () => {
      FakeSSEConnection.reset();
      const fetchStatusFn = vi
        .fn()
        .mockResolvedValueOnce(daemonStatus({ ear: { active: null, muted: false } }))
        .mockResolvedValueOnce(daemonStatus({ ear: { active: "browser", muted: false } }));
      const { result } = renderHook(() =>
        useEventStream("/api/events", TEST_SECRET, { connect: fakeConnect, fetchStatusFn }),
      );
      await act(async () => {
        FakeSSEConnection.latest().open();
      });
      expect(result.current.mic?.data.ear).toBeNull();

      await act(async () => {
        await result.current.refreshStatus();
      });
      expect(result.current.mic?.data.ear).toBe("browser");
      expect(fetchStatusFn).toHaveBeenCalledTimes(2);
      // still the SAME underlying connection -- refreshStatus never
      // reconnects, it only re-fetches the status snapshot.
      expect(FakeSSEConnection.instances).toHaveLength(1);
    });

    it("refreshStatus() never throws even when GET /api/status rejects", async () => {
      FakeSSEConnection.reset();
      const fetchStatusFn = vi.fn(async () => {
        throw new Error("network error");
      });
      const { result } = renderHook(() =>
        useEventStream("/api/events", TEST_SECRET, { connect: fakeConnect, fetchStatusFn }),
      );
      await act(async () => {
        FakeSSEConnection.latest().open();
      });
      await expect(result.current.refreshStatus()).resolves.toBeUndefined();
    });
  });
});
