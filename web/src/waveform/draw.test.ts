// waveform/draw.test.ts
//
// jsdom has no canvas (task t18's integrator note), so this pins the
// draw calls against a FAKE 2D context that records every call -- never a
// real canvas. Proves the "thin" half: given a model, drawOscilloscope
// issues the calls a real CanvasRenderingContext2D would render correctly,
// and nothing here decides idle/live -- that is `model.ts`'s job, already
// proven in `model.test.ts`.

import { describe, expect, it } from "vitest";
import { DEFAULT_OSCILLOSCOPE_COLORS, drawOscilloscope } from "./draw";
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
    stroke: (...args: unknown[]) => calls.push({ name: "stroke", args }),
    fillRect: (...args: unknown[]) => calls.push({ name: "fillRect", args }),
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 0,
    globalAlpha: 1,
  } as DrawableContext2D;
  return { ctx, calls };
}

const idleFrame: TraceFrame = { isIdle: true, bars: [], levelNorm: 0, direction: null };
const liveFrame: TraceFrame = {
  isIdle: false,
  bars: [0.1, 0.5, 1, 0.2],
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
    // center guide line + two idle-flat traces = 3 beginPath/stroke pairs.
    const strokeCount = calls.filter((c) => c.name === "stroke").length;
    expect(strokeCount).toBe(3);
    const lineToCalls = calls.filter((c) => c.name === "lineTo");
    // Each flat/guide line is exactly one lineTo (a single segment).
    expect(lineToCalls).toHaveLength(3);
  });

  it("draws one polyline point per bucket for a live trace (more lineTo calls than an idle one)", () => {
    const { ctx, calls } = fakeContext();
    const model: OscilloscopeModel = { out: liveFrame, in: idleFrame };
    drawOscilloscope(ctx, 300, 100, model);
    const lineToCalls = calls.filter((c) => c.name === "lineTo");
    // guide line (1) + idle listener flat line (1) + live assistant trace
    // (2 points per bucket: above + below, first point uses moveTo instead
    // of lineTo) = 1 + 1 + (4 buckets * 2 - 1) = 9.
    expect(lineToCalls.length).toBe(9);
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
});
