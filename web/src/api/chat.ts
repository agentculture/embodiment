// Provenance: the citation/source shape ported from pocketsync/web/static/app.js
// (lines 336-432: ask, renderAnswer, sourceList) and pocketsync/web/static/index.html
// (the #thread / #composer markup). Text only; no HTML sinks.
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
  /** Library item kind (transcript, newspaper, ...); "" on the legacy shapes. */
  kind: string;
  date: string;
  /** seconds into a recording; null for an issue source. */
  start: number | null;
  end: number | null;
  section: string;
  link: string;
  text: string;
  /** Library citation target; absent on the legacy daemon shapes. */
  item_id?: string;
  /** Where inside the item to land; opaque to this client. */
  anchor?: unknown;
}

export interface ChatReply {
  reply: string;
  sources: ChatSource[];
  turnSerial: number | null;
  /** Set when a fixed text stood in for the model's answer: the reason's code. */
  fallback: string | null;
}

export type ChatOutcome =
  | { ok: true; value: ChatReply }
  | { ok: false; code: string; /** the role a model-unavailable refusal names, when it says */ role?: string };

export interface ChatClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

import { displayKind } from "./kinds";
import { settingsBody, type ConversationSettings } from "./models";

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export function parseSources(raw: unknown): ChatSource[] {
  if (!Array.isArray(raw)) return [];
  const out: ChatSource[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    // Library ids may arrive as numbers (xteink's are ints) or strings; keep both.
    const itemId = typeof o.item_id === "number" && Number.isFinite(o.item_id) ? String(o.item_id) : str(o.item_id);
    out.push({
      ...(itemId ? { item_id: itemId } : {}),
      ...(o.anchor !== undefined && o.anchor !== null ? { anchor: o.anchor } : {}),
      source: str(o.source),
      title: str(o.title),
      kind: o.kind === undefined ? "" : displayKind(str(o.kind), str(o.title)),
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
  conversation?: string,
  settings: ConversationSettings = {},
  /** Names this request so its answer can be fetched later (GET /api/chat/{id}). */
  requestId?: string,
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
      // With a conversation id the turn continues that earlier conversation (d5).
      body: JSON.stringify({
        message,
        ...(conversation ? { conversation } : {}),
        ...settingsBody(settings),
        ...(requestId ? { request_id: requestId } : {}),
      }),
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
    const code =
      err && typeof err.code === "string"
        ? err.code
        : obj && typeof obj.code === "string"
          ? obj.code
          : "unparseable-response";
    const named = [err?.role, obj?.role].find((r) => typeof r === "string" && r);
    return { ok: false, code, ...(typeof named === "string" ? { role: named } : {}) };
  }
  const result =
    obj && typeof obj.result === "object" && obj.result ? (obj.result as Record<string, unknown>) : obj;
  const value = parseReply(result);
  return value ? { ok: true, value } : { ok: false, code: "unparseable-response" };
}

function parseReply(result: Record<string, unknown> | null): ChatReply | null {
  if (!result || typeof result.reply !== "string") return null;
  return {
    reply: result.reply,
    sources: parseSources(result.sources),
    turnSerial: num(result.turn_serial),
    fallback: typeof result.fallback === "string" && result.fallback ? result.fallback : null,
  };
}

/** The code the daemon answers when the chat route's own clock ran out; the turn runs on. */
export const CHAT_TIMEOUT_CODE = "http-control-timeout";

/** A fresh request id: a token the daemon accepts (8-64 of letters, digits, _ or -). */
export function newRequestId(): string {
  const raw =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `req-${raw.replace(/[^A-Za-z0-9]/g, "")}`.slice(0, 64);
}

export type ChatResultOutcome =
  | { state: "pending" }
  /** Never sent, dropped by the daemon's bound, or lost to a restart. */
  | { state: "unknown" }
  /** The turn finished: its answer, or the turn's own refusal code. */
  | { state: "done"; outcome: ChatOutcome }
  /** This poll failed (network, auth, shape); the request may still be running. */
  | { state: "error"; code: string };

/** GET /api/chat/{id}: an answer that outlived its request. Never throws. */
export async function fetchChatResult(
  requestId: string,
  secret: string,
  options: ChatClientOptions = {},
): Promise<ChatResultOutcome> {
  const fetchFn = options.fetchFn ?? fetch;
  const headers: Record<string, string> = {};
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let body: unknown;
  try {
    const response = await fetchFn(`${options.basePath ?? "/api"}/chat/${encodeURIComponent(requestId)}`, {
      headers,
    });
    if (!response.ok) return { state: "error", code: `http-${response.status}` };
    body = await response.json();
  } catch {
    return { state: "error", code: "network-error" };
  }
  const obj = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
  if (obj?.state === "pending") return { state: "pending" };
  if (obj?.state === "unknown") return { state: "unknown" };
  if (obj?.state !== "done" || !obj.result || typeof obj.result !== "object") {
    return { state: "error", code: "unparseable-response" };
  }
  const result = obj.result as Record<string, unknown>;
  if (result.ok !== true) {
    return { state: "done", outcome: { ok: false, code: typeof result.code === "string" && result.code ? result.code : "text-refused" } };
  }
  const value = parseReply(result);
  return { state: "done", outcome: value ? { ok: true, value } : { ok: false, code: "unparseable-response" } };
}
