// api/citations.ts
//
// The model cites library passages as [n]. It also writes [2, 3], [2][3],
// [2-3] and [2–3] (operator feedback 2026-10-24: "[2,3] was merged so the link
// was broken"). splitCitations turns all of them into one marker per source;
// stripCitations removes them so speech never reads the numbers aloud.

/** One bracketed group of source numbers: [1], [1, 2], [1,2,3], [2-4], [2–4]. */
const GROUP = /\[\s*\d+(?:\s*(?:[,،;]|[-–—])\s*\d+)*\s*\]/g;

/** At most this many numbers expand from one range, so "[1-9999]" stays bounded. */
const MAX_RANGE = 12;

export type CitationPart = { kind: "text"; text: string } | { kind: "cite"; n: number };

/** The source numbers one bracketed group names, in order, ranges expanded. */
export function groupNumbers(group: string): number[] {
  const out: number[] = [];
  const body = group.slice(1, -1);
  for (const piece of body.split(/[,،;]/)) {
    const range = /^\s*(\d+)\s*[-–—]\s*(\d+)\s*$/.exec(piece);
    if (range) {
      const a = Number(range[1]);
      const b = Number(range[2]);
      if (b >= a && b - a < MAX_RANGE) for (let n = a; n <= b; n += 1) out.push(n);
      else out.push(a, b);
      continue;
    }
    const n = Number(piece.trim());
    if (Number.isInteger(n)) out.push(n);
  }
  return out;
}

/** Text and citation markers, in order; adjacent groups ("[2][3]") become separate cites. */
export function splitCitations(text: string): CitationPart[] {
  const parts: CitationPart[] = [];
  let last = 0;
  for (const m of text.matchAll(GROUP)) {
    const at = m.index ?? 0;
    if (at > last) parts.push({ kind: "text", text: text.slice(last, at) });
    for (const n of groupNumbers(m[0])) parts.push({ kind: "cite", n });
    last = at + m[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

/** The text with every citation marker removed, for speech (TTS must not read "[2, 3]"). */
export function stripCitations(text: string): string {
  return text
    .replace(GROUP, "")
    .replace(/[ \t]+([.,;:!?،])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}
