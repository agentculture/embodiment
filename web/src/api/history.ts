// api/history.ts
//
// Client for the history pane (operator, 2026-10-24): GET /api/conversations and
// GET /api/conversations/{id}, read-only views of the daemon's session logs.
// Never throws: a failure is a tagged result with a fixed code, like api/library.ts.

export interface ConversationSummary {
  id: string;
  title: string;
  started: number | null;
  last: number | null;
  turns: number;
  current: boolean;
}

export interface HistoryTurn {
  role: "user" | "assistant";
  text: string;
  ts: number | null;
}

export type HistoryOutcome<T> = { ok: true; value: T } | { ok: false; code: string };

export interface HistoryClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

function summary(raw: unknown): ConversationSummary | null {
  const o = rec(raw);
  if (!o || !str(o.id)) return null;
  return {
    id: str(o.id),
    title: str(o.title),
    started: num(o.started),
    last: num(o.last),
    turns: num(o.turns) ?? 0,
    current: o.current === true,
  };
}

async function getJson(path: string, secret: string, options: HistoryClientOptions): Promise<HistoryOutcome<Record<string, unknown>>> {
  const headers: Record<string, string> = {};
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let response: Response;
  try {
    response = await (options.fetchFn ?? fetch)(`${options.basePath ?? "/api"}${path}`, { headers });
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
  if (!response.ok) return { ok: false, code: str(rec(obj?.error)?.code) || "unparseable-response" };
  return obj ? { ok: true, value: obj } : { ok: false, code: "unparseable-response" };
}

export async function fetchConversations(
  secret: string,
  options: HistoryClientOptions = {},
): Promise<HistoryOutcome<ConversationSummary[]>> {
  const out = await getJson("/conversations", secret, options);
  if (!out.ok) return out;
  const list = out.value.conversations;
  if (!Array.isArray(list)) return { ok: false, code: "unparseable-response" };
  return { ok: true, value: list.map(summary).filter((c): c is ConversationSummary => c !== null) };
}

export async function fetchConversation(
  id: string,
  secret: string,
  options: HistoryClientOptions = {},
): Promise<HistoryOutcome<{ conversation: ConversationSummary; turns: HistoryTurn[] }>> {
  const out = await getJson(`/conversations/${encodeURIComponent(id)}`, secret, options);
  if (!out.ok) return out;
  const conversation = summary(out.value.conversation);
  if (!conversation || !Array.isArray(out.value.turns)) return { ok: false, code: "unparseable-response" };
  const turns: HistoryTurn[] = [];
  for (const raw of out.value.turns) {
    const o = rec(raw);
    if (!o || (o.role !== "user" && o.role !== "assistant")) continue;
    turns.push({ role: o.role, text: str(o.text), ts: num(o.ts) });
  }
  return { ok: true, value: { conversation, turns } };
}
