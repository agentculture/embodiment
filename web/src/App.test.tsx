import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { type AppProps } from "./App";
import { FakeSSEConnection, fakeConnect } from "./hooks/fakeSSEConnection";
import { DISCONNECTED_AFTER_MS } from "./api/events";
import * as control from "./api/control";
import micEventAttachedFixture from "./fixtures/daemon/mic-event-attached.json";
import statusEarDetachedFixture from "./fixtures/daemon/status-ear-detached.json";

import stateFixture from "../../tests/fixtures/events/state.json";
import micFixture from "../../tests/fixtures/events/mic.json";
import turnFixture from "../../tests/fixtures/events/turn.json";
import transcriptFixture from "../../tests/fixtures/events/transcript.json";
import replyFixture from "../../tests/fixtures/events/reply.json";
import degradationFixture from "../../tests/fixtures/events/degradation.json";
import featuresFixture from "../../tests/fixtures/events/features.json";
import clientsFixture from "../../tests/fixtures/events/clients.json";
import heartbeatFixture from "../../tests/fixtures/events/heartbeat.json";

/** Build a synthetic-but-wire-shaped envelope, for attack tests that need
 *  arbitrary/malformed `data` rather than a committed fixture. Emitting
 *  only a bare `data`-shaped object (round 1's bug) would now be silently
 *  DROPPED by parseEnvelopeFrame, so an attack payload has to travel
 *  inside a real envelope to actually reach the components under test. */
function envelope(kind: string, data: Record<string, unknown>, seq = 1) {
  return { v: 1, kind, ts: "2026-09-22T12:00:00.000Z", seq, source: "app://embodiment", data };
}

/** A no-op GET /api/status stub for every test that isn't specifically
 *  about round 5's status seeding -- keeps `source.open()` from issuing
 *  (or waiting on) a real network call. */
const NOOP_STATUS_FN = async () => ({ daemon: null }) as unknown as Awaited<
  ReturnType<typeof import("./api/control").fetchStatus>
>;

function renderApp(waveformOptions?: AppProps["waveformOptions"]) {
  FakeSSEConnection.reset();
  render(
    <App
      eventStreamOptions={{ connect: fakeConnect, fetchStatusFn: NOOP_STATUS_FN }}
      waveformOptions={waveformOptions}
    />,
  );
  return FakeSSEConnection.latest();
}

/** Task t18: the oscilloscope's canvas draw loop, made observable under
 *  jsdom (which has no canvas backend at all -- see `Waveform.test.tsx`'s
 *  own copy of this pattern). `tick()` runs the single most recently
 *  scheduled animation-frame callback. */
function fakeWaveformSeam() {
  let pending: FrameRequestCallback | null = null;
  const ctx = {
    clearRect: () => {},
    beginPath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    closePath: () => {},
    stroke: () => {},
    fill: () => {},
    fillRect: () => {},
    setTransform: () => {},
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 0,
    globalAlpha: 1,
  };
  return {
    options: {
      contextFactory: () => ctx as never,
      raf: {
        request: (cb: FrameRequestCallback) => {
          pending = cb;
          return 1;
        },
        cancel: () => {
          pending = null;
        },
      },
    },
    tick: (nowMs: number) => {
      const cb = pending;
      pending = null;
      cb?.(nowMs);
    },
  };
}

describe("App — every state from the committed event fixtures", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    sessionStorage.clear();
  });

  it("renders the connecting state before the stream opens", () => {
    renderApp();
    expect(screen.getByRole("status")).toHaveAttribute("data-status", "connecting");
  });

  it("renders connected once the stream opens", () => {
    const source = renderApp();
    act(() => source.open());
    expect(screen.getByRole("status")).toHaveAttribute("data-status", "connected");
  });

  // Round 2: every fixture below is emitted as the WHOLE envelope object
  // (`stateFixture`, not `stateFixture.data`) — exactly the shape the wire
  // carries (`{v, kind, ts, seq, source, data}`). Emitting the inner
  // `data` object alone is what let round 1's bug (the hook conflating the
  // parsed frame with `data` itself) pass every test while rendering
  // nothing real: a probe server serving these exact fixture files verbatim
  // over SSE showed every pane empty against that version.

  it("renders the state.json fixture without affecting the voice-on/off control (round 5: state events never gate it)", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("state", stateFixture);
    });
    // Round 5 correction: the daemon never publishes a "voice-on" status
    // token (verified against embodiment/daemon/app.py's real _publish
    // calls) -- voiceOn comes from the `mic` event's `ear` field, not
    // `state`. No mic event has arrived here, so it's still "Start voice".
    expect(screen.getByRole("button", { name: "Start voice" })).toBeInTheDocument();
  });

  it("renders the mic.json fixture as the mute control AND the voice-on control (ear attached)", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("mic", micFixture);
    });
    // mic.json: hot: true, ear: "local" -> "Mute mic" AND "Stop voice"
    // (round 5: an attached ear means voice is on, regardless of `state`).
    expect(screen.getByRole("button", { name: "Mute mic" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop voice" })).toBeInTheDocument();
  });

  it("renders the reconstructed real mic-event-attached.json fixture the same way", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("mic", micEventAttachedFixture);
    });
    expect(screen.getByRole("button", { name: "Mute mic" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop voice" })).toBeInTheDocument();
  });

  it("treats a mic event with ear=null as voice-off, mic hot=false (round 5: the real daemon shape)", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("mic", envelope("mic", { hot: false, ear: null }));
    });
    expect(screen.getByRole("button", { name: "Start voice" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unmute mic" })).toBeInTheDocument();
  });

  it("renders the turn.json fixture's degradation codes without crashing", () => {
    const source = renderApp();
    expect(() =>
      act(() => {
        source.open();
        source.emit("turn", turnFixture);
      }),
    ).not.toThrow();
  });

  it("renders the transcript.json and reply.json fixtures in the transcript pane, RTL-safe", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("transcript", transcriptFixture);
      source.emit("reply", replyFixture);
    });
    const userLine = screen.getByText(new RegExp(transcriptFixture.data.text));
    const replyLine = screen.getByText(new RegExp(replyFixture.data.text));
    expect(userLine.closest("[dir]")).toHaveAttribute("dir", "auto");
    expect(replyLine.closest("[dir]")).toHaveAttribute("dir", "auto");
    expect(userLine.closest("[data-role]")).toHaveAttribute("data-role", "user");
    expect(replyLine.closest("[data-role]")).toHaveAttribute("data-role", "assistant");
  });

  it("renders the degradation.json fixture in the degradations pane", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("degradation", degradationFixture);
    });
    expect(screen.getByText(new RegExp(degradationFixture.data.code))).toBeInTheDocument();
  });

  it("shows the recall-mode indicator as unknown until a signal arrives, then lexical-fallback on a memory-sourced degradation", () => {
    const source = renderApp();
    // No positive "semantic" signal exists until GET /api/status seeds one
    // (round 5) -- the honest default is "unknown", never a claim of the
    // better state with no evidence for it (this rig's embedder is not
    // ready; CLAUDE.md's C3).
    expect(screen.getByText(/recall: unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/recall: semantic/)).toBeNull();
    act(() => {
      source.open();
      // Round 5: the real daemon folds recall/memory degradations with
      // source="memory" (embodiment/daemon/app.py: `self._fold("memory",
      // degradation)`), read directly -- NOT "continuity", which is
      // degradationFixture's own (unrelated) example value.
      source.emit("degradation", envelope("degradation", { source: "memory", code: "x", reason: "y" }));
    });
    expect(screen.getByText(/recall: lexical fallback/)).toBeInTheDocument();
  });

  // Round 6: "recall: unknown" is honest but unhelpful before the daemon's
  // first recall call -- status()["recall"]["configured_mode"] is always
  // present, so the indicator shows what recall WILL do in that gap.
  it("shows 'recall: keyword (configured)' when mode is null but a configured mode is seeded", async () => {
    const fetchStatusFn = vi.fn(async () => statusEarDetachedFixture as unknown as Awaited<
      ReturnType<typeof control.fetchStatus>
    >);
    FakeSSEConnection.reset();
    render(<App eventStreamOptions={{ connect: fakeConnect, fetchStatusFn }} />);
    const source = FakeSSEConnection.latest();
    await act(async () => {
      source.open();
    });
    expect(screen.getByText("recall: keyword (configured)")).toBeInTheDocument();
  });

  it("a live memory degradation still wins over the configured-mode fallback", async () => {
    const fetchStatusFn = vi.fn(async () => statusEarDetachedFixture as unknown as Awaited<
      ReturnType<typeof control.fetchStatus>
    >);
    FakeSSEConnection.reset();
    render(<App eventStreamOptions={{ connect: fakeConnect, fetchStatusFn }} />);
    const source = FakeSSEConnection.latest();
    await act(async () => {
      source.open();
      source.emit("degradation", envelope("degradation", { source: "memory", code: "x", reason: "y" }));
    });
    expect(screen.getByText(/recall: lexical fallback/)).toBeInTheDocument();
    expect(screen.queryByText(/configured/)).toBeNull();
  });

  it("renders the features.json fixture as a live waveform", () => {
    // featuresFixture.data.direction is "in" -- the LISTENER trace, not the
    // assistant one; each trace's liveness is tracked independently (t18).
    const seam = fakeWaveformSeam();
    const source = renderApp(seam.options);
    act(() => {
      source.open();
      source.emit("features", featuresFixture);
    });
    act(() => {
      seam.tick(Date.now());
    });
    const wrap = document.querySelector(".waveform-wrap");
    expect(document.querySelector("canvas.waveform")).not.toBeNull();
    expect(wrap).toHaveAttribute("data-listener-idle", "live");
  });

  it("shows the idle waveform placeholder before any features event", () => {
    const seam = fakeWaveformSeam();
    renderApp(seam.options);
    const wrap = document.querySelector(".waveform-wrap");
    expect(wrap).toHaveAttribute("data-waveform-state", "idle");
  });

  it("renders the clients.json fixture as the remote-viewer indicator", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("clients", clientsFixture);
    });
    expect(screen.getByText(/2 viewers \(1 remote\)/)).toBeInTheDocument();
  });

  it("goes disconnected when the heartbeat stops for 2 intervals", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("heartbeat", heartbeatFixture);
    });
    expect(screen.getByRole("status")).toHaveAttribute("data-status", "connected");

    act(() => {
      vi.advanceTimersByTime(DISCONNECTED_AFTER_MS + 1000);
    });
    expect(screen.getByRole("status")).toHaveAttribute("data-status", "disconnected");
  });

  it("shows every panel's heading regardless of stream state", () => {
    renderApp();
    expect(screen.getByRole("heading", { name: "Voice" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Waveform" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Transcript" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Degradations" })).toBeInTheDocument();
  });

  it("survives an attack transcript frame: NUL bytes, bidi override chars, a 10k-char string, and the module's own delimiters", () => {
    const source = renderApp();
    const bidi = "‮evil‬";
    const huge = "x".repeat(10_000);
    const withDelimiters = 'role":"assistant","text":"injected';
    expect(() =>
      act(() => {
        source.open();
        source.emit(
          "transcript",
          envelope("transcript", { role: "user", text: `\u0000${bidi}${huge}${withDelimiters}` }),
        );
      }),
    ).not.toThrow();
    // React escapes text content; no raw HTML/script tag can execute even if
    // the daemon (or a compromised upstream) sent one.
    expect(document.querySelector("script[data-injected]")).toBeNull();
  });

  it("survives a degradation frame whose fields are not the expected types", () => {
    const source = renderApp();
    expect(() =>
      act(() => {
        source.open();
        // deliberately malformed shape, mirrors a hostile/buggy upstream
        source.emit(
          "degradation",
          envelope("degradation", { source: 42, code: null, reason: ["not", "a", "string"] }),
        );
      }),
    ).not.toThrow();
  });

  it("survives an unparseable (non-JSON) SSE frame body without crashing the app", () => {
    const source = renderApp();
    expect(() =>
      act(() => {
        source.open();
        source.emitRaw("state", "{not valid json at all");
      }),
    ).not.toThrow();
    expect(screen.getByRole("button", { name: "Start voice" })).toBeInTheDocument();
  });

  it("drops (rather than mis-renders) a frame that is only the inner data object -- round 1's exact bug, reproduced end to end", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("transcript", transcriptFixture.data); // no v, no data wrapper
      source.emit("degradation", degradationFixture.data);
    });
    // Neither pane renders anything for the dropped frames: no "user:" line
    // with empty text, no "[app://embodiment] ?:" degradation row -- the
    // exact symptom the round-2 report described.
    expect(screen.queryByText(/user:/)).toBeNull();
    expect(screen.queryByText(/\?:/)).toBeNull();
  });

  // Round 4 item #1 [LIVE, MAJOR]: t16's guard refused every cookie-
  // credentialed stream request over Tailscale (off-loopback, off-https)
  // with http-refused-cookie-without-origin. Fixed by dropping the cookie
  // path entirely: the secret now travels ONLY as an Authorization header
  // on the stream request itself, applied by clicking "Apply", and forces
  // a reconnect (fetch-based streams have no "reconnect now" either).
  it("carries the applied secret as an Authorization header and reconnects when the operator applies it", () => {
    renderApp();
    expect(FakeSSEConnection.instances).toHaveLength(1);
    // No secret was ever applied yet -- no Authorization header at all.
    expect(FakeSSEConnection.instances[0].headers.Authorization).toBeUndefined();

    fireEvent.change(screen.getByLabelText("install secret"), {
      target: { value: "s3cr3t-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    // A NEW connection was opened -- the only "reconnect now" this
    // transport supports is close the old one and open a new one.
    expect(FakeSSEConnection.instances.length).toBeGreaterThanOrEqual(2);
    expect(FakeSSEConnection.latest().headers.Authorization).toBe("Bearer s3cr3t-value");
  });

  it("never writes document.cookie when the secret is applied (round 4: one credential path)", () => {
    renderApp();
    const before = document.cookie;
    fireEvent.change(screen.getByLabelText("install secret"), {
      target: { value: "s3cr3t-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(document.cookie).toBe(before);
    expect(document.cookie).not.toContain("embodiment_secret");
  });

  it("persists the applied secret to sessionStorage, not localStorage", () => {
    renderApp();
    fireEvent.change(screen.getByLabelText("install secret"), {
      target: { value: "s3cr3t-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(sessionStorage.getItem("embodiment.installSecret")).toBe("s3cr3t-value");
    expect(localStorage.getItem("embodiment.installSecret")).toBeNull();
  });

  it("restores a previously applied secret from sessionStorage on mount, carried on the very first connection", () => {
    sessionStorage.setItem("embodiment.installSecret", "restored-secret");
    renderApp();
    expect(FakeSSEConnection.instances).toHaveLength(1);
    expect(FakeSSEConnection.latest().headers.Authorization).toBe("Bearer restored-secret");
  });

  // Round 5 [(c), the silent-click investigation]: the typed `secret` field
  // is NOT the credential -- only `appliedSecret` (set by "Apply") is ever
  // sent on a control POST. Verified with a real, unapplied divergence
  // between the two: type something into the field WITHOUT clicking
  // Apply, then click Start -- the POST must use whatever was last
  // actually applied (here: nothing, i.e. the empty string), never the
  // freshly-typed-but-unapplied text.
  describe("control POSTs use appliedSecret, never the typed field directly", () => {
    it("Start voice sends the APPLIED secret, not a typed-but-unapplied one", async () => {
      const startSpy = vi.spyOn(control, "startVoice").mockResolvedValue(
        new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 }),
      );
      const source = renderApp();
      act(() => source.open());

      fireEvent.change(screen.getByLabelText("install secret"), {
        target: { value: "s3cr3t-applied" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Apply" }));

      // type something ELSE afterwards, but never click Apply again
      fireEvent.change(screen.getByLabelText("install secret"), {
        target: { value: "something-else-never-applied" },
      });

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Start voice" }));
      });

      expect(startSpy).toHaveBeenCalledWith("s3cr3t-applied");
      startSpy.mockRestore();
    });

    it("Mute sends the applied secret", async () => {
      const muteSpy = vi.spyOn(control, "setMicMute").mockResolvedValue(
        new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 }),
      );
      const source = renderApp();
      act(() => {
        source.open();
        source.emit("mic", micFixture); // hot: true -> Mute mic
      });
      fireEvent.change(screen.getByLabelText("install secret"), { target: { value: "s3cr3t-x" } });
      fireEvent.click(screen.getByRole("button", { name: "Apply" }));

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Mute mic" }));
      });
      // Round 6: hot=true -> clicking "Mute mic" must request muted=true
      // (round 5 shipped this inverted -- `!(micHot ?? false)` sent
      // `false` here, proven live: the daemon stayed unmuted).
      expect(muteSpy).toHaveBeenCalledWith("s3cr3t-x", true);
      muteSpy.mockRestore();
    });
  });

  // Round 6 [proven live, twice]: clicking "Mute mic" while hot left the
  // daemon UNmuted; clicking "Unmute mic" while muted left it muted.
  // App.tsx sent `muted = !hot`, backwards -- a toggle's new `muted` value
  // must equal the CURRENT `hot` value. These assert the exact POST body
  // in both directions; the round-5 code fails both.
  describe("mic mute direction (round 6)", () => {
    it("sends {muted: true} when the mic is currently hot (click 'Mute mic')", async () => {
      const muteSpy = vi.spyOn(control, "setMicMute").mockResolvedValue(
        new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 }),
      );
      const source = renderApp();
      act(() => {
        source.open();
        source.emit("mic", envelope("mic", { hot: true, ear: "host" }));
      });
      expect(screen.getByRole("button", { name: "Mute mic" })).toBeInTheDocument();

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Mute mic" }));
      });

      expect(muteSpy).toHaveBeenCalledTimes(1);
      const [, muted] = muteSpy.mock.calls[0];
      expect(muted).toBe(true);
      muteSpy.mockRestore();
    });

    it("sends {muted: false} when the mic is currently muted (click 'Unmute mic')", async () => {
      const muteSpy = vi.spyOn(control, "setMicMute").mockResolvedValue(
        new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 }),
      );
      const source = renderApp();
      act(() => {
        source.open();
        source.emit("mic", envelope("mic", { hot: false, ear: "host" }));
      });
      expect(screen.getByRole("button", { name: "Unmute mic" })).toBeInTheDocument();

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Unmute mic" }));
      });

      expect(muteSpy).toHaveBeenCalledTimes(1);
      const [, muted] = muteSpy.mock.calls[0];
      expect(muted).toBe(false);
      muteSpy.mockRestore();
    });

    it("the actual POST body carries the correct JSON in both directions", async () => {
      // Exercise the real setMicMute (not spied) through a fetchFn spy on
      // the underlying request, so this test also proves control.ts's own
      // request-body construction, not just App.tsx's argument passing.
      const fetchFn = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 }),
      );
      const originalFetch = globalThis.fetch;
      globalThis.fetch = fetchFn as unknown as typeof fetch;
      try {
        const source = renderApp();
        act(() => {
          source.open();
          source.emit("mic", envelope("mic", { hot: true, ear: "host" }));
        });
        await act(async () => {
          fireEvent.click(screen.getByRole("button", { name: "Mute mic" }));
        });
        const [, init] = fetchFn.mock.calls[0];
        expect(JSON.parse((init as RequestInit).body as string)).toEqual({ muted: true });
      } finally {
        globalThis.fetch = originalFetch;
      }
    });
  });

  // Round 5's explicit rendering requirement: 200 -> refresh via
  // GET /api/status; 4xx -> a visible "refused: <code>" line, no secret.
  describe("control POST outcomes are rendered, never silent", () => {
    it("refreshes from GET /api/status on a 200 outcome", async () => {
      const startSpy = vi.spyOn(control, "startVoice").mockResolvedValue(
        new Response(JSON.stringify({ ok: true, result: { ear: "host" } }), { status: 200 }),
      );
      const fetchStatusFn = vi.fn(async () =>
        ({
          daemon: { ear: { active: "host", muted: false }, clients: null, recall: null },
          http: {},
        }) as unknown as Awaited<ReturnType<typeof control.fetchStatus>>,
      );
      FakeSSEConnection.reset();
      render(
        <App eventStreamOptions={{ connect: fakeConnect, fetchStatusFn }} />,
      );
      const source = FakeSSEConnection.latest();
      act(() => source.open());
      fetchStatusFn.mockClear(); // ignore the connect-time seed call

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Start voice" }));
      });

      expect(fetchStatusFn).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("button", { name: "Stop voice" })).toBeInTheDocument();
      startSpy.mockRestore();
    });

    it("shows a visible 'refused: <code>' line on a 4xx outcome, with no secret in it", async () => {
      const startSpy = vi.spyOn(control, "startVoice").mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: "http-refused-secret", message: "no valid install secret was presented" } }),
          { status: 401 },
        ),
      );
      renderApp();
      fireEvent.change(screen.getByLabelText("install secret"), { target: { value: "s3cr3t-value" } });
      fireEvent.click(screen.getByRole("button", { name: "Apply" }));

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Start voice" }));
      });

      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent("http-refused-secret");
      expect(alert.textContent).not.toContain("s3cr3t-value");
      expect(alert.textContent).not.toContain("no valid install secret was presented");
      startSpy.mockRestore();
    });

    it("clears a previous refusal once a later control POST succeeds", async () => {
      const startSpy = vi
        .spyOn(control, "startVoice")
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ error: { code: "http-refused-secret", message: "x" } }), {
            status: 401,
          }),
        )
        .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 }));
      renderApp();

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Start voice" }));
      });
      expect(screen.getByRole("alert")).toHaveTextContent("http-refused-secret");

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Start voice" }));
      });
      expect(screen.queryByRole("alert")).toBeNull();
      startSpy.mockRestore();
    });
  });

  it("shows 'not authorised' rather than 'disconnected' when the stream errors before ever opening", () => {
    const source = renderApp();
    act(() => source.error());
    expect(screen.getByRole("status")).toHaveAttribute("data-status", "unauthorized");
    expect(screen.getByRole("status")).toHaveTextContent("not authorised");
  });

  it("still shows plain 'disconnected' (not 'not authorised') once it had opened successfully first", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.error();
    });
    expect(screen.getByRole("status")).toHaveAttribute("data-status", "disconnected");
  });

  // Brief's explicit ask: a Hebrew transcript line renders dir="auto" with
  // the text present verbatim -- Hebrew is the spoken language (CLAUDE.md).
  it("renders a Hebrew transcript event with dir=\"auto\" and the text verbatim", () => {
    const source = renderApp();
    const hebrewText = "מה מזג האוויר היום";
    act(() => {
      source.open();
      source.emit(
        "transcript",
        envelope("transcript", { role: "user", text: hebrewText }),
      );
    });
    const line = screen.getByText(hebrewText);
    expect(line).toBeInTheDocument();
    expect(line.closest("[dir]")).toHaveAttribute("dir", "auto");
    expect(line.textContent).toContain(hebrewText);
  });

  it("renders the waveform as the first panel after the header (centrepiece)", () => {
    renderApp();
    const panels = document.querySelectorAll(".panel");
    expect(panels.length).toBeGreaterThan(0);
    expect(panels[0].querySelector("canvas.waveform")).not.toBeNull();
  });

  it("renders all fixtures together end to end without throwing", () => {
    const source = renderApp();
    expect(() =>
      act(() => {
        source.open();
        source.emit("state", stateFixture);
        source.emit("mic", micFixture);
        source.emit("turn", turnFixture);
        source.emit("transcript", transcriptFixture);
        source.emit("reply", replyFixture);
        source.emit("degradation", degradationFixture);
        source.emit("features", featuresFixture);
        source.emit("clients", clientsFixture);
        source.emit("heartbeat", heartbeatFixture);
      }),
    ).not.toThrow();
    // sanity: the transcript pane holds both speech-carrying entries, WITH
    // their actual text (round 1 would have passed this "not throw" check
    // too -- the assertions below are what round 1 failed).
    const transcriptPanel = screen.getByRole("heading", { name: "Transcript" }).closest("section");
    expect(transcriptPanel).not.toBeNull();
    expect(
      within(transcriptPanel as HTMLElement).getByText(new RegExp(transcriptFixture.data.text)),
    ).toBeInTheDocument();
    expect(
      within(transcriptPanel as HTMLElement).getByText(new RegExp(replyFixture.data.text)),
    ).toBeInTheDocument();
    const degradationsPanel = screen
      .getByRole("heading", { name: "Degradations" })
      .closest("section");
    expect(
      within(degradationsPanel as HTMLElement).getByText(new RegExp(degradationFixture.data.code)),
    ).toBeInTheDocument();
    expect(
      within(degradationsPanel as HTMLElement).getByText(
        new RegExp(degradationFixture.data.reason),
      ),
    ).toBeInTheDocument();
  });
});
