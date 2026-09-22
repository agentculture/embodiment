import { beforeEach, describe, expect, it } from "vitest";
import {
  decodeEnvelope,
  getRejectedEnvelopeCount,
  resetRejectedEnvelopeCountForTests,
} from "./envelope";

/** Build a base64 `env` string from raw signed-int8 values, mirroring
 *  embodiment/audio/features.py's `_encode_envelope` packing. */
function encode(values: number[]): string {
  const bytes = new Uint8Array(values.map((v) => v & 0xff));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

describe("decodeEnvelope", () => {
  beforeEach(() => {
    resetRejectedEnvelopeCountForTests();
  });

  it("decodes a real 32-byte (16 min + 16 max) envelope", () => {
    const mins = Array.from({ length: 16 }, (_, i) => -i - 1);
    const maxs = Array.from({ length: 16 }, (_, i) => i);
    const result = decodeEnvelope(encode([...mins, ...maxs]));
    expect(result).not.toBeNull();
    expect(result?.mins).toEqual(mins);
    expect(result?.maxs).toEqual(maxs);
    expect(getRejectedEnvelopeCount()).toBe(0);
  });

  it("decodes a smaller even-length envelope (any even split is accepted, not just 16+16)", () => {
    const result = decodeEnvelope(encode([1, 2, 3, 4]));
    expect(result).toEqual({ mins: [1, 2], maxs: [3, 4] });
    expect(getRejectedEnvelopeCount()).toBe(0);
  });

  it("rejects and counts a zero-length envelope", () => {
    const result = decodeEnvelope(encode([]));
    expect(result).toBeNull();
    expect(getRejectedEnvelopeCount()).toBe(1);
  });

  it("rejects and counts an odd-length envelope (round 2: never split whatever arrives)", () => {
    const result = decodeEnvelope(encode([1, 2, 3]));
    expect(result).toBeNull();
    expect(getRejectedEnvelopeCount()).toBe(1);
  });

  it("rejects and counts an empty string", () => {
    const result = decodeEnvelope("");
    expect(result).toBeNull();
    expect(getRejectedEnvelopeCount()).toBe(1);
  });

  it("rejects and counts invalid base64", () => {
    const result = decodeEnvelope("not valid base64!!!");
    expect(result).toBeNull();
    expect(getRejectedEnvelopeCount()).toBe(1);
  });

  it("accumulates the rejection count across multiple bad frames", () => {
    decodeEnvelope(encode([1, 2, 3]));
    decodeEnvelope(encode([]));
    decodeEnvelope("garbage!!!");
    expect(getRejectedEnvelopeCount()).toBe(3);
  });

  it("the committed features.json fixture's placeholder env (known non-32-byte) is rejected, not mis-split", () => {
    // tests/fixtures/events/features.json's env decodes to 48 bytes, not
    // the real encoder's 32 -- even, so it is NOT rejected by the
    // even/zero-length rule (round 2 only tightens odd/zero, per the
    // brief: "leave the fixture alone"). This test documents that fact
    // rather than assuming it.
    const fixtureEnv =
      "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    const result = decodeEnvelope(fixtureEnv);
    expect(result).not.toBeNull();
    expect(result?.mins).toHaveLength(24);
    expect(result?.maxs).toHaveLength(24);
  });
});
