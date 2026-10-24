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

/**
 * The numbers a bracketed group cites, or null when it is not a citation: a group
 * counts only when every number names one of the *sources* (1..sources). Review of
 * PR #98: "[2026-10-24]" or "[3, 4]" in an answer about a date or a list is text,
 * so it is shown and spoken, never turned into links to nothing.
 */
function citedNumbers(group: string, sources: number): number[] | null {
  const numbers = groupNumbers(group);
  if (numbers.length === 0) return null;
  return numbers.every((n) => n >= 1 && n <= sources) ? numbers : null;
}

/** Text and citation markers, in order; adjacent groups ("[2][3]") become separate cites. */
export function splitCitations(text: string, sources: number = Number.MAX_SAFE_INTEGER): CitationPart[] {
  const parts: CitationPart[] = [];
  let last = 0;
  for (const m of text.matchAll(GROUP)) {
    const numbers = citedNumbers(m[0], sources);
    if (numbers === null) continue;
    const at = m.index ?? 0;
    if (at > last) parts.push({ kind: "text", text: text.slice(last, at) });
    for (const n of numbers) parts.push({ kind: "cite", n });
    last = at + m[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

/** The text with its citation markers removed, for speech (TTS must not read "[2, 3]"). */
export function stripCitations(text: string, sources: number = Number.MAX_SAFE_INTEGER): string {
  return text
    .replace(GROUP, (group) => (citedNumbers(group, sources) === null ? group : ""))
    .replace(/[ \t]+([.,;:!?،])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}
