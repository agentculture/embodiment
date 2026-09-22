// audio/envelope.ts
//
// Decodes the `features` event's `env` field: a base64 string packing
// signed int8 minimums then an equal count of signed int8 maximums
// (embodiment's audio/features.py, `_encode_envelope`/`decode_envelope`,
// task t8). That module's own docstring names this exact browser decode
// path: "a browser decodes it with a single atob call into an Int8Array"
// — this is that call, kept in ONE place so nothing here hand-rolls the
// packing again.

export const ENVELOPE_POINTS = 16;

export interface Envelope {
  mins: number[];
  maxs: number[];
}

/** Count of `env` values rejected by :func:`decodeEnvelope` since the last
 *  reset — lesson 3: a bounded/rejecting decoder counts what it drops
 *  rather than losing it silently. Module-scoped rather than returned
 *  alongside every call so Waveform.tsx doesn't have to thread a counter
 *  through React state just to expose a number nothing renders yet; a
 *  future status pane can read it. */
let rejectedCount = 0;

export function getRejectedEnvelopeCount(): number {
  return rejectedCount;
}

/** Test seam: drop back to zero between tests. Never called by production
 *  code — the whole point of the counter is that it accumulates for the
 *  dashboard's real lifetime. */
export function resetRejectedEnvelopeCountForTests(): void {
  rejectedCount = 0;
}

/**
 * Decode a `features` event's `env` field, or return null and count the
 * rejection. Never throws (lesson 3: a public function degrades, it does
 * not raise).
 *
 * Round 2 correction: the previous version split whatever length arrived
 * in half regardless — an odd total (can't split into two equal arrays) or
 * a zero-length decode silently produced nonsense or an empty envelope
 * indistinguishable from "no data yet". This version REJECTS (and counts)
 * a decoded byte length that is zero or not evenly divisible by two,
 * rather than splitting it anyway. It does NOT require exactly
 * :data:`ENVELOPE_POINTS`*2 bytes — the real encoder always produces
 * 32 (16+16), but nothing here hardcodes that count, so a differently
 * sized-but-well-formed envelope still decodes.
 */
export function decodeEnvelope(env: string): Envelope | null {
  try {
    const binary = atob(env);
    if (binary.length === 0 || binary.length % 2 !== 0) {
      rejectedCount += 1;
      return null;
    }
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    const signed = new Int8Array(bytes.buffer);
    const half = signed.length / 2;
    return {
      mins: Array.from(signed.slice(0, half)),
      maxs: Array.from(signed.slice(half, half * 2)),
    };
  } catch {
    rejectedCount += 1;
    return null;
  }
}
