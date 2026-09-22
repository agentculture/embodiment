// waveform/model.ts
//
// The pure "events -> frame model" half of the canvas oscilloscope (task
// t18's integrator note: "test the pure events -> frame model function and
// keep the canvas thin" -- jsdom has no canvas, so every fact this task's
// acceptance criteria assert about MOTION and IDLE-vs-FROZEN is proven here,
// against plain data, with no DOM and no canvas anywhere in this file).
//
// Two independent traces share one shape: the assistant (`direction: "out"`)
// and the listener (`direction: "in"`), each built from the SAME `features`
// event kind (t18's instruction), each with its OWN idle timeout -- a stalled
// assistant trace must not borrow liveness from a chatty listener trace or
// vice versa (mirrors Waveform.tsx round 3's per-signal idle rule, now
// applied to two signals instead of one).
//
// Round 2 correction (coordinator's live-Chrome review): a bucket's min and
// max were previously collapsed into one "peak magnitude" number and drawn
// as ONE polyline -- which reads as a sawtooth, not an oscilloscope trace,
// because it alternates between a bucket's floor and ceiling with no
// relation to which is which. `features.py`'s own envelope is a MIN line
// and a MAX line; `NormalizedEnvelope` now carries both, separately,
// normalized to [-1, 1] (int8 range, signed) rather than collapsed to a
// single [0, 1] magnitude.

import { decodeEnvelope } from "../audio/envelope";
import type { EventEnvelope, FeaturesData } from "../api/events";

export const INT8_RANGE = 128;

/** Mirrors Waveform.tsx's own idle default -- unchanged by this task, kept
 *  as one named constant rather than two copies drifting apart. */
export const DEFAULT_IDLE_AFTER_MS = 500;

/**
 * A `FeaturesData` field the schema does not carry today
 * (`tests/fixtures/events/schema.json`'s `features` kind has no `resonance_hz`
 * -- see this task's integrator notes). Read defensively via an index
 * signature rather than widening `FeaturesData` itself, so a future field
 * named exactly this is picked up with no change here, and anything else
 * present on the object is ignored rather than misread.
 */
const RESONANCE_FIELD = "resonance_hz";

/** The min line and the max line of one bucketed envelope, each value
 *  normalized to [-1, 1] -- what `features.py`'s min/max envelope actually
 *  means (two bounds per bucket, not one collapsed magnitude). Both arrays
 *  are always the same length. */
export interface NormalizedEnvelope {
  mins: number[];
  maxs: number[];
}

export const EMPTY_ENVELOPE: NormalizedEnvelope = { mins: [], maxs: [] };

/** One decoded `features` event, still carrying the fields a readout needs --
 *  distinct from `TraceFrame` (below), which is what the CANVAS draws. */
export interface TraceSample {
  envelope: NormalizedEnvelope;
  levelDb: number;
  noiseFloorDb: number | null;
  zeroCrossingHz: number | null;
  /** Not on the schema today (see `RESONANCE_FIELD`'s comment); carried
   *  through so a future field is rendered by field presence alone, with no
   *  change to this module. */
  resonanceHz: number | null;
  direction: string;
  /** Wall-clock ms this sample was decoded, per the caller's own clock --
   *  never `Date.now()` internally, so idle timing is deterministic under
   *  fake timers. */
  receivedAtMs: number;
}

/** What the canvas actually draws for one trace on one animation frame. */
export interface TraceFrame {
  isIdle: boolean;
  /** The min/max envelope to draw as two polylines with a filled band
   *  between them. Both arrays empty while idle -- idle is its own state,
   *  never the last live frame redrawn (see `traceFrame`'s own doc). */
  envelope: NormalizedEnvelope;
  levelNorm: number;
  direction: string | null;
}

function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function clampSigned(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(1, Math.max(-1, value));
}

/** dBFS floor a level normalizes against -- the same constant Waveform.tsx's
 *  SVG predecessor used, and for the identical reason (features.py's own
 *  FLOOR_DB, -96 dB). */
export const LEVEL_FLOOR_DB = -96;

function readOptionalNumber(data: FeaturesData, key: string): number | null {
  const raw = (data as Record<string, unknown>)[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

/**
 * Decode one `features` event into a `TraceSample`, or `null` if its `env`
 * is absent/undecodable (lesson 3: degrade, never throw -- an undecodable
 * envelope is treated exactly like "no event arrived", which is what the
 * SVG predecessor did too).
 */
export function sampleFromFeatures(
  envelope: EventEnvelope<"features">,
  nowMs: number,
): TraceSample | null {
  // `EventEnvelope<K>.data` is typed as the generic `EventEnvelopeData` at
  // the envelope level (see api/events.ts) -- narrowing to `FeaturesData` is
  // a caller responsibility for whichever kind it knows it decoded, exactly
  // like the retired SVG Waveform's own `features!.data` reads did.
  const data = envelope.data as FeaturesData;
  const env = data.env;
  if (typeof env !== "string" || env.length === 0) return null;
  const decoded = decodeEnvelope(env);
  if (!decoded || decoded.mins.length === 0) return null;

  return {
    envelope: {
      mins: decoded.mins.map((v) => clampSigned(v / INT8_RANGE)),
      maxs: decoded.maxs.map((v) => clampSigned(v / INT8_RANGE)),
    },
    levelDb: Number(data.level_db ?? LEVEL_FLOOR_DB),
    noiseFloorDb: readOptionalNumber(data, "noise_floor_db"),
    zeroCrossingHz: readOptionalNumber(data, "zero_crossing_hz"),
    resonanceHz: readOptionalNumber(data, RESONANCE_FIELD),
    direction: String(data.direction ?? "?"),
    receivedAtMs: nowMs,
  };
}

/**
 * The frame one trace draws right now: live (moving with the sample) while
 * `nowMs` is within `idleAfterMs` of the sample's own arrival, idle (flat,
 * empty envelope) once it is not -- evaluated fresh on EVERY call against
 * the caller's current clock, never cached. This is what makes idle its own
 * state rather than a frozen last frame: an oscilloscope re-evaluating this
 * on every animation frame goes flat exactly `idleAfterMs` after the last
 * sample, with no new event required to notice the silence (t18 acceptance
 * criterion 1's own wording: "goes flat, not frozen, when the stream
 * stops").
 */
export function traceFrame(
  sample: TraceSample | null,
  nowMs: number,
  idleAfterMs: number = DEFAULT_IDLE_AFTER_MS,
): TraceFrame {
  if (!sample || nowMs - sample.receivedAtMs >= idleAfterMs) {
    return {
      isIdle: true,
      envelope: EMPTY_ENVELOPE,
      levelNorm: 0,
      direction: sample?.direction ?? null,
    };
  }
  return {
    isIdle: false,
    envelope: sample.envelope,
    levelNorm: clamp01((sample.levelDb - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB),
    direction: sample.direction,
  };
}

export interface OscilloscopeModel {
  out: TraceFrame;
  in: TraceFrame;
}

/**
 * Both traces' frames for one animation-frame tick. `outSample`/`inSample`
 * are whatever `sampleFromFeatures` last produced for each direction --
 * routing an incoming event to the right slot is the CALLER's job (it needs
 * React state to remember "the last `out` sample" across renders, which this
 * pure module deliberately has none of).
 */
export function oscilloscopeModel(
  outSample: TraceSample | null,
  inSample: TraceSample | null,
  nowMs: number,
  idleAfterMs: number = DEFAULT_IDLE_AFTER_MS,
): OscilloscopeModel {
  return {
    out: traceFrame(outSample, nowMs, idleAfterMs),
    in: traceFrame(inSample, nowMs, idleAfterMs),
  };
}
