// api/todos.ts
//
// Client for GET /api/todos -> {groups:[{recording_id,title,date,items:[{text,created}]}]}
// (plan t9, route from t6). Read-only: no done/undone writes this cycle.
// Never throws: a failure is a tagged result carrying a fixed code and the
// HTTP status. The install secret rides in `Authorization: Bearer` only when
// held, exactly like api/chat.ts.

export interface TodoItem {
  text: string;
  created: string;
}

export interface TodoGroup {
  recordingId: string;
  title: string;
  date: string;
  items: TodoItem[];
}

export type TodosOutcome =
  | { ok: true; groups: TodoGroup[] }
  | { ok: false; status: number; code: string };

export interface TodosClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object";

function parseItems(raw: unknown): TodoItem[] {
  if (!Array.isArray(raw)) return [];
  const out: TodoItem[] = [];
  for (const it of raw) {
    if (isObj(it) && typeof it.text === "string") out.push({ text: it.text, created: str(it.created) });
  }
  return out;
}

function parseGroups(raw: unknown): TodoGroup[] {
  const out: TodoGroup[] = [];
  for (const g of raw as unknown[]) {
    if (!isObj(g)) continue;
    out.push({
      recordingId: str(g.recording_id),
      title: str(g.title),
      date: str(g.date),
      items: parseItems(g.items),
    });
  }
  return out;
}

function errorCode(body: unknown): string {
  if (!isObj(body)) return "unparseable-response";
  if (typeof body.code === "string") return body.code;
  if (isObj(body.error) && typeof body.error.code === "string") return body.error.code;
  return "unparseable-response";
}

export async function fetchTodos(
  secret: string,
  options: TodosClientOptions = {},
): Promise<TodosOutcome> {
  const fetchFn = options.fetchFn ?? fetch;
  const basePath = options.basePath ?? "/api";
  const headers: Record<string, string> = {};
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let response: Response;
  try {
    response = await fetchFn(`${basePath}/todos`, { method: "GET", headers });
  } catch {
    return { ok: false, status: 0, code: "network-error" };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  if (!response.ok) return { ok: false, status: response.status, code: errorCode(body) };
  if (!isObj(body) || !Array.isArray(body.groups)) {
    return { ok: false, status: response.status, code: "unparseable-response" };
  }
  return { ok: true, groups: parseGroups(body.groups) };
}
