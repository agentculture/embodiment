// components/Waveform.tsx
//
// Task t18: replaces the round-3 SVG bar chart with a canvas oscilloscope
// drawing TWO traces -- the assistant (`direction: "out"`) and the listener
// (`direction: "in"`) -- from the same `features` bus event kind, at
// animation-frame rate. Split deliberately across three files so the parts
// that can be tested without a canvas are (integrator note: "jsdom has no
// canvas ... test the pure events -> frame model function and keep the
// canvas thin"):
//
//   - `waveform/model.ts`  -- pure: features event -> live/idle trace frame.
//   - `waveform/draw.ts`   -- thin: a frame -> 2D context calls, against an
//                             injectable `DrawableContext2D` so a test can
//                             assert the calls without a real canvas.
//   - this file            -- wiring: owns the `<canvas>` element, the
//                             requestAnimationFrame loop (coalescing every
//                             `features` event that arrived since the last
//                             frame into whatever the NEXT frame draws,
//                             cancelling on unmount), and the readouts.
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
import type { DrawableContext2D } from "../waveform/draw";
import { drawOscilloscope } from "../waveform/draw";
import type { TraceSample } from "../waveform/model";
import { DEFAULT_IDLE_AFTER_MS, oscilloscopeModel, sampleFromFeatures } from "../waveform/model";

export { DEFAULT_IDLE_AFTER_MS };

const CANVAS_WIDTH = 640;
const CANVAS_HEIGHT = 160;

/** A live audio analyser this component reads once per animation frame --
 *  never owns or creates the `AnalyserNode` itself (that lives in
 *  `audio/browserEar.ts`, which owns the mic's AudioContext). Returns
 *  per-bucket magnitudes already normalized to [0, 1], the same shape
 *  `sampleFromFeatures` produces from the bus, or `null` when there is
 *  nothing to show yet (e.g. capture just started). */
export interface ListenerAnalyserSource {
  readTrace(): number[] | null;
}

export interface RafScheduler {
  request(callback: FrameRequestCallback): number;
  cancel(id: number): void;
}

const defaultRaf: RafScheduler = {
  request: (cb) => window.requestAnimationFrame(cb),
  cancel: (id) => window.cancelAnimationFrame(id),
};

function defaultContextFactory(canvas: HTMLCanvasElement): DrawableContext2D | null {
  // Never throws (lesson 3): a browser without 2D canvas support, or a
  // test environment (jsdom has no canvas backend at all), degrades to "no
  // context" -- the draw loop below already treats that as a no-op rather
  // than a crash.
  try {
    return canvas.getContext("2d") as unknown as DrawableContext2D | null;
  } catch {
    return null;
  }
}

export interface WaveformProps {
  features: EventEnvelope<"features"> | null;
  /** How long with no NEW `features` event before a trace reports itself
   *  idle again -- unchanged contract from round 3, now applied per-trace. */
  idleAfterMs?: number;
  /** Test seam: defaults to `canvas.getContext("2d")`. jsdom returns `null`
   *  here, which is why every canvas-behavior test injects a fake. */
  contextFactory?: (canvas: HTMLCanvasElement) => DrawableContext2D | null;
  /** Test seam: defaults to `window.requestAnimationFrame`. */
  raf?: RafScheduler;
  /** Test seam: defaults to `Date.now`. */
  nowFn?: () => number;
  /** Present only while the browser mic is capturing (t18 instruction:
   *  "AnalyserNode only when the browser is the ear"); `null` otherwise, in
   *  which case the listener trace comes from bus `features` events like
   *  the assistant trace. */
  listenerAnalyser?: ListenerAnalyserSource | null;
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

    const tick = () => {
      if (cancelled) return;
      const now = nowFn();

      let inSample = inSampleRef.current;
      if (listenerAnalyser) {
        const trace = listenerAnalyser.readTrace();
        if (trace) {
          inSample = {
            bars: trace,
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
      drawOscilloscope(ctx, CANVAS_WIDTH, CANVAS_HEIGHT, model);

      setOutIdle((prev) => (prev === model.out.isIdle ? prev : model.out.isIdle));
      setInIdle((prev) => (prev === model.in.isIdle ? prev : model.in.isIdle));

      frameId = raf.request(tick);
    };

    frameId = raf.request(tick);

    return () => {
      cancelled = true;
      if (frameId !== null) raf.cancel(frameId);
    };
  }, [contextFactory, raf, nowFn, idleAfterMs, listenerAnalyser]);

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
        width={CANVAS_WIDTH}
        height={CANVAS_HEIGHT}
      />
      <div className="waveform__readouts-row">
        <Readouts sample={outSample} label="assistant" />
        <Readouts sample={inSample} label="listener" />
      </div>
    </div>
  );
}
