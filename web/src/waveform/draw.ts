// waveform/draw.ts
//
// The thin canvas half of the oscilloscope (task t18's integrator note:
// "jsdom has no canvas: inject a 2D-context factory so tests assert the
// draw calls ... keep the canvas thin"). Every DECISION about what the
// traces look like lives in `model.ts` (pure, no DOM); this file only turns
// an already-computed `OscilloscopeModel` into a sequence of 2D context
// calls. No timing, no state, no idle logic here -- `drawOscilloscope` draws
// exactly the model it is handed, every call, deterministically.
//
// Round 2 correction (coordinator's live-Chrome review): drawing a
// per-bucket peak magnitude as ONE polyline reads as a sawtooth, not a
// scope trace -- it alternates between the bucket's floor and ceiling with
// no meaning attached to which is which. `features.py`'s min/max envelope
// is a MIN line and a MAX line; this module now draws both, per direction,
// with the band between them filled at low opacity (the conventional
// "envelope follower" look).

import type { NormalizedEnvelope, OscilloscopeModel, TraceFrame } from "./model";

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
  closePath(): void;
  stroke(): void;
  fill(): void;
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
 *  canvas 2D context (which cannot resolve `var(--accent)` itself). This is
 *  the FALLBACK only -- `readOscilloscopeColors` below resolves the real
 *  design-token values at draw time and only falls back to these when a
 *  token is empty/unavailable (e.g. `getComputedStyle` under jsdom, or a
 *  page whose stylesheet hasn't loaded yet). */
export const DEFAULT_OSCILLOSCOPE_COLORS: OscilloscopeColors = {
  background: "transparent",
  centerLine: "rgba(148, 148, 148, 0.4)",
  assistant: "#5b8def",
  listener: "#e08a3c",
};

/** The minimal `getComputedStyle` surface `readOscilloscopeColors` needs --
 *  injectable so a test can assert against fixed values without depending
 *  on jsdom's own CSS custom-property resolution (which does not evaluate
 *  `@media` blocks or resolve `var()` the way a real browser does). */
export interface ComputedStyleReader {
  (element: Element): { getPropertyValue(name: string): string };
}

const defaultStyleReader: ComputedStyleReader = (element) =>
  window.getComputedStyle(element);

/** The `tokens.css` custom properties each trace's color reads from
 *  (`web/src/culture-design/tokens.css`, the pinned org design system --
 *  there is no `--fg-muted`; the closest named token for a muted/secondary
 *  color is `--ink-soft`). `--line` is the same faint guide-line color the
 *  panel borders already use. */
export const ASSISTANT_COLOR_TOKEN = "--accent";
export const LISTENER_COLOR_TOKEN = "--ink-soft";
export const CENTER_LINE_COLOR_TOKEN = "--line";

/**
 * Resolve the oscilloscope's colors from the design tokens actually applied
 * to `element` (its computed style, which resolves `var()` and whichever
 * `@media (prefers-color-scheme)` block is active) -- read ONCE per call,
 * never cached inside this function, so the caller controls when a
 * re-theme (or a resize, which is when `Waveform.tsx` re-reads this) is
 * noticed. Never throws (lesson 3): a `styleReader` that throws, or a token
 * that resolves to an empty string (no stylesheet loaded yet, or the
 * element isn't in a document at all -- both true under jsdom without a
 * real layout/style engine), degrades to `fallback` for that one color,
 * never the whole call.
 */
export function readOscilloscopeColors(
  element: Element,
  styleReader: ComputedStyleReader = defaultStyleReader,
  fallback: OscilloscopeColors = DEFAULT_OSCILLOSCOPE_COLORS,
): OscilloscopeColors {
  let style: { getPropertyValue(name: string): string };
  try {
    style = styleReader(element);
  } catch {
    return fallback;
  }

  const read = (token: string, fallbackValue: string): string => {
    try {
      const value = style.getPropertyValue(token).trim();
      return value.length > 0 ? value : fallbackValue;
    } catch {
      return fallbackValue;
    }
  };

  return {
    // Background is intentionally never read from a token: the canvas is
    // left transparent so the panel's own `background: var(--bg)` (already
    // applied via CSS on `.waveform`) shows through -- one source of truth
    // for the backdrop, not two.
    background: fallback.background,
    centerLine: read(CENTER_LINE_COLOR_TOKEN, fallback.centerLine),
    assistant: read(ASSISTANT_COLOR_TOKEN, fallback.assistant),
    listener: read(LISTENER_COLOR_TOKEN, fallback.listener),
  };
}

function tracePoints(
  values: number[],
  width: number,
  height: number,
): { x: number; y: number }[] {
  const centerY = height / 2;
  const bucketWidth = width / values.length;
  return values.map((value, i) => ({
    x: (i + 0.5) * bucketWidth,
    y: centerY - value * (height / 2),
  }));
}

function strokePolyline(ctx: DrawableContext2D, points: { x: number; y: number }[]): void {
  ctx.beginPath();
  points.forEach((p, i) => {
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
}

function drawTrace(
  ctx: DrawableContext2D,
  frame: TraceFrame,
  width: number,
  height: number,
  color: string,
): void {
  const centerY = height / 2;
  const envelope: NormalizedEnvelope = frame.envelope;
  if (frame.isIdle || envelope.mins.length === 0 || envelope.maxs.length === 0) {
    // Idle is drawn as a flat line at center -- never the last live trace
    // redrawn (model.ts already guarantees the envelope is empty; this is
    // the canvas honoring that, not re-deciding it).
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.35;
    ctx.lineWidth = 1;
    strokePolyline(ctx, [
      { x: 0, y: centerY },
      { x: width, y: centerY },
    ]);
    ctx.globalAlpha = 1;
    return;
  }

  const topPoints = tracePoints(envelope.maxs, width, height); // above center
  const bottomPoints = tracePoints(envelope.mins, width, height); // below center

  // The filled band between the min line and the max line -- what makes
  // this read as an envelope rather than two unrelated lines. Path: forward
  // along the max line, then backward along the min line, closed.
  ctx.beginPath();
  topPoints.forEach((p, i) => {
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  for (let i = bottomPoints.length - 1; i >= 0; i -= 1) {
    ctx.lineTo(bottomPoints[i].x, bottomPoints[i].y);
  }
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.18;
  ctx.fill();
  ctx.globalAlpha = 1;

  // The two polylines themselves, drawn on top of the fill.
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.9;
  ctx.lineWidth = 1.25;
  strokePolyline(ctx, topPoints);
  strokePolyline(ctx, bottomPoints);
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
  strokePolyline(ctx, [
    { x: 0, y: height / 2 },
    { x: width, y: height / 2 },
  ]);

  drawTrace(ctx, model.in, width, height, colors.listener);
  drawTrace(ctx, model.out, width, height, colors.assistant);
}
