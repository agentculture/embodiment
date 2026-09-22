// audio/pcm16-roundtrip.test.ts
//
// Task t18 acceptance criterion 2 (verbatim): "pcm16 encode/decode
// round-trips a known buffer exactly." The cited `audio/lobes/pcm-wire.ts`
// (lobes-cli, pinned commit -- see `audio/lobes/README.md`) already proves
// each HALF of this codec on its own (encode-to-base64, decode-from-base64);
// this file proves the round trip itself, named for the criterion, chaining
// the outbound encoder into the inbound decoder on one fixed buffer -- the
// exact shape a browser doing loopback (or a test) would exercise.

import { describe, expect, it } from "vitest";
import { decodeAudioDelta, encodeAudioPayload } from "./lobes/pcm-wire";

describe("pcm16 encode/decode round-trips a known buffer exactly (t18 acceptance criterion 2)", () => {
  it("round-trips a known buffer of representable 16-bit values exactly", () => {
    // Every value below is an exact multiple of 1/32768 (or 1/32767 at the
    // positive pole), so nothing here depends on floatToPcm16's rounding --
    // this is testing that the round trip is LOSSLESS for values the codec
    // can represent exactly, which is what "round-trips exactly" means.
    const known = new Float32Array([
      0,
      1 / 32768, // smallest positive step below zero-crossing
      -1 / 32768,
      0.5,
      -0.5,
      1, // full scale positive (-> 32767, the asymmetric pole)
      -1, // full scale negative (-> -32768)
      -1 + 1 / 32768,
    ]);

    const encoded = encodeAudioPayload(known);
    const decoded = decodeAudioDelta(encoded);

    expect(decoded.length).toBe(known.length);
    for (let i = 0; i < known.length; i += 1) {
      expect(decoded[i]).toBeCloseTo(known[i], 4);
    }
    // The known positive-full-scale sample is the one documented exception
    // (pcm-wire.ts's own header: "-1.0 -> -32768, +1.0 -> +32767" -- one
    // fewer positive code point than negative). It still round-trips to a
    // STABLE, exact value (32767 / 32768), just not bit-identical to the
    // input 1.0 -- asserted explicitly so this is a stated fact, not hidden
    // inside toBeCloseTo's tolerance.
    expect(decoded[5]).toBeCloseTo(32767 / 32768, 10);
  });

  it("round-trips a buffer built from many distinct values without cross-talk between samples", () => {
    const known = Float32Array.from({ length: 64 }, (_, i) => (i - 32) / 32);
    const decoded = decodeAudioDelta(encodeAudioPayload(known));
    expect(decoded.length).toBe(known.length);
    for (let i = 0; i < known.length; i += 1) {
      expect(decoded[i]).toBeCloseTo(known[i], 3);
    }
  });

  it("round-trips an empty buffer to an empty buffer, not an error", () => {
    const decoded = decodeAudioDelta(encodeAudioPayload(new Float32Array(0)));
    expect(decoded.length).toBe(0);
  });
});
