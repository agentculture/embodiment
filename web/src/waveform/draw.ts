// waveform/draw.ts
//
// The thin canvas half of the oscilloscope (task t18's integrator note:
// "jsdom has no canvas: inject a 2D-context factory so tests assert the
// draw calls ... keep the canvas thin"). Every DECISION about what the
// traces look like lives in `model.ts` (pure, no DOM); this file only turns
// an already-computed `OscilloscopeModel` into a sequence of 2D context
// calls. No timing, no state, no idle logic here -- `drawOscilloscope` draws
// exactly the model it is handed, every call, deterministically.

import type { OscilloscopeModel, TraceFrame } from "./model";

/**
 * The subset of `CanvasRenderingContext2D` this module calls. Declared
 * narrowly (rather than importing the DOM lib type) so a test's fake context
 * only has to implement what drawing an oscilloscope actually uses, and a
 * real `<canvas>`'s `getContext("2d")` return value satisfies it for free
 * (every member below is also on the real interface).
 */
export interface DrawableContext2D {
  clearRect(x: number, y: number, w: number, h: number): void;
  beginPath(): void;
  moveTo(x: number, y: number): void;
  lineTo(x: number, y: number): void;
  stroke(): void;
  fillRect(x: number, y: number, w: number, h: number): void;
  strokeStyle: string;
  fillStyle: string;
  lineWidth: number;
  globalAlpha: number;
}

export interface OscilloscopeColors {
  background: string;
  centerLine: string;
  assistant: string;
  listener: string;
}

/** Chosen (not measured) to read as "assistant" vs "listener" against the
 *  culture-design tokens without importing CSS custom properties into a
 *  canvas 2D context (which cannot resolve `var(--accent)` itself). Kept as
 *  one named constant, overridable by a caller that wants the real resolved
 *  token value (see `Waveform.tsx`, which reads it from computed style). */
export const DEFAULT_OSCILLOSCOPE_COLORS: OscilloscopeColors = {
  background: "transparent",
  centerLine: "rgba(148, 148, 148, 0.4)",
  assistant: "#5b8def",
  listener: "#e08a3c",
};

function drawTrace(
  ctx: DrawableContext2D,
  frame: TraceFrame,
  width: number,
  height: number,
  color: string,
): void {
  const centerY = height / 2;
  if (frame.isIdle || frame.bars.length === 0) {
    // Idle is drawn as a flat line at center -- never the last live trace
    // redrawn (model.ts already guarantees `bars` is empty; this is the
    // canvas honoring that, not re-deciding it).
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.35;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, centerY);
    ctx.lineTo(width, centerY);
    ctx.stroke();
    ctx.globalAlpha = 1;
    return;
  }

  const bucketCount = frame.bars.length;
  const bucketWidth = width / bucketCount;
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.9;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  // A classic mirrored-oscilloscope polyline: each bucket contributes a
  // peak-above and a peak-below point, so the trace oscillates around the
  // center line the way a real scope trace does, rather than a flat top of
  // bars (which is what the retired SVG bar-chart drew).
  frame.bars.forEach((peak, i) => {
    const x = (i + 0.5) * bucketWidth;
    const half = peak * (height / 2);
    const above = centerY - half;
    const below = centerY + half;
    if (i === 0) {
      ctx.moveTo(x, above);
    } else {
      ctx.lineTo(x, above);
    }
    ctx.lineTo(x, below);
  });
  ctx.stroke();
  ctx.globalAlpha = 1;
}

/**
 * Draw both traces (assistant `out`, listener `in`) for one animation
 * frame. Clears the canvas first -- this function owns the whole canvas,
 * always redrawing it fully rather than accumulating strokes across calls
 * (an accumulating canvas would make "goes flat" invisible: the last live
 * trace would stay burned in under a new flat line drawn on top at low
 * alpha).
 */
export function drawOscilloscope(
  ctx: DrawableContext2D,
  width: number,
  height: number,
  model: OscilloscopeModel,
  colors: OscilloscopeColors = DEFAULT_OSCILLOSCOPE_COLORS,
): void {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = colors.background;
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = colors.centerLine;
  ctx.lineWidth = 0.5;
  ctx.globalAlpha = 1;
  ctx.beginPath();
  ctx.moveTo(0, height / 2);
  ctx.lineTo(width, height / 2);
  ctx.stroke();

  drawTrace(ctx, model.in, width, height, colors.listener);
  drawTrace(ctx, model.out, width, height, colors.assistant);
}
