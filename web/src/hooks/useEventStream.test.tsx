import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEventStream } from "./useEventStream";
import { FakeEventSource } from "./fakeEventSource";
import {
  DISCONNECTED_AFTER_MS,
  HEARTBEAT_INTERVAL_S,
} from "../api/events";

import stateFixture from "../../../tests/fixtures/events/state.json";
import micFixture from "../../../tests/fixtures/events/mic.json";
import turnFixture from "../../../tests/fixtures/events/turn.json";
import transcriptFixture from "../../../tests/fixtures/events/transcript.json";
import replyFixture from "../../../tests/fixtures/events/reply.json";
import degradationFixture from "../../../tests/fixtures/events/degradation.json";
import featuresFixture from "../../../tests/fixtures/events/features.json";
import clientsFixture from "../../../tests/fixtures/events/clients.json";
import heartbeatFixture from "../../../tests/fixtures/events/heartbeat.json";

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

  it("applies a state event fixture", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("state", stateFixture.data);
    });
    expect(result.current.state?.data).toEqual(stateFixture.data);
  });

  it("applies a mic event fixture", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("mic", micFixture.data);
    });
    expect(result.current.mic?.data).toEqual(micFixture.data);
  });

  it("applies a turn event fixture, including its degradation codes", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("turn", turnFixture.data);
    });
    expect(result.current.turn?.data).toEqual(turnFixture.data);
  });

  it("appends transcript and reply events to one speech log, in arrival order", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("transcript", transcriptFixture.data);
      source.emit("reply", replyFixture.data);
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
      source.emit("degradation", degradationFixture.data);
    });
    expect(result.current.degradations.map((e) => e.data)).toEqual([degradationFixture.data]);
  });

  it("applies a features event fixture (the waveform's own source)", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("features", featuresFixture.data);
    });
    expect(result.current.features?.data).toEqual(featuresFixture.data);
  });

  it("applies a clients event fixture (the remote-viewer indicator's source)", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("clients", clientsFixture.data);
    });
    expect(result.current.clients?.data).toEqual(clientsFixture.data);
  });

  it("records a heartbeat and stays connected while within the missed-heartbeat window", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      source.emit("heartbeat", heartbeatFixture.data);
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
      source.emit("heartbeat", heartbeatFixture.data);
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
      source.emit("heartbeat", heartbeatFixture.data);
      vi.advanceTimersByTime(DISCONNECTED_AFTER_MS + 1000);
    });
    expect(result.current.status).toBe("disconnected");
    act(() => {
      source.emit("heartbeat", heartbeatFixture.data);
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

  it("drops a frame whose data is not valid JSON rather than throwing", () => {
    const { result, source } = setUp();
    expect(() =>
      act(() => {
        source.open();
        source.emitRaw("state", "{not json");
      }),
    ).not.toThrow();
    expect(result.current.state).toBeNull();
  });

  it("ignores an envelope for a kind it never registered a listener for", () => {
    const { result, source } = setUp();
    act(() => {
      source.open();
      // "unknown-kind" was never wired via addEventListener, so emit() is a
      // silent no-op (mirrors the real EventSource: no wildcard listener).
      source.emit("unknown-kind", { x: 1 });
    });
    expect(result.current.state).toBeNull();
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
        source.emit("transcript", { role: "user", text: `line ${i}` });
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
        source.emit("degradation", { source: "test", code: `code-${i}`, reason: "r" });
      }
    });
    expect(result.current.degradations.length).toBeLessThan(5000);
    expect(result.current.degradations.at(-1)?.data.code).toBe("code-4999");
  });
});
