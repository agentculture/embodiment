// waveform/model.attack.test.ts
//
// Task-agent preamble lesson 1: attack the module beyond the acceptance
// criteria's happy path before reporting it done. Everything here found no
// defect (each assertion is what `model.ts`/`draw.ts` were already written
// to do) -- kept as a permanent regression file per the same lesson ("an
// attack that found nothing" is still reported, and worth pinning).

import { describe, expect, it } from "vitest";
import { sampleFromFeatures, oscilloscopeModel } from "./model";
import { drawOscilloscope, DEFAULT_OSCILLOSCOPE_COLORS } from "./draw";

function bigEnv(n: number) {
  return "A".repeat(n);
}

describe("attack: waveform model/draw under hostile input", () => {
  it("survives a 10k-char env string without throwing", () => {
    const evt: any = {
      v: 1, kind: "features", ts: "x", seq: 1, source: "s",
      data: { direction: "out", env: bigEnv(10000), level_db: -1, noise_floor_db: -1, zero_crossing_hz: 1 },
    };
    expect(() => sampleFromFeatures(evt, 0)).not.toThrow();
  });

  it("survives NUL bytes and bidi override chars in direction", () => {
    const evt: any = {
      v: 1, kind: "features", ts: "x", seq: 1, source: "s",
      data: { direction: "out\u0000‮attack", env: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", level_db: -1, noise_floor_db: -1, zero_crossing_hz: 1 },
    };
    const sample = sampleFromFeatures(evt, 0);
    expect(sample).not.toBeNull();
  });

  it("survives calling the same sample 10000 times through traceFrame with advancing clock", () => {
    const evt: any = {
      v: 1, kind: "features", ts: "x", seq: 1, source: "s",
      data: { direction: "out", env: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", level_db: -1, noise_floor_db: -1, zero_crossing_hz: 1 },
    };
    const sample = sampleFromFeatures(evt, 0);
    for (let i = 0; i < 10000; i++) {
      const model = oscilloscopeModel(sample, null, i, 500);
      expect(typeof model.out.isIdle).toBe("boolean");
    }
  });

  it("draw survives zero width/height canvas", () => {
    const calls: string[] = [];
    const ctx: any = {
      clearRect: () => calls.push("clearRect"),
      beginPath: () => {},
      moveTo: () => {},
      lineTo: () => {},
      stroke: () => {},
      fillRect: () => {},
      strokeStyle: "", fillStyle: "", lineWidth: 0, globalAlpha: 1,
    };
    const model = oscilloscopeModel(null, null, 0, 500);
    expect(() => drawOscilloscope(ctx, 0, 0, model, DEFAULT_OSCILLOSCOPE_COLORS)).not.toThrow();
  });

  it("model degrades (never throws) on env with only odd-length or garbage bytes", () => {
    const evt: any = {
      v: 1, kind: "features", ts: "x", seq: 1, source: "s",
      data: { direction: "out", env: "not-base64!!!", level_db: -1, noise_floor_db: -1, zero_crossing_hz: 1 },
    };
    expect(sampleFromFeatures(evt, 0)).toBeNull();
  });

  it("resonance/pitch fields with NaN/Infinity are treated as absent, not rendered as a bogus number", () => {
    const evt: any = {
      v: 1, kind: "features", ts: "x", seq: 1, source: "s",
      data: { direction: "out", env: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", level_db: -1, noise_floor_db: NaN, zero_crossing_hz: Infinity, resonance_hz: -Infinity },
    };
    const sample = sampleFromFeatures(evt, 0);
    expect(sample?.noiseFloorDb).toBeNull();
    expect(sample?.zeroCrossingHz).toBeNull();
    expect(sample?.resonanceHz).toBeNull();
  });
});
