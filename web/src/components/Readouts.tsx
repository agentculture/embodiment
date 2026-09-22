// components/Readouts.tsx
//
// Task t18 acceptance criterion 3 (verbatim): "pitch, resonance and
// noise-floor readouts render when present and are absent, not zero, when
// not." This task's integrator notes translate that against the real
// `features` schema (`tests/fixtures/events/schema.json`): there is no
// pitch or resonance field today, so the readouts are keyed by FIELD
// PRESENCE on the decoded sample --  `zero_crossing_hz` is the pitch
// readout, `noise_floor_db` is the noise-floor readout, and a `resonance`
// readout renders only if a future `resonance_hz` field is present. A
// missing field renders NOTHING for that readout -- never `0` and never a
// placeholder dash, which would look like a real (if quiet) measurement.

import type { TraceSample } from "../waveform/model";

export interface ReadoutsProps {
  sample: TraceSample | null;
  /** Distinguishes the assistant vs listener readout blocks in the DOM
   *  (aria-label, data attribute) -- purely descriptive, never affects which
   *  fields render. */
  label: string;
}

function round(value: number): number {
  return Math.round(value);
}

/**
 * Renders `null` (nothing) when `sample` is null, OR when every one of the
 * three fields it could show is absent -- an empty `<dl>` would be a real
 * DOM node with no content, which is not "absent" for anything querying the
 * DOM for these readouts.
 */
export function Readouts({ sample, label }: ReadoutsProps) {
  if (!sample) return null;

  const hasPitch = sample.zeroCrossingHz != null;
  const hasNoiseFloor = sample.noiseFloorDb != null;
  const hasResonance = sample.resonanceHz != null;
  if (!hasPitch && !hasNoiseFloor && !hasResonance) return null;

  return (
    <dl
      className="waveform__readouts"
      data-readouts-for={label}
      aria-label={`${label} audio readouts`}
    >
      {hasPitch && (
        <div className="waveform__readout" data-readout="pitch">
          <dt>pitch</dt>
          <dd>{round(sample.zeroCrossingHz as number)} Hz</dd>
        </div>
      )}
      {hasNoiseFloor && (
        <div className="waveform__readout" data-readout="noise-floor">
          <dt>noise floor</dt>
          <dd>{sample.noiseFloorDb} dB</dd>
        </div>
      )}
      {hasResonance && (
        <div className="waveform__readout" data-readout="resonance">
          <dt>resonance</dt>
          <dd>{round(sample.resonanceHz as number)} Hz</dd>
        </div>
      )}
    </dl>
  );
}
