import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { FakeSSEConnection, fakeConnect } from "./hooks/fakeSSEConnection";
import { DISCONNECTED_AFTER_MS } from "./api/events";

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

function renderApp() {
  FakeSSEConnection.reset();
  render(<App eventStreamOptions={{ connect: fakeConnect }} />);
  return FakeSSEConnection.latest();
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

  it("renders the state.json fixture as the voice-on/off control", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("state", stateFixture);
    });
    // stateFixture.data.status is "up", not "voice-on" -> Start voice shown
    expect(screen.getByRole("button", { name: "Start voice" })).toBeInTheDocument();
  });

  it("renders the mic.json fixture as the mute control", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("mic", micFixture);
    });
    // mic.json: hot: true -> "Mute mic"
    expect(screen.getByRole("button", { name: "Mute mic" })).toBeInTheDocument();
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

  it("shows the recall-mode indicator as unknown until a signal arrives, then lexical-fallback on a continuity degradation", () => {
    const source = renderApp();
    // No positive "semantic" signal exists in v1 (see RecallModeIndicator's
    // own comment) -- the honest default is "unknown", never a claim of the
    // better state with no evidence for it (this rig's embedder is not
    // ready; CLAUDE.md's C3).
    expect(screen.getByText(/recall: unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/recall: semantic/)).toBeNull();
    act(() => {
      source.open();
      source.emit("degradation", degradationFixture); // source: "continuity"
    });
    expect(screen.getByText(/recall: lexical fallback/)).toBeInTheDocument();
  });

  it("renders the features.json fixture as a live waveform", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("features", featuresFixture);
    });
    const svg = document.querySelector("svg.waveform");
    expect(svg).toHaveAttribute("data-waveform-state", "live");
  });

  it("shows the idle waveform placeholder before any features event", () => {
    renderApp();
    const svg = document.querySelector("svg.waveform");
    expect(svg).toHaveAttribute("data-waveform-state", "idle");
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
    expect(panels[0].querySelector("svg.waveform")).not.toBeNull();
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
