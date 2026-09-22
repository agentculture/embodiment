// audio/browserEar.test.ts
//
// Proves this task's thin-wrapper contract against `embodiment/audio/
// remote.py`'s own documented wire: auth is sent FIRST and nothing else is
// sent before the socket is open, `response.audio.delta` reaches the
// player, and the AnalyserNode-backed listener source exists only once the
// mic is actually capturing (t18 instruction: "AnalyserNode only when the
// browser is the ear").

import { describe, expect, it, vi } from "vitest";
import {
  AUTH_MESSAGE_TYPE,
  BrowserEar,
  type AnalyserLike,
  type BrowserEarDeps,
  type SocketLike,
} from "./browserEar";
import { AUDIO_DELTA_EVENT_TYPE, buildAppendEvent } from "./lobes/pcm-wire";

/** `WebSocket.OPEN`/`CLOSED` numeric ready-states, mirrored here rather than
 *  imported (this test double is not a real `WebSocket`). */
const READY_STATE_OPEN = 1;
const READY_STATE_CLOSED = 3;

function fakeSocket(): SocketLike & {
  sent: string[];
  open(): void;
  forceClosed(): void;
} {
  const sent: string[] = [];
  let readyState = 0; // CONNECTING, like a real WebSocket before onopen.
  const socket: SocketLike & { sent: string[]; open(): void; forceClosed(): void } = {
    get readyState() {
      return readyState;
    },
    sent,
    send(data: string) {
      sent.push(data);
    },
    close() {},
    onopen: null,
    onclose: null,
    onerror: null,
    onmessage: null,
    open() {
      readyState = READY_STATE_OPEN;
      socket.onopen?.();
    },
    /** Round 3, finding 2: simulate the socket closing out from under an
     *  otherwise-"live" `BrowserEar` -- a real race between the socket's
     *  own close and this module's `onclose` handler noticing it. */
    forceClosed() {
      readyState = READY_STATE_CLOSED;
    },
  };
  return socket;
}

function fakeAnalyser(fill: number): AnalyserLike {
  return {
    frequencyBinCount: 32,
    getByteTimeDomainData(out: Uint8Array) {
      out.fill(fill);
    },
    connect: () => undefined,
    disconnect: () => {},
  };
}

function fakeContext() {
  return {
    sampleRate: 24000,
    state: "running",
    currentTime: 0,
    destination: { connect: () => undefined, disconnect: () => {} },
    audioWorklet: { addModule: async () => {} },
    resume: async () => {},
    close: async () => {},
    createMediaStreamSource: () => ({ connect: () => undefined, disconnect: () => {} }),
    createBuffer: () => ({ duration: 0, getChannelData: () => new Float32Array(0) }),
    createBufferSource: () => ({
      buffer: null,
      onended: null,
      connect: () => undefined,
      disconnect: () => {},
      start: () => {},
      stop: () => {},
    }),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

function makeDeps(overrides: Partial<BrowserEarDeps> = {}): {
  deps: BrowserEarDeps;
  socket: ReturnType<typeof fakeSocket>;
  enqueued: string[];
} {
  const socket = fakeSocket();
  const enqueued: string[] = [];
  const deps: BrowserEarDeps = {
    createSocket: () => socket,
    createMicCapture: () => {
      throw new Error("not used in this test");
    },
    createPlayer: () =>
      ({
        enqueueDelta: (b64: string) => enqueued.push(b64),
      }) as unknown as ReturnType<BrowserEarDeps["createPlayer"]>,
    createAnalyser: () => fakeAnalyser(200),
    ...overrides,
  };
  return { deps, socket, enqueued };
}

describe("BrowserEar.connect", () => {
  it("sends the auth message FIRST, and it is the only message sent on open", () => {
    const { deps, socket } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s3cr3t" }, fakeContext(), deps);
    ear.connect();
    socket.open();
    expect(socket.sent).toHaveLength(1);
    const parsed = JSON.parse(socket.sent[0]);
    expect(parsed).toEqual({ type: AUTH_MESSAGE_TYPE, secret: "s3cr3t" });
  });

  it("never sends the raw secret anywhere except inside that one auth message's own field", () => {
    const { deps, socket } = makeDeps();
    const ear = new BrowserEar(
      { wsUrl: "ws://x", secret: "marker-secret-xyz" },
      fakeContext(),
      deps,
    );
    ear.connect();
    socket.open();
    // Exactly one occurrence: inside the auth payload's `secret` field.
    const occurrences = socket.sent.filter((s) => s.includes("marker-secret-xyz")).length;
    expect(occurrences).toBe(1);
  });

  it("drops (and counts) an append attempted before auth completes -- while connecting AND while authenticating -- then sends it once live", async () => {
    // Round 3, finding 3: the previous version of this test attempted no
    // append at all -- both its assertions held regardless of what
    // `sendAppend` did. This drives a REAL `MicCapture.onAppend` callback
    // (captured from `createMicCapture`, exactly how `startMic()` wires it)
    // at each stage, so a mutated `sendAppend` (sending too early, or
    // throwing) fails this test.
    const { deps, socket } = makeDeps();
    const captured: { onAppend: ((event: ReturnType<typeof buildAppendEvent>) => void) | null } = {
      onAppend: null,
    };
    (deps as { createMicCapture: BrowserEarDeps["createMicCapture"] }).createMicCapture = (
      _context,
      onAppend,
    ) => {
      captured.onAppend = onAppend;
      return { start: vi.fn(async () => true), stop: vi.fn() } as unknown as ReturnType<
        BrowserEarDeps["createMicCapture"]
      >;
    };
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);

    ear.connect();
    await ear.startMic();
    expect(captured.onAppend).not.toBeNull();

    // Still "connecting" -- the socket has not opened yet.
    captured.onAppend?.(buildAppendEvent(new Float32Array([0])));
    expect(ear.status().appendsSentBeforeAuthDropped).toBe(1);
    expect(socket.sent).toHaveLength(0);

    // The socket opens -> the auth frame is sent, state becomes
    // "authenticating" (round 3: NOT "live" yet -- see the dedicated test
    // below). An append attempted here is still dropped.
    socket.open();
    expect(ear.status().state).toBe("authenticating");
    expect(socket.sent).toHaveLength(1); // the auth frame, nothing else
    captured.onAppend?.(buildAppendEvent(new Float32Array([0])));
    expect(ear.status().appendsSentBeforeAuthDropped).toBe(2);
    expect(socket.sent).toHaveLength(1); // the append was NOT sent

    // The server confirms auth -> now live. An append now IS sent.
    socket.onmessage?.({ data: JSON.stringify({ type: "session.created" }) });
    expect(ear.status().state).toBe("live");
    captured.onAppend?.(buildAppendEvent(new Float32Array([0])));
    expect(socket.sent).toHaveLength(2);
    expect(ear.status().appendsSentBeforeAuthDropped).toBe(2); // unchanged
  });

  it("stays 'authenticating' once the auth frame is sent -- becomes 'live' only when session.created arrives, never merely on send", () => {
    const { deps, socket } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    socket.open();
    expect(ear.status().state).toBe("authenticating");
    socket.onmessage?.({ data: JSON.stringify({ type: "session.created" }) });
    expect(ear.status().state).toBe("live");
  });

  it("counts (and never throws on) an auth-send failure, marking the connection failed rather than stuck authenticating forever", () => {
    const { deps, socket } = makeDeps();
    socket.send = () => {
      throw new Error("socket closed between open and send");
    };
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    expect(() => socket.open()).not.toThrow();
    expect(ear.status().state).toBe("failed");
    expect(ear.status().authSendFailures).toBe(1);
  });

  it("routes response.audio.delta frames to the player", () => {
    const { deps, socket, enqueued } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    ear.startPlayback();
    socket.open();
    socket.onmessage?.({
      data: JSON.stringify({ type: AUDIO_DELTA_EVENT_TYPE, audio: "QUJD" }),
    });
    expect(enqueued).toEqual(["QUJD"]);
  });

  it("counts (never throws on) an unparseable inbound frame", () => {
    const { deps, socket } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    socket.open();
    expect(() => socket.onmessage?.({ data: "not json{{{" })).not.toThrow();
    expect(ear.status().unparseableFramesDropped).toBe(1);
  });

  it("ignores a frame of an unrecognized type without throwing or enqueueing", () => {
    const { deps, socket, enqueued } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    ear.startPlayback();
    socket.open();
    socket.onmessage?.({ data: JSON.stringify({ type: "some-other-event" }) });
    expect(enqueued).toEqual([]);
    expect(ear.status().state).toBe("authenticating"); // unrecognized -- did NOT flip to live
  });

  // -- Round 3, finding 2: sendAppend must also guard on socket.readyState --

  it("drops (and counts) an append when state reads 'live' but the socket has since closed (a race with onclose) -- never throws", async () => {
    const { deps, socket } = makeDeps();
    const captured: { onAppend: ((event: ReturnType<typeof buildAppendEvent>) => void) | null } = {
      onAppend: null,
    };
    (deps as { createMicCapture: BrowserEarDeps["createMicCapture"] }).createMicCapture = (
      _context,
      onAppend,
    ) => {
      captured.onAppend = onAppend;
      return { start: vi.fn(async () => true), stop: vi.fn() } as unknown as ReturnType<
        BrowserEarDeps["createMicCapture"]
      >;
    };
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    socket.open();
    socket.onmessage?.({ data: JSON.stringify({ type: "session.created" }) });
    expect(ear.status().state).toBe("live");
    await ear.startMic();

    socket.forceClosed(); // readyState now CLOSED, but `state` still reads "live"
    expect(() => captured.onAppend?.(buildAppendEvent(new Float32Array([0])))).not.toThrow();
    expect(ear.status().appendsDroppedSocketClosed).toBe(1);
    expect(socket.sent).toHaveLength(1); // only the auth frame -- the append never went out
  });

  it("counts (and never throws on) a send() that itself throws during a 'live' append", async () => {
    const { deps, socket } = makeDeps();
    const captured: { onAppend: ((event: ReturnType<typeof buildAppendEvent>) => void) | null } = {
      onAppend: null,
    };
    (deps as { createMicCapture: BrowserEarDeps["createMicCapture"] }).createMicCapture = (
      _context,
      onAppend,
    ) => {
      captured.onAppend = onAppend;
      return { start: vi.fn(async () => true), stop: vi.fn() } as unknown as ReturnType<
        BrowserEarDeps["createMicCapture"]
      >;
    };
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    socket.open();
    socket.onmessage?.({ data: JSON.stringify({ type: "session.created" }) });
    await ear.startMic();

    socket.send = () => {
      throw new Error("send after close");
    };
    expect(() => captured.onAppend?.(buildAppendEvent(new Float32Array([0])))).not.toThrow();
    expect(ear.status().appendsDroppedSocketClosed).toBe(1);
  });
});

describe("BrowserEar listener analyser source -- AnalyserNode only when the browser is the ear", () => {
  it("has no analyser source before the mic ever starts", () => {
    const { deps } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    expect(ear.listenerAnalyserSource()).toBeNull();
    expect(ear.status().analyserActive).toBe(false);
  });

  it("creates and exposes the analyser once mic capture starts successfully", async () => {
    const micCapture = { start: vi.fn(async () => true), stop: vi.fn() };
    const { deps } = makeDeps({
      createMicCapture: () => micCapture as unknown as ReturnType<BrowserEarDeps["createMicCapture"]>,
    });
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    const started = await ear.startMic();
    expect(started).toBe(true);
    expect(ear.status().analyserActive).toBe(true);
    const source = ear.listenerAnalyserSource();
    expect(source).not.toBeNull();
    const envelope = source?.readTrace();
    expect(envelope?.mins).toHaveLength(16);
    expect(envelope?.maxs).toHaveLength(16);
  });

  it("stays without an analyser when mic capture fails to start (e.g. permission denied)", async () => {
    const micCapture = { start: vi.fn(async () => false), stop: vi.fn() };
    const { deps } = makeDeps({
      createMicCapture: () => micCapture as unknown as ReturnType<BrowserEarDeps["createMicCapture"]>,
    });
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    const started = await ear.startMic();
    expect(started).toBe(false);
    expect(ear.listenerAnalyserSource()).toBeNull();
  });

  it("converts analyser bytes to the same min/max-per-bucket NormalizedEnvelope shape the bus envelope uses", () => {
    const { deps } = makeDeps({ createAnalyser: () => fakeAnalyser(255) }); // full-scale positive
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    const micCapture = { start: vi.fn(async () => true), stop: vi.fn() };
    (deps as { createMicCapture: unknown }).createMicCapture = () =>
      micCapture as unknown as ReturnType<BrowserEarDeps["createMicCapture"]>;
    return ear.startMic().then(() => {
      const envelope = ear.listenerAnalyserSource()?.readTrace();
      expect(envelope?.mins).toHaveLength(16);
      expect(envelope?.maxs).toHaveLength(16);
      envelope?.maxs.forEach((v) => {
        expect(v).toBeGreaterThanOrEqual(-1);
        expect(v).toBeLessThanOrEqual(1);
      });
      // Every analyser byte is 255 (constant full-scale positive), so the
      // bucket never sees a value below the initial min=0 -- the min line
      // stays 0 and the max line saturates near +1 (|255-128|/128, clamped).
      expect(envelope?.mins[0]).toBe(0);
      expect(envelope?.maxs[0]).toBeCloseTo(127 / 128, 5);
    });
  });

  it("removes the analyser on close (shutdown tears down everything it owns)", async () => {
    const disconnect = vi.fn();
    const { deps } = makeDeps({
      createAnalyser: () => ({ ...fakeAnalyser(200), disconnect }),
    });
    const micCapture = { start: vi.fn(async () => true), stop: vi.fn() };
    (deps as { createMicCapture: unknown }).createMicCapture = () =>
      micCapture as unknown as ReturnType<BrowserEarDeps["createMicCapture"]>;
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    await ear.startMic();
    ear.close();
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(ear.listenerAnalyserSource()).toBeNull();
  });

  it("round 3, finding 4: a second startMic() stops and disconnects the previous capture/analyser -- never orphans them", async () => {
    const stops = [vi.fn(), vi.fn()];
    const disconnects = [vi.fn(), vi.fn()];
    let micCalls = 0;
    let analyserCalls = 0;
    const { deps } = makeDeps({
      createMicCapture: () => {
        const stop = stops[micCalls];
        micCalls += 1;
        return { start: vi.fn(async () => true), stop } as unknown as ReturnType<
          BrowserEarDeps["createMicCapture"]
        >;
      },
      createAnalyser: () => {
        const disconnect = disconnects[analyserCalls];
        analyserCalls += 1;
        return { ...fakeAnalyser(200), disconnect };
      },
    });
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);

    await ear.startMic();
    expect(stops[0]).not.toHaveBeenCalled();
    expect(disconnects[0]).not.toHaveBeenCalled();
    expect(ear.status().micRestartCount).toBe(0);

    await ear.startMic();
    expect(stops[0]).toHaveBeenCalledTimes(1); // the FIRST capture was stopped
    expect(disconnects[0]).toHaveBeenCalledTimes(1); // the FIRST analyser was disconnected
    expect(stops[1]).not.toHaveBeenCalled(); // the new one is still running
    expect(ear.status().micRestartCount).toBe(1);
    expect(ear.status().analyserActive).toBe(true); // still an (new) analyser attached
  });
});

describe("BrowserEar.close", () => {
  it("never throws even when nothing was ever started", () => {
    const { deps } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    expect(() => ear.close()).not.toThrow();
  });

  it("is idempotent -- calling close twice never throws", () => {
    const { deps } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    ear.close();
    expect(() => ear.close()).not.toThrow();
  });

  it("never throws even if the underlying socket.close() itself throws", () => {
    const { deps, socket } = makeDeps();
    socket.close = () => {
      throw new Error("boom");
    };
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    expect(() => ear.close()).not.toThrow();
  });
});

describe("buildAppendEvent sanity (this wrapper relays exactly this shape)", () => {
  it("is the append event shape sendAppend forwards verbatim", () => {
    const event = buildAppendEvent(new Float32Array([0, 0.5, -0.5]));
    expect(event.type).toBe("input_audio_buffer.append");
    expect(typeof event.audio).toBe("string");
  });
});

describe("BrowserEar attack surface", () => {
  it("survives a secret containing NUL bytes, quotes, and a 10k-char length", () => {
    const nasty = 'a"b\u0000c'.repeat(2000);
    const { deps, socket } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: nasty }, fakeContext(), deps);
    expect(() => {
      ear.connect();
      socket.open();
    }).not.toThrow();
    const parsed = JSON.parse(socket.sent[0]);
    expect(parsed.secret).toBe(nasty);
  });

  it("survives 10000 inbound audio-delta frames without throwing or leaking unbounded state", () => {
    const { deps, socket, enqueued } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    ear.startPlayback();
    socket.open();
    for (let i = 0; i < 10000; i += 1) {
      socket.onmessage?.({ data: JSON.stringify({ type: AUDIO_DELTA_EVENT_TYPE, audio: "AA" }) });
    }
    expect(enqueued.length).toBe(10000);
    expect(ear.status().unparseableFramesDropped).toBe(0);
  });

  it("survives 10000 malformed inbound frames, counting every one without throwing", () => {
    const { deps, socket } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    ear.connect();
    socket.open();
    for (let i = 0; i < 10000; i += 1) {
      socket.onmessage?.({ data: "{{{not json" });
    }
    expect(ear.status().unparseableFramesDropped).toBe(10000);
  });

  it("never throws when close() runs before connect() ever ran", () => {
    const { deps } = makeDeps();
    const ear = new BrowserEar({ wsUrl: "ws://x", secret: "s" }, fakeContext(), deps);
    expect(() => ear.close()).not.toThrow();
  });
});
