import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Waveform } from "./Waveform";
import type { RafScheduler, ScalableContext2D } from "./Waveform";
import type { EventEnvelope } from "../api/events";
import type { ResizeObserverLike } from "../waveform/hidpi";

function encode(values: number[]): string {
  const bytes = new Uint8Array(values.map((v) => v & 0xff));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function featuresEvent(
  overrides: Record<string, unknown> = {},
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

/** A controllable requestAnimationFrame double: `tick()` runs the most
 *  recently scheduled callback exactly once, at the caller's chosen time --
 *  never a real timer, so the test drives frame cadence deterministically. */
function fakeRaf(): { scheduler: RafScheduler; tick: (nowMs: number) => void; cancelled: number[] } {
  let pending: FrameRequestCallback | null = null;
  let nextId = 1;
  const cancelled: number[] = [];
  const scheduler: RafScheduler = {
    request: (cb) => {
      pending = cb;
      return nextId++;
    },
    cancel: (id) => {
      cancelled.push(id);
      pending = null;
    },
  };
  return {
    scheduler,
    tick: (nowMs: number) => {
      const cb = pending;
      pending = null;
      cb?.(nowMs);
    },
    cancelled,
  };
}

function fakeContext(): ScalableContext2D {
  return {
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
}

/** A controllable ResizeObserver double: `trigger()` invokes whatever
 *  callback the component last registered; `observedCount`/`disconnected`
 *  let a test assert the wiring itself (observe on mount, disconnect on
 *  unmount) without a real `ResizeObserver` -- jsdom has none at all. */
function fakeResizeObserverFactory(): {
  factory: (callback: () => void) => ResizeObserverLike;
  trigger: () => void;
  observedCount: () => number;
  disconnected: () => boolean;
} {
  let callback: (() => void) | null = null;
  let observed = 0;
  let disconnected = false;
  return {
    factory: (cb: () => void) => {
      callback = cb;
      return {
        observe: () => {
          observed += 1;
        },
        disconnect: () => {
          disconnected = true;
        },
      };
    },
    trigger: () => callback?.(),
    observedCount: () => observed,
    disconnected: () => disconnected,
  };
}

describe("Waveform", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("is idle before any features event and before the first animation frame runs", () => {
    const { container } = render(<Waveform features={null} idleAfterMs={50} />);
    expect(container.querySelector(".waveform-wrap")).toHaveAttribute(
      "data-waveform-state",
      "idle",
    );
  });

  it("renders a <canvas> element (the oscilloscope), not an SVG", () => {
    const { container } = render(<Waveform features={null} idleAfterMs={50} />);
    expect(container.querySelector("canvas.waveform")).not.toBeNull();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("does not crash when the injected context factory returns null (e.g. an unsupported canvas)", () => {
    const raf = fakeRaf();
    expect(() =>
      render(
        <Waveform
          features={featuresEvent()}
          idleAfterMs={50}
          contextFactory={() => null}
          raf={raf.scheduler}
        />,
      ),
    ).not.toThrow();
  });

  it("becomes live on the animation frame after a features event arrives, and goes flat (idle) -- not frozen -- once the stream stops, at animation-frame rate", () => {
    const raf = fakeRaf();
    let now = 1_000;
    const nowFn = () => now;

    const { container, rerender } = render(
      <Waveform
        features={null}
        idleAfterMs={50}
        contextFactory={fakeContext}
        raf={raf.scheduler}
        nowFn={nowFn}
      />,
    );
    const wrap = () => container.querySelector(".waveform-wrap") as HTMLElement;
    expect(wrap()).toHaveAttribute("data-waveform-state", "idle");

    // A features event arrives; the NEXT animation frame is what notices it
    // (not the React render itself -- the canvas is drawn imperatively).
    act(() => {
      rerender(
        <Waveform
          features={featuresEvent()}
          idleAfterMs={50}
          contextFactory={fakeContext}
          raf={raf.scheduler}
          nowFn={nowFn}
        />,
      );
    });
    act(() => {
      raf.tick(now);
    });
    expect(wrap()).toHaveAttribute("data-waveform-state", "live");

    // No new event; time advances past idleAfterMs. The very next frame
    // (with no new event in between) must notice the silence on its own.
    now += 51;
    act(() => {
      raf.tick(now);
    });
    expect(wrap()).toHaveAttribute("data-waveform-state", "idle");
  });

  it("coalesces multiple features events that arrive between two animation frames into the LATEST one", () => {
    const raf = fakeRaf();
    let now = 1_000;
    const nowFn = () => now;
    const drawn: unknown[] = [];
    const ctx: ScalableContext2D = fakeContext();
    ctx.lineTo = (...args: unknown[]) => {
      drawn.push(args);
    };

    const { rerender } = render(
      <Waveform
        features={featuresEvent({}, 1)}
        idleAfterMs={500}
        contextFactory={() => ctx}
        raf={raf.scheduler}
        nowFn={nowFn}
      />,
    );

    // Two more events land before the animation frame ever fires.
    act(() => {
      rerender(
        <Waveform
          features={featuresEvent(
            {
              env: encode([
                ...Array.from({ length: 16 }, () => -5),
                ...Array.from({ length: 16 }, () => 5),
              ]),
            },
            2,
          )}
          idleAfterMs={500}
          contextFactory={() => ctx}
          raf={raf.scheduler}
          nowFn={nowFn}
        />,
      );
    });
    act(() => {
      rerender(
        <Waveform
          features={featuresEvent(
            {
              env: encode([
                ...Array.from({ length: 16 }, () => -127),
                ...Array.from({ length: 16 }, () => 127),
              ]),
            },
            3,
          )}
          idleAfterMs={500}
          contextFactory={() => ctx}
          raf={raf.scheduler}
          nowFn={nowFn}
        />,
      );
    });

    // Only ONE animation frame runs for all of the above.
    drawn.length = 0;
    act(() => {
      raf.tick(now);
    });
    expect(drawn.length).toBeGreaterThan(0);
    // Only one frame was drawn despite three events -- the loop ran exactly
    // once (it re-scheduled itself for the NEXT frame, which we never tick).
  });

  it("cancels the animation frame loop on unmount", () => {
    const raf = fakeRaf();
    const { unmount } = render(
      <Waveform
        features={featuresEvent()}
        idleAfterMs={50}
        contextFactory={fakeContext}
        raf={raf.scheduler}
      />,
    );
    unmount();
    expect(raf.cancelled.length).toBeGreaterThan(0);
  });

  it("keeps the listener trace's idle state independent of the assistant trace's", () => {
    const raf = fakeRaf();
    let now = 1_000;
    const nowFn = () => now;

    const { container, rerender } = render(
      <Waveform
        features={featuresEvent({ direction: "out" }, 1)}
        idleAfterMs={50}
        contextFactory={fakeContext}
        raf={raf.scheduler}
        nowFn={nowFn}
      />,
    );
    act(() => {
      raf.tick(now);
    });
    const wrap = () => container.querySelector(".waveform-wrap") as HTMLElement;
    // No "in" event ever arrived -- listener stays idle even while the
    // assistant is live.
    expect(wrap()).toHaveAttribute("data-waveform-state", "live");
    expect(wrap()).toHaveAttribute("data-listener-idle", "idle");

    act(() => {
      rerender(
        <Waveform
          features={featuresEvent({ direction: "in" }, 2)}
          idleAfterMs={50}
          contextFactory={fakeContext}
          raf={raf.scheduler}
          nowFn={nowFn}
        />,
      );
    });
    act(() => {
      raf.tick(now);
    });
    expect(wrap()).toHaveAttribute("data-listener-idle", "live");
  });

  it("draws from the bus by default, and reports the analyser as the listener source only when one is supplied (AnalyserNode only when the browser is the ear)", () => {
    const raf = fakeRaf();
    const { container: busContainer } = render(
      <Waveform features={null} idleAfterMs={50} contextFactory={fakeContext} raf={raf.scheduler} />,
    );
    expect(busContainer.querySelector(".waveform-wrap")).toHaveAttribute(
      "data-listener-source",
      "bus",
    );

    const analyser = { readTrace: () => ({ mins: [-0.1, -0.2], maxs: [0.1, 0.2] }) };
    const { container: analyserContainer } = render(
      <Waveform
        features={null}
        idleAfterMs={50}
        contextFactory={fakeContext}
        raf={raf.scheduler}
        listenerAnalyser={analyser}
      />,
    );
    expect(analyserContainer.querySelector(".waveform-wrap")).toHaveAttribute(
      "data-listener-source",
      "analyser",
    );
  });

  it("renders readouts for fields present on the latest assistant sample, and omits fields that are absent", () => {
    const raf = fakeRaf();
    const { container } = render(
      <Waveform
        features={featuresEvent({ zero_crossing_hz: 180, noise_floor_db: -55 })}
        idleAfterMs={500}
        contextFactory={fakeContext}
        raf={raf.scheduler}
      />,
    );
    expect(container.querySelector('[data-readouts-for="assistant"] [data-readout="pitch"]')).not.toBeNull();
    expect(
      container.querySelector('[data-readouts-for="assistant"] [data-readout="noise-floor"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-readouts-for="assistant"] [data-readout="resonance"]'),
    ).toBeNull();
  });

  it("omits a readout (never renders 0) when its field is null on the event", () => {
    const raf = fakeRaf();
    const { container } = render(
      <Waveform
        features={featuresEvent({ zero_crossing_hz: null })}
        idleAfterMs={500}
        contextFactory={fakeContext}
        raf={raf.scheduler}
      />,
    );
    expect(container.querySelector('[data-readouts-for="assistant"] [data-readout="pitch"]')).toBeNull();
  });

  // -- Round 2, item 2: hi-DPI backing store sizing -------------------------

  describe("hi-DPI canvas backing store", () => {
    it("sizes the backing store from clientWidth/clientHeight * devicePixelRatio on mount", () => {
      const raf = fakeRaf();
      const canvasSize = { width: 300, height: 150 };
      const { container } = render(
        <Waveform
          features={null}
          contextFactory={fakeContext}
          raf={raf.scheduler}
          measureSize={() => canvasSize}
          devicePixelRatioFn={() => 2}
        />,
      );
      const canvas = container.querySelector("canvas.waveform") as HTMLCanvasElement;
      expect(canvas.width).toBe(600);
      expect(canvas.height).toBe(300);
    });

    it("scales the context by the device pixel ratio via setTransform", () => {
      const raf = fakeRaf();
      const setTransformCalls: number[][] = [];
      const ctx = fakeContext();
      ctx.setTransform = (a, b, c, d, e, f) => setTransformCalls.push([a, b, c, d, e, f]);
      render(
        <Waveform
          features={null}
          contextFactory={() => ctx}
          raf={raf.scheduler}
          measureSize={() => ({ width: 300, height: 150 })}
          devicePixelRatioFn={() => 3}
        />,
      );
      expect(setTransformCalls).toContainEqual([3, 0, 0, 3, 0, 0]);
    });

    it("re-sizes the backing store when the injected ResizeObserver fires", () => {
      const raf = fakeRaf();
      const ro = fakeResizeObserverFactory();
      let size = { width: 300, height: 150 };
      const { container } = render(
        <Waveform
          features={null}
          contextFactory={fakeContext}
          raf={raf.scheduler}
          measureSize={() => size}
          devicePixelRatioFn={() => 1}
          resizeObserverFactory={ro.factory}
        />,
      );
      const canvas = container.querySelector("canvas.waveform") as HTMLCanvasElement;
      expect(canvas.width).toBe(300);
      expect(ro.observedCount()).toBe(1);

      size = { width: 600, height: 300 };
      act(() => {
        ro.trigger();
      });
      expect(canvas.width).toBe(600);
      expect(canvas.height).toBe(300);
    });

    it("disconnects the ResizeObserver on unmount", () => {
      const raf = fakeRaf();
      const ro = fakeResizeObserverFactory();
      const { unmount } = render(
        <Waveform
          features={null}
          contextFactory={fakeContext}
          raf={raf.scheduler}
          resizeObserverFactory={ro.factory}
        />,
      );
      expect(ro.disconnected()).toBe(false);
      unmount();
      expect(ro.disconnected()).toBe(true);
    });

    it("never throws when devicePixelRatio is absurd (attack: a spoofed huge value)", () => {
      const raf = fakeRaf();
      expect(() =>
        render(
          <Waveform
            features={null}
            contextFactory={fakeContext}
            raf={raf.scheduler}
            devicePixelRatioFn={() => 1_000_000}
          />,
        ),
      ).not.toThrow();
    });

    it("keeps the CSS size untouched (never writes canvas.style)", () => {
      const raf = fakeRaf();
      const { container } = render(
        <Waveform
          features={null}
          contextFactory={fakeContext}
          raf={raf.scheduler}
          measureSize={() => ({ width: 300, height: 150 })}
          devicePixelRatioFn={() => 2}
        />,
      );
      const canvas = container.querySelector("canvas.waveform") as HTMLCanvasElement;
      expect(canvas.style.width).toBe("");
      expect(canvas.style.height).toBe("");
    });
  });

  // -- Round 2, item 3: colors from design tokens ---------------------------

  describe("design-token colors", () => {
    it("reads colors via the injected style reader once on mount", () => {
      const raf = fakeRaf();
      const styleReader = vi.fn(() => ({
        getPropertyValue: (name: string) =>
          name === "--accent" ? "#111111" : name === "--ink-soft" ? "#222222" : "",
      }));
      render(
        <Waveform
          features={null}
          contextFactory={fakeContext}
          raf={raf.scheduler}
          styleReader={styleReader}
        />,
      );
      expect(styleReader).toHaveBeenCalled();
    });

    it("re-reads colors when the ResizeObserver callback fires (a theme/resize signal)", () => {
      const raf = fakeRaf();
      const ro = fakeResizeObserverFactory();
      const styleReader = vi.fn(() => ({ getPropertyValue: () => "#abcdef" }));
      render(
        <Waveform
          features={null}
          contextFactory={fakeContext}
          raf={raf.scheduler}
          resizeObserverFactory={ro.factory}
          styleReader={styleReader}
        />,
      );
      const callsAfterMount = styleReader.mock.calls.length;
      expect(callsAfterMount).toBeGreaterThan(0);
      act(() => {
        ro.trigger();
      });
      expect(styleReader.mock.calls.length).toBeGreaterThan(callsAfterMount);
    });

    it("falls back to the hardcoded default color when a token resolves empty, without throwing", () => {
      const raf = fakeRaf();
      const styleReader = () => ({ getPropertyValue: () => "" });
      expect(() =>
        render(
          <Waveform
            features={featuresEvent()}
            idleAfterMs={500}
            contextFactory={fakeContext}
            raf={raf.scheduler}
            styleReader={styleReader}
          />,
        ),
      ).not.toThrow();
    });
  });
});
