// audio/envelope.ts
//
// Decodes the `features` event's `env` field: a base64 string packing 16
// signed int8 minimums then 16 signed int8 maximums (embodiment's
// audio/features.py, `_encode_envelope`/`decode_envelope`, task t8). That
// module's own docstring names this exact browser decode path: "a browser
// decodes it with a single atob call into an Int8Array" — this is that
// call, kept in ONE place so nothing here hand-rolls the packing again.

export const ENVELOPE_POINTS = 16;

export interface Envelope {
  mins: number[];
  maxs: number[];
}

/** Decode a `features` event's `env` field. Never throws for malformed
 *  input (lesson 3: a public function degrades, it does not raise) — an
 *  undecodable or wrong-length string yields empty arrays, and the caller
 *  (Waveform.tsx) renders nothing rather than crash the whole dashboard. */
export function decodeEnvelope(env: string): Envelope {
  try {
    const binary = atob(env);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    const signed = new Int8Array(bytes.buffer);
    const half = Math.floor(signed.length / 2);
    if (half === 0) return { mins: [], maxs: [] };
    return {
      mins: Array.from(signed.slice(0, half)),
      maxs: Array.from(signed.slice(half, half * 2)),
    };
  } catch {
    return { mins: [], maxs: [] };
  }
}
