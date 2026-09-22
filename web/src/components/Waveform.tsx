import { useMemo } from "react";
import type { EventEnvelope } from "../api/events";
import { decodeEnvelope } from "../audio/envelope";

export interface WaveformProps {
  features: EventEnvelope<"features"> | null;
}

const INT8_RANGE = 128;
const BAR_WIDTH = 4;
const BAR_GAP = 1;
const HEIGHT = 64;

/**
 * The live assistant waveform is the centrepiece (operator decision, this
 * task's integrator notes, obligation o9): rendered directly from the
 * `features` event's min/max envelope + level, NEVER a decorative
 * animation with no data behind it. With no `features` event yet, this
 * renders a flat, explicitly-labelled placeholder rather than a fake
 * moving bar — an idle waveform must look idle, not alive.
 */
export function Waveform({ features }: WaveformProps) {
  const envelope = useMemo(() => {
    const env = features?.data.env;
    if (typeof env !== "string" || env.length === 0) return null;
    return decodeEnvelope(env);
  }, [features]);

  if (!features || !envelope || envelope.mins.length === 0) {
    return (
      <svg
        className="waveform"
        viewBox={`0 0 100 ${HEIGHT}`}
        role="img"
        aria-label="no audio features received yet"
        data-waveform-state="idle"
      >
        <line x1="0" y1={HEIGHT / 2} x2="100" y2={HEIGHT / 2} stroke="var(--line)" strokeWidth="1" />
      </svg>
    );
  }

  const direction = String(features.data.direction ?? "?");
  const bars = envelope.mins.map((min, i) => {
    const max = envelope.maxs[i] ?? min;
    const yMin = HEIGHT / 2 - (max / INT8_RANGE) * (HEIGHT / 2);
    const yMax = HEIGHT / 2 - (min / INT8_RANGE) * (HEIGHT / 2);
    const x = i * (BAR_WIDTH + BAR_GAP);
    return (
      <rect
        key={i}
        x={x}
        y={Math.min(yMin, yMax)}
        width={BAR_WIDTH}
        height={Math.max(1, Math.abs(yMax - yMin))}
        fill="var(--accent)"
      />
    );
  });
  const width = envelope.mins.length * (BAR_WIDTH + BAR_GAP);

  return (
    <svg
      className="waveform"
      viewBox={`0 0 ${width} ${HEIGHT}`}
      role="img"
      aria-label={`live ${direction} audio waveform, level ${features.data.level_db} dB`}
      data-waveform-state="live"
      data-direction={direction}
    >
      {bars}
    </svg>
  );
}
