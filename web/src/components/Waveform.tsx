import { useEffect, useMemo, useState } from "react";
import type { EventEnvelope } from "../api/events";
import { decodeEnvelope } from "../audio/envelope";

export interface WaveformProps {
  features: EventEnvelope<"features"> | null;
  /** How long with no NEW `features` event before the waveform reports
   *  itself idle again (round 3: idle must mean "no signal", never "the
   *  last frame, frozen forever"). Overridable for tests; the operator
   *  decision on record ("live assistant waveform centrepiece") did not
   *  pin an exact number, so 500 ms is chosen, not measured. */
  idleAfterMs?: number;
}

const INT8_RANGE = 128;
/** The SVG's own coordinate space is unit-independent of pixels — CSS gives
 *  it its real on-screen size (see .waveform in app.css: width 100%,
 *  min-height >= 160px on desktop) and `preserveAspectRatio="none"` lets
 *  that box stretch the viewBox non-uniformly, so the bars always fill the
 *  full card width regardless of how many buckets a frame carries. */
const VIEWBOX_HEIGHT = 100;
const BAR_WIDTH_FRACTION = 0.72;
/** dBFS floor a level fill normalizes against — embodiment/audio/features.py's
 *  own FLOOR_DB (module docstring: "-96 dB is the conventional 16-bit noise
 *  floor"). Duplicated as a literal here rather than imported: importing a
 *  Python module's constant into TS isn't possible, and this dashboard has
 *  no other reason to depend on audio/features.py's internals. */
const LEVEL_FLOOR_DB = -96;
export const DEFAULT_IDLE_AFTER_MS = 500;

function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/**
 * The live assistant waveform is the centrepiece (operator decision, this
 * task's integrator notes, obligation o9): rendered directly from the
 * `features` event's min/max envelope + level, NEVER a decorative
 * animation with no data behind it — round 3 makes that literal: bars are
 * drawn MIRRORED (symmetric above/below center, the conventional
 * audio-visualizer look) from each bucket's peak magnitude, and a subtle
 * background fill tracks `level_db`.
 *
 * Idle is its own state, distinct from "the last frame, frozen": if no NEW
 * `features` event has arrived for `idleAfterMs`, this renders the flat
 * idle line again even though `features` (the prop) hasn't changed — a
 * stalled feed must look stalled, not like audio is still playing.
 */
export function Waveform({ features, idleAfterMs = DEFAULT_IDLE_AFTER_MS }: WaveformProps) {
  const [idleTimedOut, setIdleTimedOut] = useState(true);

  useEffect(() => {
    if (!features) {
      setIdleTimedOut(true);
      return;
    }
    setIdleTimedOut(false);
    const timer = setTimeout(() => setIdleTimedOut(true), idleAfterMs);
    return () => clearTimeout(timer);
  }, [features, idleAfterMs]);

  const envelope = useMemo(() => {
    const env = features?.data.env;
    if (typeof env !== "string" || env.length === 0) return null;
    return decodeEnvelope(env);
  }, [features]);

  const isIdle = idleTimedOut || !features || !envelope || envelope.mins.length === 0;

  if (isIdle) {
    return (
      <svg
        className="waveform"
        viewBox={`0 0 100 ${VIEWBOX_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="no live audio -- idle"
        data-waveform-state="idle"
      >
        <line
          x1="0"
          y1={VIEWBOX_HEIGHT / 2}
          x2="100"
          y2={VIEWBOX_HEIGHT / 2}
          stroke="var(--line)"
          strokeWidth="1"
        />
      </svg>
    );
  }

  const direction = String(features!.data.direction ?? "?");
  const levelDb = Number(features!.data.level_db ?? LEVEL_FLOOR_DB);
  const levelNorm = clamp01((levelDb - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB);
  const bucketCount = envelope!.mins.length;

  const bars = envelope!.mins.map((min, i) => {
    const max = envelope!.maxs[i] ?? min;
    // Mirrored: each bar's half-height comes from the bucket's PEAK
    // magnitude (whichever of min/max is further from silence), drawn
    // symmetrically above and below the center line -- the conventional
    // mirrored-bar visualizer, rather than the raw (possibly asymmetric)
    // min..max span.
    const peak = Math.max(Math.abs(min), Math.abs(max));
    const halfHeight = Math.max(0.5, (peak / INT8_RANGE) * (VIEWBOX_HEIGHT / 2));
    const x = i + (1 - BAR_WIDTH_FRACTION) / 2;
    return (
      <rect
        key={i}
        className="waveform__bar"
        x={x}
        y={VIEWBOX_HEIGHT / 2 - halfHeight}
        width={BAR_WIDTH_FRACTION}
        height={halfHeight * 2}
        fill="var(--accent)"
      />
    );
  });

  return (
    <svg
      className="waveform"
      viewBox={`0 0 ${bucketCount} ${VIEWBOX_HEIGHT}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`live ${direction} audio waveform, level ${features!.data.level_db} dB`}
      data-waveform-state="live"
      data-direction={direction}
    >
      {/* level_db as a subtle fill, never the centrepiece itself -- a
          low-opacity band rising from the baseline like a VU meter. */}
      <rect
        className="waveform__level-fill"
        x="0"
        y={VIEWBOX_HEIGHT * (1 - levelNorm)}
        width={bucketCount}
        height={VIEWBOX_HEIGHT * levelNorm}
        fill="var(--accent)"
        opacity="0.12"
      />
      <line
        x1="0"
        y1={VIEWBOX_HEIGHT / 2}
        x2={bucketCount}
        y2={VIEWBOX_HEIGHT / 2}
        stroke="var(--line)"
        strokeWidth="0.3"
      />
      {bars}
    </svg>
  );
}
