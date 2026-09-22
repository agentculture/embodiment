// waveform/hidpi.test.ts
//
// Task t18 round 2, item 2 (verbatim): "size the canvas backing store from
// `clientWidth * devicePixelRatio` on mount and on a ResizeObserver (cleared
// on unmount), scale the context, keep the CSS size from the stylesheet."
// The arithmetic half, pinned here with no DOM at all; the wiring half
// (actual canvas/ResizeObserver/unmount) is proven in Waveform.test.tsx with
// an injected ResizeObserver factory and a fake DPR, per the coordinator's
// own instruction.

import { describe, expect, it } from "vitest";
import { computeBackingSize, defaultResizeObserverFactory, readDevicePixelRatio } from "./hidpi";

describe("computeBackingSize", () => {
  it("multiplies CSS size by the device pixel ratio", () => {
    expect(computeBackingSize({ width: 300, height: 150 }, 2)).toEqual({
      width: 600,
      height: 300,
    });
  });

  it("is a passthrough (rounded) at DPR 1", () => {
    expect(computeBackingSize({ width: 300, height: 150 }, 1)).toEqual({
      width: 300,
      height: 150,
    });
  });

  it("rounds a fractional DPR result rather than truncating (a canvas dimension must be an integer)", () => {
    expect(computeBackingSize({ width: 301, height: 151 }, 1.5)).toEqual({
      width: 452, // round(451.5)
      height: 227, // round(226.5)
    });
  });

  it("floors at 1x1 for a zero-size CSS box (an unlaid-out canvas, e.g. under jsdom) -- never 0, never throws", () => {
    expect(computeBackingSize({ width: 0, height: 0 }, 2)).toEqual({ width: 1, height: 1 });
  });

  it("treats a non-finite or non-positive DPR as 1 rather than propagating NaN/Infinity", () => {
    expect(computeBackingSize({ width: 100, height: 50 }, NaN)).toEqual({ width: 100, height: 50 });
    expect(computeBackingSize({ width: 100, height: 50 }, 0)).toEqual({ width: 100, height: 50 });
    expect(computeBackingSize({ width: 100, height: 50 }, -3)).toEqual({ width: 100, height: 50 });
    expect(computeBackingSize({ width: 100, height: 50 }, Infinity)).toEqual({ width: 100, height: 50 });
  });

  it("treats a non-finite CSS dimension as 0 rather than propagating NaN", () => {
    expect(computeBackingSize({ width: NaN, height: 50 }, 2)).toEqual({ width: 1, height: 100 });
  });

  it("survives a very large DPR (attack: a spoofed/huge devicePixelRatio) without throwing", () => {
    expect(() => computeBackingSize({ width: 300, height: 150 }, 1_000_000)).not.toThrow();
  });
});

describe("defaultResizeObserverFactory", () => {
  it("returns a no-op observer (never throws) when ResizeObserver is not a global -- true under jsdom", () => {
    expect(typeof ResizeObserver).toBe("undefined");
    const observer = defaultResizeObserverFactory(() => {});
    expect(() => observer.observe({} as Element)).not.toThrow();
    expect(() => observer.disconnect()).not.toThrow();
  });
});

describe("readDevicePixelRatio", () => {
  it("returns a positive finite number (>= 1 in every real/test environment observed here)", () => {
    const dpr = readDevicePixelRatio();
    expect(Number.isFinite(dpr)).toBe(true);
    expect(dpr).toBeGreaterThan(0);
  });
});
