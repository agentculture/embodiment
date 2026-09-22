import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEventStream } from "./useEventStream";
import { FakeEventSource } from "./fakeEventSource";
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

const FIXTURES_DIR = join(__dirname, "../../../tests/fixtures/events");

/** Build a synthetic-but-wire-shaped envelope for tests that need arbitrary
 *  data (bounded-log stress tests) rather than a committed fixture. */
function envelope(kind: string, data: Record<string, unknown>, seq = 1) {
  return { v: 1, kind, ts: "2026-09-22T12:00:00.000Z", seq, source: "app://embodiment", data };
}

function setUp() {
  FakeEventSource.reset();
  const { result, unmount } = renderHook(() =>
    useEventStream("/api/events", {
      eventSourceFactory: (url: string) => new FakeEventSource(url) as unknown as EventSource,
    }),
  );
  const source = FakeEventSource.latest();
  return { result, unmount, source };
}

function setUpWithReconnectKey(initialKey: number) {
  FakeEventSource.reset();
  const { result, rerender, unmount } = renderHook(
    ({ reconnectKey }: { reconnectKey: number }) =>
      useEventStream("/api/events", {
        eventSourceFactory: (url: string) => new FakeEventSource(url) as unknown as EventSource,
        reconnectKey,
      }),
    { initialProps: { reconnectKey: initialKey } },
  );
  const source = FakeEventSource.latest();
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

  it("closes the underlying EventSource on unmount", () => {
    const { unmount, source } = setUp();
    act(() => source.open());
    unmount();
    expect(source.closed).toBe(true);
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
      expect(FakeEventSource.instances).toHaveLength(1);
      rerender({ reconnectKey: 1 });
      expect(FakeEventSource.instances).toHaveLength(2);
    });

    it("closes the previous EventSource when reconnecting", () => {
      const { rerender } = setUpWithReconnectKey(0);
      const first = FakeEventSource.latest();
      rerender({ reconnectKey: 1 });
      expect(first.closed).toBe(true);
    });

    it("does NOT reconnect merely on re-render when reconnectKey is unchanged", () => {
      const { rerender } = setUpWithReconnectKey(0);
      rerender({ reconnectKey: 0 });
      rerender({ reconnectKey: 0 });
      expect(FakeEventSource.instances).toHaveLength(1);
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
      const secondSource = FakeEventSource.latest();
      act(() => secondSource.open());
      expect(result.current.status).toBe("connected");
    });
  });
});
