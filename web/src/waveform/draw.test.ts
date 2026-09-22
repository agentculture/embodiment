// waveform/draw.test.ts
//
// jsdom has no canvas (task t18's integrator note), so this pins the
// draw calls against a FAKE 2D context that records every call -- never a
// real canvas. Proves the "thin" half: given a model, drawOscilloscope
// issues the calls a real CanvasRenderingContext2D would render correctly,
// and nothing here decides idle/live -- that is `model.ts`'s job, already
// proven in `model.test.ts`.
//
// Round 2 (coordinator's live-Chrome review) adds: the envelope is drawn as
// TWO polylines (min line, max line) with a filled band between them, not
// one collapsed-magnitude sawtooth; and colors are resolved from computed
// style (design tokens), falling back to the hardcoded defaults.

import { describe, expect, it } from "vitest";
import {
  ASSISTANT_COLOR_TOKEN,
  CENTER_LINE_COLOR_TOKEN,
  DEFAULT_OSCILLOSCOPE_COLORS,
  LISTENER_COLOR_TOKEN,
  drawOscilloscope,
  readOscilloscopeColors,
} from "./draw";
import type { DrawableContext2D } from "./draw";
import type { OscilloscopeModel, TraceFrame } from "./model";

type Call = { name: string; args: unknown[] };

function fakeContext(): { ctx: DrawableContext2D; calls: Call[] } {
  const calls: Call[] = [];
  const ctx = {
    clearRect: (...args: unknown[]) => calls.push({ name: "clearRect", args }),
    beginPath: (...args: unknown[]) => calls.push({ name: "beginPath", args }),
    moveTo: (...args: unknown[]) => calls.push({ name: "moveTo", args }),
    lineTo: (...args: unknown[]) => calls.push({ name: "lineTo", args }),
    closePath: (...args: unknown[]) => calls.push({ name: "closePath", args }),
    stroke: (...args: unknown[]) => calls.push({ name: "stroke", args }),
    fill: (...args: unknown[]) => calls.push({ name: "fill", args }),
    fillRect: (...args: unknown[]) => calls.push({ name: "fillRect", args }),
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 0,
    globalAlpha: 1,
  } as DrawableContext2D;
  return { ctx, calls };
}

const idleFrame: TraceFrame = {
  isIdle: true,
  envelope: { mins: [], maxs: [] },
  levelNorm: 0,
  direction: null,
};
const liveFrame: TraceFrame = {
  isIdle: false,
  envelope: {
    mins: [-0.1, -0.5, -1, -0.2],
    maxs: [0.1, 0.5, 1, 0.2],
  },
  levelNorm: 0.6,
  direction: "out",
};

describe("drawOscilloscope", () => {
  it("clears the full canvas before drawing anything (never accumulates strokes)", () => {
    const { ctx, calls } = fakeContext();
    const model: OscilloscopeModel = { out: idleFrame, in: idleFrame };
    drawOscilloscope(ctx, 300, 100, model);
    expect(calls[0]).toEqual({ name: "clearRect", args: [0, 0, 300, 100] });
  });

  it("draws only a flat line (no per-bucket lineTo beyond the flat segment) for an idle trace", () => {
    const { ctx, calls } = fakeContext();
    const model: OscilloscopeModel = { out: idleFrame, in: idleFrame };
    drawOscilloscope(ctx, 300, 100, model);
    // center guide line + two idle-flat traces = 3 stroke calls, no fill.
    const strokeCount = calls.filter((c) => c.name === "stroke").length;
    const fillCount = calls.filter((c) => c.name === "fill").length;
    expect(strokeCount).toBe(3);
    expect(fillCount).toBe(0);
    const lineToCalls = calls.filter((c) => c.name === "lineTo");
    expect(lineToCalls).toHaveLength(3);
  });

  it("draws the envelope as TWO polylines (min line, max line), not one collapsed sawtooth line", () => {
    const { ctx, calls } = fakeContext();
    const model: OscilloscopeModel = { out: liveFrame, in: idleFrame };
    drawOscilloscope(ctx, 300, 100, model);
    // guide line (1 stroke) + idle listener flat line (1 stroke) + live
    // assistant: min polyline (1 stroke) + max polyline (1 stroke) = 4.
    const strokeCount = calls.filter((c) => c.name === "stroke").length;
    expect(strokeCount).toBe(4);
  });

  it("fills the band between the min and max lines for a live trace, with a lower alpha than the stroke", () => {
    const { ctx, calls } = fakeContext();
    const alphasAtFill: number[] = [];
    const originalFill = ctx.fill;
    ctx.fill = () => {
      alphasAtFill.push(ctx.globalAlpha);
      originalFill();
    };
    const model: OscilloscopeModel = { out: liveFrame, in: idleFrame };
    drawOscilloscope(ctx, 300, 100, model);
    expect(calls.filter((c) => c.name === "fill")).toHaveLength(1);
    expect(alphasAtFill[0]).toBeLessThan(1);
    expect(alphasAtFill[0]).toBeGreaterThan(0);
  });

  it("the max-line points sit ABOVE center and the min-line points sit BELOW center for a positive/negative envelope", () => {
    const { ctx, calls } = fakeContext();
    const model: OscilloscopeModel = { out: liveFrame, in: idleFrame };
    drawOscilloscope(ctx, 300, 100, model);
    const moveAndLineTo = calls.filter((c) => c.name === "moveTo" || c.name === "lineTo");
    const ys = moveAndLineTo.map((c) => c.args[1] as number);
    // height=100, center=50. maxs are all positive -> y < 50 above center;
    // mins are all negative -> y > 50 below center. Both kinds of point
    // must appear (proves two distinct lines, not one folded line).
    expect(ys.some((y) => y < 50)).toBe(true);
    expect(ys.some((y) => y > 50)).toBe(true);
  });

  it("uses the assistant color for the out trace and the listener color for the in trace", () => {
    const { ctx, calls } = fakeContext();
    void calls;
    const strokeStyles: string[] = [];
    const originalStroke = ctx.stroke;
    ctx.stroke = () => {
      strokeStyles.push(ctx.strokeStyle);
      originalStroke();
    };
    const model: OscilloscopeModel = { out: liveFrame, in: liveFrame };
    drawOscilloscope(ctx, 300, 100, model);
    expect(strokeStyles).toContain(DEFAULT_OSCILLOSCOPE_COLORS.assistant);
    expect(strokeStyles).toContain(DEFAULT_OSCILLOSCOPE_COLORS.listener);
  });

  it("accepts a custom color set without throwing", () => {
    const { ctx } = fakeContext();
    const model: OscilloscopeModel = { out: liveFrame, in: idleFrame };
    expect(() =>
      drawOscilloscope(ctx, 300, 100, model, {
        background: "black",
        centerLine: "gray",
        assistant: "lime",
        listener: "red",
      }),
    ).not.toThrow();
  });

  it("guards against a malformed frame (mismatched min/max lengths) by treating it as idle rather than throwing", () => {
    const { ctx } = fakeContext();
    const malformed: TraceFrame = {
      isIdle: false,
      envelope: { mins: [-0.1, -0.2], maxs: [] },
      levelNorm: 0.1,
      direction: "out",
    };
    const model: OscilloscopeModel = { out: malformed, in: idleFrame };
    expect(() => drawOscilloscope(ctx, 300, 100, model)).not.toThrow();
  });
});

function fakeStyle(values: Record<string, string>): (el: Element) => { getPropertyValue(name: string): string } {
  return () => ({
    getPropertyValue: (name: string) => values[name] ?? "",
  });
}

describe("readOscilloscopeColors", () => {
  it("reads the assistant/listener/center-line colors from the injected computed style", () => {
    const reader = fakeStyle({
      [ASSISTANT_COLOR_TOKEN]: "#123456",
      [LISTENER_COLOR_TOKEN]: "#abcdef",
      [CENTER_LINE_COLOR_TOKEN]: "#999999",
    });
    const colors = readOscilloscopeColors({} as Element, reader);
    expect(colors.assistant).toBe("#123456");
    expect(colors.listener).toBe("#abcdef");
    expect(colors.centerLine).toBe("#999999");
  });

  it("falls back to the hardcoded default when a token resolves to an empty string", () => {
    const reader = fakeStyle({ [ASSISTANT_COLOR_TOKEN]: "" });
    const colors = readOscilloscopeColors({} as Element, reader);
    expect(colors.assistant).toBe(DEFAULT_OSCILLOSCOPE_COLORS.assistant);
  });

  it("falls back entirely when the style reader itself throws", () => {
    const reader = () => {
      throw new Error("no computed style available");
    };
    const colors = readOscilloscopeColors({} as Element, reader);
    expect(colors).toEqual(DEFAULT_OSCILLOSCOPE_COLORS);
  });

  it("never reads the background from a token (stays the CSS-driven default)", () => {
    const reader = fakeStyle({ "--bg": "#ff00ff" });
    const colors = readOscilloscopeColors({} as Element, reader);
    expect(colors.background).toBe(DEFAULT_OSCILLOSCOPE_COLORS.background);
  });

  it("trims whitespace a real getComputedStyle often returns around a custom property value", () => {
    const reader = fakeStyle({ [ASSISTANT_COLOR_TOKEN]: "  #0b655c  " });
    const colors = readOscilloscopeColors({} as Element, reader);
    expect(colors.assistant).toBe("#0b655c");
  });
});
