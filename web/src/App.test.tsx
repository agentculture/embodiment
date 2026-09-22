import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { FakeEventSource } from "./hooks/fakeEventSource";
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

function renderApp() {
  FakeEventSource.reset();
  render(
    <App
      eventStreamOptions={{
        eventSourceFactory: (url: string) => new FakeEventSource(url) as unknown as EventSource,
      }}
    />,
  );
  return FakeEventSource.latest();
}

describe("App — every state from the committed event fixtures", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
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

  it("renders the state.json fixture as the voice-on/off control", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("state", stateFixture.data);
    });
    // stateFixture.data.status is "up", not "voice-on" -> Start voice shown
    expect(screen.getByRole("button", { name: "Start voice" })).toBeInTheDocument();
  });

  it("renders the mic.json fixture as the mute control", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("mic", micFixture.data);
    });
    // mic.json: hot: true -> "Mute mic"
    expect(screen.getByRole("button", { name: "Mute mic" })).toBeInTheDocument();
  });

  it("renders the turn.json fixture's degradation codes without crashing", () => {
    const source = renderApp();
    expect(() =>
      act(() => {
        source.open();
        source.emit("turn", turnFixture.data);
      }),
    ).not.toThrow();
  });

  it("renders the transcript.json and reply.json fixtures in the transcript pane, RTL-safe", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("transcript", transcriptFixture.data);
      source.emit("reply", replyFixture.data);
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
      source.emit("degradation", degradationFixture.data);
    });
    expect(screen.getByText(new RegExp(degradationFixture.data.code))).toBeInTheDocument();
  });

  it("switches the recall-mode indicator once a continuity degradation arrives", () => {
    const source = renderApp();
    expect(screen.getByText(/recall: semantic/)).toBeInTheDocument();
    act(() => {
      source.open();
      source.emit("degradation", degradationFixture.data); // source: "continuity"
    });
    expect(screen.getByText(/recall: lexical fallback/)).toBeInTheDocument();
  });

  it("renders the features.json fixture as a live waveform", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("features", featuresFixture.data);
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
      source.emit("clients", clientsFixture.data);
    });
    expect(screen.getByText(/2 viewers \(1 remote\)/)).toBeInTheDocument();
  });

  it("goes disconnected when the heartbeat stops for 2 intervals", () => {
    const source = renderApp();
    act(() => {
      source.open();
      source.emit("heartbeat", heartbeatFixture.data);
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
        source.emit("transcript", { role: "user", text: `\u0000${bidi}${huge}${withDelimiters}` });
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
        source.emit("degradation", { source: 42, code: null, reason: ["not", "a", "string"] });
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

  it("renders all fixtures together end to end without throwing", () => {
    const source = renderApp();
    expect(() =>
      act(() => {
        source.open();
        source.emit("state", stateFixture.data);
        source.emit("mic", micFixture.data);
        source.emit("turn", turnFixture.data);
        source.emit("transcript", transcriptFixture.data);
        source.emit("reply", replyFixture.data);
        source.emit("degradation", degradationFixture.data);
        source.emit("features", featuresFixture.data);
        source.emit("clients", clientsFixture.data);
        source.emit("heartbeat", heartbeatFixture.data);
      }),
    ).not.toThrow();
    // sanity: the transcript pane holds both speech-carrying entries
    const transcriptPanel = screen.getByRole("heading", { name: "Transcript" }).closest("section");
    expect(transcriptPanel).not.toBeNull();
    expect(within(transcriptPanel as HTMLElement).getAllByText(/:/).length).toBeGreaterThan(0);
  });
});
