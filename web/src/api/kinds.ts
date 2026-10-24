// api/kinds.ts
//
// Library item kinds: the label people read and the colour token that
// identifies the kind everywhere (shelf, list dots, cards, charts). The order
// and the colours are culture-library's (styles.css --k-*), so an item looks
// the same on library.culture.dev and on Gwen's page.

export interface KindInfo {
  id: string;
  /** Singular, for a badge on one item. */
  one: string;
  /** Plural, for a filter or a legend. */
  many: string;
  /** The CSS custom property holding the kind's colour. */
  color: string;
}

export const KINDS: KindInfo[] = [
  { id: "newspaper", one: "Newspaper", many: "Newspapers", color: "var(--k-newspaper)" },
  { id: "document", one: "Document", many: "Documents", color: "var(--k-document)" },
  { id: "book", one: "Book", many: "Books", color: "var(--k-book)" },
  { id: "transcript", one: "Transcript", many: "Transcripts", color: "var(--k-transcript)" },
  { id: "spec", one: "Spec or plan", many: "Specs and plans", color: "var(--k-repo)" },
  { id: "diary", one: "Diary", many: "Diary", color: "var(--k-diary)" },
];

const OTHER: KindInfo = { id: "other", one: "Item", many: "Other", color: "var(--k-other)" };

export function kindInfo(kind: string): KindInfo {
  return KINDS.find((k) => k.id === kind) ?? { ...OTHER, id: kind || "other" };
}

/** Kinds present in `items`, in the fixed order (never by count, so colours never shift). */
export function kindsPresent(items: { kind: string }[]): KindInfo[] {
  const seen = new Set(items.map((i) => i.kind));
  const known = KINDS.filter((k) => seen.has(k.id));
  const rest = [...seen].filter((k) => !KINDS.some((x) => x.id === k)).sort();
  return [...known, ...rest.map(kindInfo)];
}

/** "Friday 23 October" from an ISO timestamp; "" when it does not parse. */
export function dayLabel(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
}

/** "23 Oct" from an ISO timestamp; "" when it does not parse. */
export function shortDate(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

// xteink stores every non-book as kind "article" (seen on the live library,
// 2026-10-24: 50 articles, 1 book), but the titles carry what each item is:
// culture-rules names newspapers "Daily newspaper YYYY-MM-DD" and transcripts
// "YYYY-MM-DD <topic>". These are display rules only; the wire kind is kept.
const DATED = /^(\d{4}-\d{2}-\d{2})\s+(.+)$/;
const PAPER = /^daily newspaper\b.*?(\d{4}-\d{2}-\d{2})?/i;

/** The kind to show: the wire kind, refined from the title when the wire says only "article". */
export function displayKind(kind: string, title: string): string {
  if (kind && kind !== "article") return kind;
  if (PAPER.test(title)) return "newspaper";
  if (DATED.test(title)) return "transcript";
  return kind === "article" ? "document" : kind;
}

/** The day an item is about: a date in its title (recording or issue day), else when it was added. */
export function itemDay(createdAt: string, title: string): string {
  const dated = DATED.exec(title);
  if (dated) return dated[1];
  const paper = /(\d{4}-\d{2}-\d{2})/.exec(PAPER.test(title) ? title : "");
  if (paper) return paper[1];
  return createdAt.slice(0, 10);
}

/** A title without the date its day heading already shows ("2026-10-14 recording" -> "recording"). */
export function shortTitle(title: string): string {
  const dated = DATED.exec(title);
  if (dated) return dated[2];
  if (PAPER.test(title)) return title.replace(/\s*\d{4}-\d{2}-\d{2}/, "").trim() || title;
  return title;
}
