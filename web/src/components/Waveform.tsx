// components/Waveform.tsx
//
// Task t18: replaces the round-3 SVG bar chart with a canvas oscilloscope
// drawing TWO traces -- the assistant (`direction: "out"`) and the listener
// (`direction: "in"`) -- from the same `features` bus event kind, at
// animation-frame rate. Split deliberately across several files so the
// parts that can be tested without a canvas are (integrator note: "jsdom
// has no canvas ... test the pure events -> frame model function and keep
// the canvas thin"):
//
//   - `waveform/model.ts`  -- pure: features event -> live/idle trace frame
//                             (a min line and a max line per trace).
//   - `waveform/draw.ts`   -- thin: a frame -> 2D context calls, against an
//                             injectable `DrawableContext2D`, and the design-
//                             token color resolver.
//   - `waveform/hidpi.ts`  -- pure arithmetic: CSS size + DPR -> backing
//                             store size, plus the `ResizeObserver` seam.
//   - this file            -- wiring: owns the `<canvas>` element, the
//                             requestAnimationFrame loop (coalescing every
//                             `features` event that arrived since the last
//                             frame into whatever the NEXT frame draws,
//                             cancelling on unmount), the hi-DPI backing-
//                             store sizing (mount + ResizeObserver), the
//                             design-token color re-read (mount + resize),
//                             and the readouts.
//
// `AnalyserNode` only when the browser is the ear (t18 instruction): by
// default the listener trace is built from bus `features` events exactly
// like the assistant trace. `listenerAnalyser`, supplied by
// `audio/browserEar.ts` only while the browser mic is actually capturing,
// swaps the listener trace's SOURCE to a live `AnalyserNode` read each
// frame -- `data-listener-source` on the wrapper says, at all times, which
// one is actually driving what's on screen (C3: a degradation/mode must be
// observable, not merely correct under the hood).

import { useEffect, useRef, useState } from "react";
import type { EventEnvelope } from "../api/events";
import { Readouts } from "./Readouts";
import type { ComputedStyleReader, DrawableContext2D, OscilloscopeColors } from "../waveform/draw";
import { DEFAULT_OSCILLOSCOPE_COLORS, drawOscilloscope, readOscilloscopeColors } from "../waveform/draw";
import type { NormalizedEnvelope, TraceSample } from "../waveform/model";
import { DEFAULT_IDLE_AFTER_MS, oscilloscopeModel, sampleFromFeatures } from "../waveform/model";
import type { CssSize, ResizeObserverFactory } from "../waveform/hidpi";
import {
  computeBackingSize,
  defaultResizeObserverFactory,
  readDevicePixelRatio,
} from "../waveform/hidpi";

export { DEFAULT_IDLE_AFTER_MS };

/** Fallback CSS size used only before the canvas has ever been laid out
 *  (e.g. the very first `applySize()` call, or a `measureSize` that reports
 *  0x0 -- jsdom never runs layout at all, so `clientWidth`/`clientHeight`
 *  are 0 there by construction). Matches the stylesheet's own default
 *  `.waveform` box (`web/src/styles/app.css`). */
const FALLBACK_CSS_WIDTH = 640;
const FALLBACK_CSS_HEIGHT = 160;

/** A 2D context that can also scale for hi-DPI backing stores -- the real
 *  `CanvasRenderingContext2D` satisfies both this and `DrawableContext2D`
 *  for free; a test's fake context must implement `setTransform` too. */
export type ScalableContext2D = DrawableContext2D & {
  setTransform(a: number, b: number, c: number, d: number, e: number, f: number): void;
};

/** A live audio analyser this component reads once per animation frame --
 *  never owns or creates the `AnalyserNode` itself (that lives in
 *  `audio/browserEar.ts`, which owns the mic's AudioContext). Returns a
 *  min/max envelope in the SAME normalized shape `sampleFromFeatures`
 *  produces from the bus (one draw path serves both sources), or `null`
 *  when there is nothing to show yet (e.g. capture just started). */
export interface ListenerAnalyserSource {
  readTrace(): NormalizedEnvelope | null;
}

export interface RafScheduler {
  request(callback: FrameRequestCallback): number;
  cancel(id: number): void;
}

const defaultRaf: RafScheduler = {
  request: (cb) => window.requestAnimationFrame(cb),
  cancel: (id) => window.cancelAnimationFrame(id),
};

function defaultContextFactory(canvas: HTMLCanvasElement): ScalableContext2D | null {
  // Never throws (lesson 3): a browser without 2D canvas support, or a
  // test environment (jsdom has no canvas backend at all), degrades to "no
  // context" -- the draw loop below already treats that as a no-op rather
  // than a crash.
  try {
    return canvas.getContext("2d") as unknown as ScalableContext2D | null;
  } catch {
    return null;
  }
}

/** Defaults to the canvas's own laid-out CSS size. Under jsdom (no layout
 *  engine) this is always 0x0 -- `computeBackingSize` already floors that
 *  at a valid 1x1 backing store, and `FALLBACK_CSS_WIDTH`/`HEIGHT` are used
 *  as the LOGICAL drawing size in that case so the trace still has a
 *  sensible coordinate space to draw into. */
function defaultMeasureSize(canvas: HTMLCanvasElement): CssSize {
  const width = canvas.clientWidth || FALLBACK_CSS_WIDTH;
  const height = canvas.clientHeight || FALLBACK_CSS_HEIGHT;
  return { width, height };
}

export interface WaveformProps {
  features: EventEnvelope<"features"> | null;
  /** How long with no NEW `features` event before a trace reports itself
   *  idle again -- unchanged contract from round 3, now applied per-trace. */
  idleAfterMs?: number;
  /** Test seam: defaults to `canvas.getContext("2d")`. jsdom returns `null`
   *  here, which is why every canvas-behavior test injects a fake. */
  contextFactory?: (canvas: HTMLCanvasElement) => ScalableContext2D | null;
  /** Test seam: defaults to `window.requestAnimationFrame`. */
  raf?: RafScheduler;
  /** Test seam: defaults to `Date.now`. */
  nowFn?: () => number;
  /** Present only while the browser mic is capturing (t18 instruction:
   *  "AnalyserNode only when the browser is the ear"); `null` otherwise, in
   *  which case the listener trace comes from bus `features` events like
   *  the assistant trace. */
  listenerAnalyser?: ListenerAnalyserSource | null;
  /** Test seam (round 2): defaults to `canvas.clientWidth`/`clientHeight`. */
  measureSize?: (canvas: HTMLCanvasElement) => CssSize;
  /** Test seam (round 2): defaults to a real `ResizeObserver` when the
   *  global exists, a no-op otherwise (jsdom has none at all). */
  resizeObserverFactory?: ResizeObserverFactory;
  /** Test seam (round 2): defaults to `window.devicePixelRatio`. */
  devicePixelRatioFn?: () => number;
  /** Test seam (round 2): defaults to `window.getComputedStyle`. */
  styleReader?: ComputedStyleReader;
}

/**
 * The live oscilloscope centrepiece (operator decision carried over from
 * round 3, obligation o9): rendered directly from `features` events, never
 * a decorative animation with no data behind it. Two traces, one canvas,
 * redrawn every animation frame from whatever the model computes for
 * "right now" -- idle is recomputed on every frame purely from elapsed
 * time, which is what makes a stalled trace go flat on its own instead of
 * needing one more event to notice the silence.
 */
export function Waveform({
  features,
  idleAfterMs = DEFAULT_IDLE_AFTER_MS,
  contextFactory = defaultContextFactory,
  raf = defaultRaf,
  nowFn = Date.now,
  listenerAnalyser = null,
  measureSize = defaultMeasureSize,
  resizeObserverFactory = defaultResizeObserverFactory,
  devicePixelRatioFn = readDevicePixelRatio,
  styleReader,
}: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const outSampleRef = useRef<TraceSample | null>(null);
  const inSampleRef = useRef<TraceSample | null>(null);
  // Mirrors of the refs above, held in React state ONLY so the readouts
  // (rendered through ordinary React, not the imperative canvas loop) pick
  // up a new sample's fields even on a tick where outIdle/inIdle happen not
  // to flip -- a ref mutation alone triggers no re-render, and the readouts
  // must reflect every new event, not just the ones that also cross the
  // idle boundary.
  const [outSample, setOutSample] = useState<TraceSample | null>(null);
  const [inSample, setInSample] = useState<TraceSample | null>(null);

  // Route each incoming `features` event into the right slot as it arrives.
  // Deliberately separate from the draw loop below: many events can land
  // between two animation frames (a 15ms audio feature cadence against a
  // ~16.7ms frame budget easily produces more than one), and only the
  // LATEST sample per direction should ever reach the canvas -- storing
  // into a ref rather than dispatching a draw per event is what coalesces
  // them for free, with no explicit batching logic needed.
  useEffect(() => {
    if (!features) return;
    const sample = sampleFromFeatures(features, nowFn());
    if (!sample) return;
    if (sample.direction === "in") {
      inSampleRef.current = sample;
      setInSample(sample);
    } else {
      outSampleRef.current = sample;
      setOutSample(sample);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [features]);

  // Idle/live is exposed as component state ONLY for the wrapper's
  // data-waveform-state attribute (a cheap, test-observable summary of what
  // the canvas is currently drawing) -- the canvas itself is redrawn
  // imperatively inside the rAF loop below, never through a React render.
  const [outIdle, setOutIdle] = useState(true);
  const [inIdle, setInIdle] = useState(true);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = contextFactory(canvas);
    if (!ctx) return undefined;

    let cancelled = false;
    let frameId: number | null = null;

    // The logical (CSS-pixel) drawing size, re-derived every time the
    // canvas is resized -- draw calls always use THIS, never the backing
    // store's device-pixel dimensions, because `ctx.setTransform` below
    // already maps logical pixels to device pixels once per resize.
    let drawSize: CssSize = { width: FALLBACK_CSS_WIDTH, height: FALLBACK_CSS_HEIGHT };
    let colors: OscilloscopeColors = DEFAULT_OSCILLOSCOPE_COLORS;

    // Round 2 (coordinator, item 2): size the BACKING STORE from
    // `clientWidth * devicePixelRatio`, on mount and on every resize, and
    // scale the context so every draw call keeps using CSS-pixel
    // coordinates -- a canvas whose backing store matches its CSS box 1:1
    // renders blurry on any display denser than 1x. The CSS size itself is
    // untouched here (no `canvas.style.width/height` write): it stays
    // exactly what the stylesheet already gives `.waveform`.
    const applySize = () => {
      const cssSize = measureSize(canvas);
      drawSize = {
        width: cssSize.width || FALLBACK_CSS_WIDTH,
        height: cssSize.height || FALLBACK_CSS_HEIGHT,
      };
      const dpr = devicePixelRatioFn();
      const backing = computeBackingSize(drawSize, dpr);
      canvas.width = backing.width;
      canvas.height = backing.height;
      // setTransform (not scale()): idempotent across repeated resizes --
      // scale() would compound with whatever transform was already there.
      try {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      } catch {
        // A fake/degraded context without setTransform -- draw calls still
        // work, just against the raw backing-store pixel grid; never a
        // crash (lesson 3).
      }

      // Round 2 (coordinator, item 3): re-read the design-token colors on
      // every resize too, not only on mount -- a resize is the same signal
      // this component already reacts to, and re-reading here means a
      // stylesheet swap or a theme change that also reflows the page is
      // picked up without a separate observer.
      colors = readOscilloscopeColors(canvas, styleReader);
    };

    applySize();
    const resizeObserver = resizeObserverFactory(applySize);
    resizeObserver.observe(canvas);

    const tick = () => {
      if (cancelled) return;
      const now = nowFn();

      let inSample = inSampleRef.current;
      if (listenerAnalyser) {
        const trace = listenerAnalyser.readTrace();
        if (trace) {
          inSample = {
            envelope: trace,
            levelDb: 0,
            noiseFloorDb: null,
            zeroCrossingHz: null,
            resonanceHz: null,
            direction: "in",
            receivedAtMs: now,
          };
        }
      }

      const model = oscilloscopeModel(outSampleRef.current, inSample, now, idleAfterMs);
      drawOscilloscope(ctx, drawSize.width, drawSize.height, model, colors);

      setOutIdle((prev) => (prev === model.out.isIdle ? prev : model.out.isIdle));
      setInIdle((prev) => (prev === model.in.isIdle ? prev : model.in.isIdle));

      frameId = raf.request(tick);
    };

    frameId = raf.request(tick);

    return () => {
      cancelled = true;
      if (frameId !== null) raf.cancel(frameId);
      resizeObserver.disconnect();
    };
  }, [
    contextFactory,
    raf,
    nowFn,
    idleAfterMs,
    listenerAnalyser,
    measureSize,
    resizeObserverFactory,
    devicePixelRatioFn,
    styleReader,
  ]);

  return (
    <div
      className="waveform-wrap"
      data-waveform-state={outIdle ? "idle" : "live"}
      data-listener-idle={inIdle ? "idle" : "live"}
      data-listener-source={listenerAnalyser ? "analyser" : "bus"}
    >
      <canvas
        ref={canvasRef}
        className="waveform"
        role="img"
        aria-label="live assistant and listener audio oscilloscope"
        width={FALLBACK_CSS_WIDTH}
        height={FALLBACK_CSS_HEIGHT}
      />
      <div className="waveform__readouts-row">
        <Readouts sample={outSample} label="assistant" />
        <Readouts sample={inSample} label="listener" />
      </div>
    </div>
  );
}
