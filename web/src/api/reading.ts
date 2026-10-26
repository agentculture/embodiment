// api/reading.ts
//
// Client for the reading routes (embodiment#107, plan t4; the routes are t7's,
// the state machine behind them t3's). The contract, shared by t3, t4 and t7:
//
//   GET  /api/reading                      saved positions
//        [{item_id, sha256, section, paragraph, part, updated_at, gone}]
//   GET  /api/reading/{item_id}/next?section=&paragraph=
//        both optional (absent = continue at the cursor); answers one unit
//        {item_id, sha256, title, section, section_count, heading, paragraph,
//         paragraph_count, part, part_count, text, position} or {done: true}
//   POST /api/reading/{item_id}/heard {position} -> {moved, cursor}
//   POST /api/reading/{item_id}/clear      drops the cursor
//   errors: 404 library-item-not-found, 410 library-item-gone,
//           503 library_unavailable
//
// `position` is opaque: the page only ever hands back the exact string a unit
// carried. Never throws, like api/library.ts: every failure is a fixed code.
// The install secret rides in `Authorization: Bearer` only when one is held
// (on the public Host an Access assertion replaces it).

export interface ReadingClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

export type ReadingOutcome<T> = { ok: true; value: T } | { ok: false; code: string };

export interface SavedPosition {
  itemId: string;
  sha256: string;
  section: number;
  paragraph: number;
  part: number;
  updatedAt: string;
  /** The item is no longer in the library: the cursor is kept but not offered. */
  gone: boolean;
}

/** One spoken unit: a paragraph, or one part of a paragraph too long for one speak call. */
export interface ReadingUnit {
  itemId: string;
  sha256: string;
  title: string;
  /** Indices as the daemon gives them (0-based); the page shows them +1. */
  section: number;
  sectionCount: number;
  heading: string;
  paragraph: number;
  paragraphCount: number;
  part: number;
  partCount: number;
  text: string;
  position: string;
}

export type NextResult = { done: true } | { done: false; unit: ReadingUnit };

export interface HeardResult {
  moved: boolean;
  cursor: Record<string, unknown>;
}

export interface At {
  section: number;
  paragraph: number;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const idOf = (v: unknown): string =>
  typeof v === "number" && Number.isFinite(v) ? String(v) : str(v);
const int = (v: unknown, dflt = 0): number =>
  typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.floor(v)) : dflt;
const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

/** The contract's codes for a refusal whose body names none. */
const STATUS_CODES: Record<number, string> = {
  404: "library-item-not-found",
  410: "library-item-gone",
  503: "library_unavailable",
};

async function call<T>(
  path: string,
  secret: string,
  options: ReadingClientOptions,
  parse: (body: unknown) => T | null,
  post?: Record<string, unknown>,
): Promise<ReadingOutcome<T>> {
  const fetchFn = options.fetchFn ?? fetch;
  const headers: Record<string, string> = post ? { "Content-Type": "application/json" } : {};
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let response: Response;
  try {
    response = await fetchFn(
      `${options.basePath ?? "/api"}${path}`,
      post ? { method: "POST", headers, body: JSON.stringify(post) } : { headers },
    );
  } catch {
    return { ok: false, code: "network-error" };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  if (!response.ok) {
    const code = str(rec(rec(body)?.error)?.code);
    return { ok: false, code: code || STATUS_CODES[response.status] || `http-${response.status}` };
  }
  const value = parse(body);
  return value === null ? { ok: false, code: "unparseable-response" } : { ok: true, value };
}

function parsePositions(body: unknown): SavedPosition[] | null {
  const list = Array.isArray(body) ? body : rec(body)?.positions;
  if (!Array.isArray(list)) return null;
  const out: SavedPosition[] = [];
  for (const raw of list) {
    const o = rec(raw);
    const itemId = o ? idOf(o.item_id) : "";
    if (!o || !itemId) continue;
    out.push({
      itemId,
      sha256: str(o.sha256),
      section: int(o.section),
      paragraph: int(o.paragraph),
      part: int(o.part),
      updatedAt: str(o.updated_at),
      gone: o.gone === true,
    });
  }
  return out;
}

function parseNext(body: unknown): NextResult | null {
  const o = rec(body);
  if (!o) return null;
  if (o.done === true) return { done: true };
  const position = str(o.position);
  const itemId = idOf(o.item_id);
  if (!position || !itemId || typeof o.text !== "string") return null;
  return {
    done: false,
    unit: {
      itemId,
      sha256: str(o.sha256),
      title: str(o.title),
      section: int(o.section),
      sectionCount: Math.max(1, int(o.section_count, 1)),
      heading: str(o.heading),
      paragraph: int(o.paragraph),
      paragraphCount: Math.max(1, int(o.paragraph_count, 1)),
      part: int(o.part),
      partCount: Math.max(1, int(o.part_count, 1)),
      text: o.text,
      position,
    },
  };
}

function parseHeard(body: unknown): HeardResult | null {
  const o = rec(body);
  if (!o || typeof o.moved !== "boolean") return null;
  return { moved: o.moved, cursor: rec(o.cursor) ?? {} };
}

const itemPath = (itemId: string) => `/reading/${encodeURIComponent(itemId)}`;

/** GET /api/reading: every saved position, gone ones flagged. */
export function fetchPositions(
  secret: string,
  options: ReadingClientOptions = {},
): Promise<ReadingOutcome<SavedPosition[]>> {
  return call("/reading", secret, options, parsePositions);
}

/** GET /api/reading/{id}/next: the unit at `at`, or at the cursor when `at` is absent. */
export function fetchNext(
  itemId: string,
  secret: string,
  options: ReadingClientOptions = {},
  at?: At,
): Promise<ReadingOutcome<NextResult>> {
  const query = at ? `?section=${int(at.section)}&paragraph=${int(at.paragraph)}` : "";
  return call(`${itemPath(itemId)}/next${query}`, secret, options, parseNext);
}

/** POST /api/reading/{id}/heard {position}: sent only once that unit's audio ended. */
export function reportHeard(
  itemId: string,
  position: string,
  secret: string,
  options: ReadingClientOptions = {},
): Promise<ReadingOutcome<HeardResult>> {
  return call(`${itemPath(itemId)}/heard`, secret, options, parseHeard, { position });
}

/** POST /api/reading/{id}/clear: forget the cursor. */
export function clearReading(
  itemId: string,
  secret: string,
  options: ReadingClientOptions = {},
): Promise<ReadingOutcome<true>> {
  return call(`${itemPath(itemId)}/clear`, secret, options, () => true, {});
}
