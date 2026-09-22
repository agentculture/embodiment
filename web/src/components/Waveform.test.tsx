import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Waveform } from "./Waveform";
import type { EventEnvelope } from "../api/events";

function encode(values: number[]): string {
  const bytes = new Uint8Array(values.map((v) => v & 0xff));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function featuresEnvelope(
  overrides: Partial<{ env: string; level_db: number; direction: string }> = {},
  seq = 1,
): EventEnvelope<"features"> {
  const mins = Array.from({ length: 16 }, () => -40);
  const maxs = Array.from({ length: 16 }, () => 40);
  return {
    v: 1,
    kind: "features",
    ts: "2026-09-22T12:00:00.000Z",
    seq,
    source: "app://embodiment",
    data: {
      direction: "out",
      env: encode([...mins, ...maxs]),
      level_db: -20,
      noise_floor_db: -60,
      zero_crossing_hz: 200,
      ...overrides,
    },
  };
}

describe("Waveform", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("is idle before any features event", () => {
    const { container } = render(<Waveform features={null} idleAfterMs={50} />);
    const svg = container.querySelector("svg.waveform");
    expect(svg).toHaveAttribute("data-waveform-state", "idle");
  });

  it("is idle for a rejected/undecodable envelope even though a features event arrived", () => {
    const bad = featuresEnvelope({ env: "not valid base64!!!" });
    const { container } = render(<Waveform features={bad} idleAfterMs={50} />);
    const svg = container.querySelector("svg.waveform");
    expect(svg).toHaveAttribute("data-waveform-state", "idle");
  });

  it("becomes live immediately when a valid features event arrives", () => {
    const { container } = render(<Waveform features={featuresEnvelope()} idleAfterMs={50} />);
    const svg = container.querySelector("svg.waveform");
    expect(svg).toHaveAttribute("data-waveform-state", "live");
    expect(svg).toHaveAttribute("data-direction", "out");
  });

  it("draws one mirrored bar per envelope bucket", () => {
    const { container } = render(<Waveform features={featuresEnvelope()} idleAfterMs={50} />);
    const bars = container.querySelectorAll(".waveform__bar");
    expect(bars).toHaveLength(16);
  });

  it("draws bars symmetric about the vertical center (mirrored)", () => {
    const { container } = render(<Waveform features={featuresEnvelope()} idleAfterMs={50} />);
    const bar = container.querySelector(".waveform__bar") as SVGRectElement;
    const y = Number(bar.getAttribute("y"));
    const height = Number(bar.getAttribute("height"));
    // center of the viewBox is VIEWBOX_HEIGHT/2 == 50; a mirrored bar's
    // midpoint must sit exactly on that center line.
    expect(y + height / 2).toBeCloseTo(50, 5);
  });

  it("renders a level-fill element whose height reflects level_db", () => {
    const { container } = render(
      <Waveform features={featuresEnvelope({ level_db: -6 })} idleAfterMs={50} />,
    );
    const loud = container.querySelector(".waveform__level-fill") as SVGRectElement;
    const loudHeight = Number(loud.getAttribute("height"));

    const { container: quietContainer } = render(
      <Waveform features={featuresEnvelope({ level_db: -90 }, 2)} idleAfterMs={50} />,
    );
    const quiet = quietContainer.querySelector(".waveform__level-fill") as SVGRectElement;
    const quietHeight = Number(quiet.getAttribute("height"));

    expect(loudHeight).toBeGreaterThan(quietHeight);
  });

  it("goes idle again after idleAfterMs with no new features event -- never a frozen last frame", () => {
    // The SAME object reference across both renders: a plain re-render (no
    // new SSE frame) must not look like a new event just because a parent
    // re-rendered for an unrelated reason.
    const frame = featuresEnvelope();
    const { container, rerender } = render(<Waveform features={frame} idleAfterMs={50} />);
    expect(container.querySelector("svg.waveform")).toHaveAttribute("data-waveform-state", "live");

    act(() => {
      vi.advanceTimersByTime(60);
    });
    rerender(<Waveform features={frame} idleAfterMs={50} />);
    // the idle timeout already fired with no NEW event in between -- must
    // show idle, never a frozen last frame.
    expect(container.querySelector("svg.waveform")).toHaveAttribute("data-waveform-state", "idle");
  });

  it("stays live if a new features event arrives before the idle timeout", () => {
    const { container, rerender } = render(
      <Waveform features={featuresEnvelope({}, 1)} idleAfterMs={100} />,
    );
    act(() => {
      vi.advanceTimersByTime(60);
    });
    rerender(<Waveform features={featuresEnvelope({}, 2)} idleAfterMs={100} />);
    act(() => {
      vi.advanceTimersByTime(60);
    });
    // 120ms total have passed, but the second event reset the clock at 60ms,
    // so only 60ms has elapsed since the last event -- still live.
    expect(container.querySelector("svg.waveform")).toHaveAttribute("data-waveform-state", "live");
  });

  it("actually goes idle 500ms (the default) after the last event, using the real default", () => {
    const frame = featuresEnvelope();
    const { container, rerender } = render(<Waveform features={frame} />);
    expect(container.querySelector("svg.waveform")).toHaveAttribute("data-waveform-state", "live");
    act(() => {
      vi.advanceTimersByTime(501);
    });
    rerender(<Waveform features={frame} />);
    expect(container.querySelector("svg.waveform")).toHaveAttribute("data-waveform-state", "idle");
  });

  it("uses a viewBox with preserveAspectRatio=none so the bars stretch to fill any container width", () => {
    const { container } = render(<Waveform features={featuresEnvelope()} idleAfterMs={50} />);
    const svg = container.querySelector("svg.waveform");
    expect(svg).toHaveAttribute("preserveAspectRatio", "none");
  });
});
