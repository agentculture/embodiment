// api/chat.ts
//
// Client for POST /api/chat {message} -> {reply, sources:[...]} (plan t8).
// Never throws: every failure is returned as a tagged result carrying only a
// fixed code, so the caller can render and record the degradation (C3).
// The install secret rides in `Authorization: Bearer` exactly like
// api/control.ts -- and is omitted entirely when none is held. It is sent
// whenever held, whatever the Host: on the public Host the daemon admits a
// valid Access assertion instead, and a held secret there is harmless.

export interface ChatSource {
  source: string;
  title: string;
  date: string;
  /** seconds into a recording; null for an issue source. */
  start: number | null;
  end: number | null;
  section: string;
  link: string;
  text: string;
}

export interface ChatReply {
  reply: string;
  sources: ChatSource[];
  turnSerial: number | null;
}

export type ChatOutcome =
  | { ok: true; value: ChatReply }
  | { ok: false; code: string };

export interface ChatClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export function parseSources(raw: unknown): ChatSource[] {
  if (!Array.isArray(raw)) return [];
  const out: ChatSource[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    out.push({
      source: str(o.source),
      title: str(o.title),
      date: str(o.date),
      start: num(o.start),
      end: num(o.end),
      section: str(o.section),
      link: str(o.link),
      text: str(o.text),
    });
  }
  return out;
}

/** Seconds -> "mm:ss" (minutes keep growing past 59). */
export function formatMmSs(seconds: number | null): string {
  if (seconds === null || seconds < 0) return "";
  const total = Math.floor(seconds);
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

export async function sendChat(
  message: string,
  secret: string,
  options: ChatClientOptions = {},
): Promise<ChatOutcome> {
  const fetchFn = options.fetchFn ?? fetch;
  const basePath = options.basePath ?? "/api";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let response: Response;
  try {
    response = await fetchFn(`${basePath}/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify({ message }),
    });
  } catch {
    return { ok: false, code: "network-error" };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const obj = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
  if (!response.ok) {
    const err = obj && typeof obj.error === "object" && obj.error ? (obj.error as Record<string, unknown>) : null;
    const code = err && typeof err.code === "string" ? err.code : "unparseable-response";
    return { ok: false, code };
  }
  const result =
    obj && typeof obj.result === "object" && obj.result ? (obj.result as Record<string, unknown>) : obj;
  if (!result || typeof result.reply !== "string") {
    return { ok: false, code: "unparseable-response" };
  }
  return {
    ok: true,
    value: {
      reply: result.reply,
      sources: parseSources(result.sources),
      turnSerial: num(result.turn_serial),
    },
  };
}
