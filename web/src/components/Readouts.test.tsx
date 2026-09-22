// components/Readouts.test.tsx
//
// Task t18 acceptance criterion 3 (verbatim): "pitch, resonance and
// noise-floor readouts render when present and are absent, not zero, when
// not."

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Readouts } from "./Readouts";
import type { TraceSample } from "../waveform/model";

function sample(overrides: Partial<TraceSample> = {}): TraceSample {
  return {
    envelope: { mins: [], maxs: [] },
    levelDb: -20,
    noiseFloorDb: -60,
    zeroCrossingHz: 220,
    resonanceHz: null,
    direction: "out",
    receivedAtMs: 0,
    ...overrides,
  };
}

describe("Readouts", () => {
  it("renders nothing when sample is null", () => {
    const { container } = render(<Readouts sample={null} label="assistant" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the pitch readout when zero_crossing_hz is present", () => {
    const { container } = render(
      <Readouts sample={sample({ zeroCrossingHz: 220.4 })} label="assistant" />,
    );
    const pitch = container.querySelector('[data-readout="pitch"] dd');
    expect(pitch).not.toBeNull();
    expect(pitch?.textContent).toBe("220 Hz");
  });

  it("renders NOTHING (no element) for pitch when zero_crossing_hz is null -- not 0, not a dash", () => {
    const { container } = render(
      <Readouts sample={sample({ zeroCrossingHz: null })} label="assistant" />,
    );
    expect(container.querySelector('[data-readout="pitch"]')).toBeNull();
    expect(container.textContent).not.toContain("0 Hz");
    expect(container.textContent).not.toContain("—"); // em dash placeholder
  });

  it("renders the noise-floor readout when noise_floor_db is present", () => {
    const { container } = render(
      <Readouts sample={sample({ noiseFloorDb: -58 })} label="assistant" />,
    );
    const noiseFloor = container.querySelector('[data-readout="noise-floor"] dd');
    expect(noiseFloor?.textContent).toBe("-58 dB");
  });

  it("renders nothing for noise-floor when noise_floor_db is null", () => {
    const { container } = render(
      <Readouts sample={sample({ noiseFloorDb: null })} label="assistant" />,
    );
    expect(container.querySelector('[data-readout="noise-floor"]')).toBeNull();
  });

  it("renders the resonance readout ONLY when resonance_hz is present (a field the schema does not carry today)", () => {
    const withoutResonance = render(
      <Readouts sample={sample({ resonanceHz: null })} label="assistant" />,
    );
    expect(withoutResonance.container.querySelector('[data-readout="resonance"]')).toBeNull();

    const withResonance = render(
      <Readouts sample={sample({ resonanceHz: 850.6 })} label="assistant" />,
    );
    const resonance = withResonance.container.querySelector('[data-readout="resonance"] dd');
    expect(resonance?.textContent).toBe("851 Hz");
  });

  it("renders nothing at all when every readout field is absent", () => {
    const { container } = render(
      <Readouts
        sample={sample({ zeroCrossingHz: null, noiseFloorDb: null, resonanceHz: null })}
        label="assistant"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("never renders a literal 0 for an absent field even if 0 would be a plausible-looking value", () => {
    // A regression guard against the classic defect this criterion calls
    // out by name: "absent, not zero" -- a naive `sample.zeroCrossingHz ?? 0`
    // would print "0 Hz" here instead of omitting the readout.
    const { container } = render(
      <Readouts sample={sample({ zeroCrossingHz: null })} label="assistant" />,
    );
    expect(container.textContent ?? "").not.toMatch(/\b0 Hz\b/);
  });

  it("tags the block with which trace it belongs to", () => {
    const { container } = render(<Readouts sample={sample()} label="listener" />);
    expect(container.querySelector('[data-readouts-for="listener"]')).not.toBeNull();
  });
});
