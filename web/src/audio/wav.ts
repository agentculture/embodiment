// audio/wav.ts
//
// d7 (the operator's phone, 2026-10-26): a long paragraph starts from its first sentence's
// audio while the rest is made, and the two are kept joined as one file, so a replay or a
// Previous plays it seamlessly. /api/speak answers pcm16 WAV (daemon/text_turns.py
// pcm_to_wav); this reads its data chunk and writes one back. Pure, no IO.

export interface Pcm {
  rate: number;
  channels: number;
  bits: number;
  data: Uint8Array;
}

/** The format and sample bytes of a PCM WAV, or null for anything else. Never throws. */
export function readWav(bytes: Uint8Array): Pcm | null {
  try {
    const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const tag = (o: number) => String.fromCharCode(bytes[o], bytes[o + 1], bytes[o + 2], bytes[o + 3]);
    if (bytes.length < 12 || tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
    let fmt: { rate: number; channels: number; bits: number } | null = null;
    let o = 12;
    while (o + 8 <= bytes.length) {
      const id = tag(o);
      const size = v.getUint32(o + 4, true);
      const body = o + 8;
      if (id === "fmt ") {
        if (v.getUint16(body, true) !== 1) return null; // PCM only
        fmt = { channels: v.getUint16(body + 2, true), rate: v.getUint32(body + 4, true), bits: v.getUint16(body + 14, true) };
      } else if (id === "data") {
        if (!fmt) return null;
        return { ...fmt, data: bytes.subarray(body, Math.min(bytes.length, body + size)) };
      }
      o = body + size + (size % 2);
    }
    return null;
  } catch {
    return null;
  }
}

/** A PCM WAV of *data* in format *f*. */
export function writeWav(f: Omit<Pcm, "data">, data: Uint8Array): Uint8Array {
  const out = new Uint8Array(44 + data.length);
  const v = new DataView(out.buffer);
  const put = (o: number, s: string) => [...s].forEach((c, i) => (out[o + i] = c.charCodeAt(0)));
  const block = f.channels * (f.bits / 8);
  put(0, "RIFF");
  v.setUint32(4, 36 + data.length, true);
  put(8, "WAVE");
  put(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, f.channels, true);
  v.setUint32(24, f.rate, true);
  v.setUint32(28, f.rate * block, true);
  v.setUint16(32, block, true);
  v.setUint16(34, f.bits, true);
  put(36, "data");
  v.setUint32(40, data.length, true);
  out.set(data, 44);
  return out;
}

/** *a* then *pauseS* of silence then *b*, as one WAV; null unless both are WAVs of one format. */
export function joinWav(a: Uint8Array, b: Uint8Array, pauseS: number): Uint8Array | null {
  const x = readWav(a);
  const y = readWav(b);
  if (!x || !y || x.rate !== y.rate || x.channels !== y.channels || x.bits !== y.bits) return null;
  const block = x.channels * (x.bits / 8);
  const gap = Math.max(0, Math.round(pauseS * x.rate)) * block;
  const data = new Uint8Array(x.data.length + gap + y.data.length);
  data.set(x.data, 0);
  data.set(y.data, x.data.length + gap);
  return writeWav(x, data);
}

/** The first sentence of *text* and the rest, split as the daemon splits sentences
 *  (voice.py _SENTENCE_END); null when there is one sentence. */
export function splitHead(text: string): { head: string; rest: string } | null {
  const m = /[.!?。！？](\s+)/.exec(text.trim());
  if (!m) return null;
  const cut = m.index + 1;
  const head = text.trim().slice(0, cut).trim();
  const rest = text.trim().slice(cut).trim();
  return head && rest ? { head, rest } : null;
}
