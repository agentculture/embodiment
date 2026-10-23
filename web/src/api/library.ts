// api/library.ts
//
// Client for GET /api/library and GET /api/library/{id} (plan t8; routes by t6).
// Never throws: every failure is a tagged result carrying only a fixed code,
// like api/chat.ts. The install secret rides in `Authorization: Bearer` only
// when one is held.

export interface LibraryItem {
  id: string;
  title: string;
  kind: string;
  createdAt: string;
  indexed: boolean;
}

export interface LibraryItemDetail {
  id: string;
  title: string;
  kind: string;
  author: string;
  createdAt: string;
}

export interface Passage {
  anchor: string;
  heading: string;
  text: string;
  startS: number | null;
  endS: number | null;
  speaker: string;
}

export interface LibraryDocument {
  item: LibraryItemDetail;
  passages: Passage[];
}

export type LibraryOutcome<T> = { ok: true; value: T } | { ok: false; code: string };

export interface LibraryClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

async function getJson<T>(
  path: string,
  secret: string,
  options: LibraryClientOptions,
  parse: (body: Record<string, unknown>) => T | null,
): Promise<LibraryOutcome<T>> {
  const fetchFn = options.fetchFn ?? fetch;
  const headers: Record<string, string> = {};
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let response: Response;
  try {
    response = await fetchFn(`${options.basePath ?? "/api"}${path}`, { headers });
  } catch {
    return { ok: false, code: "network-error" };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const obj = rec(body);
  if (!response.ok) {
    const code = str(rec(obj?.error)?.code);
    return { ok: false, code: code || "unparseable-response" };
  }
  const value = obj ? parse(obj) : null;
  return value === null ? { ok: false, code: "unparseable-response" } : { ok: true, value };
}

function parseItems(body: Record<string, unknown>): LibraryItem[] | null {
  if (!Array.isArray(body.items)) return null;
  const out: LibraryItem[] = [];
  for (const raw of body.items) {
    const o = rec(raw);
    if (!o) continue;
    out.push({
      id: str(o.id),
      title: str(o.title),
      kind: str(o.kind),
      createdAt: str(o.created_at),
      indexed: o.indexed === true,
    });
  }
  return out;
}

function parseDocument(body: Record<string, unknown>): LibraryDocument | null {
  const item = rec(body.item);
  if (!item || !Array.isArray(body.passages)) return null;
  const passages: Passage[] = [];
  for (const raw of body.passages) {
    const o = rec(raw);
    if (!o) continue;
    passages.push({
      anchor: str(o.anchor),
      heading: str(o.heading),
      text: str(o.text),
      startS: num(o.start_s),
      endS: num(o.end_s),
      speaker: str(o.speaker),
    });
  }
  return {
    item: {
      id: str(item.id),
      title: str(item.title),
      kind: str(item.kind),
      author: str(item.author),
      createdAt: str(item.created_at),
    },
    passages,
  };
}

export function fetchLibrary(
  secret: string,
  options: LibraryClientOptions = {},
): Promise<LibraryOutcome<LibraryItem[]>> {
  return getJson("/library", secret, options, parseItems);
}

export function fetchItem(
  id: string,
  secret: string,
  options: LibraryClientOptions = {},
): Promise<LibraryOutcome<LibraryDocument>> {
  return getJson(`/library/${encodeURIComponent(id)}`, secret, options, parseDocument);
}
