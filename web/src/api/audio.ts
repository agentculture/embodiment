// api/audio.ts
//
// Clients for POST /api/speak {text} -> audio/wav and POST /api/clip <raw
// audio> -> {ok, transcript, reply, code} (plan t15). Never throws: every
// failure is a tagged result with a fixed code the caller shows (C3). The
// clip is sent exactly as the browser recorded it (webm/opus or mp4/aac
// differ by browser); transcoding is not done here -- the server's refusal
// (413/415/400/422/502) is surfaced instead. The secret rides as a bearer
// and is omitted when empty (public Host).

import { parseSources, type ChatSource } from "./chat";
import { settingsHeaders, type ConversationSettings } from "./models";

export interface AudioClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

/** `truncated`: the daemon stopped short of the text (X-Speak-Truncated, a cap or a deadline). */
export type SpeakOutcome = { ok: true; blob: Blob; truncated: boolean } | { ok: false; code: string };
export type ClipOutcome =
  | { ok: true; transcript: string; reply: string; turnSerial: number | null; sources: ChatSource[] }
  | { ok: false; code: string; status: number };

function authHeaders(secret: string, base: Record<string, string>): Record<string, string> {
  return secret ? { ...base, Authorization: `Bearer ${secret}` } : base;
}

async function refusalCode(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      const o = body as Record<string, unknown>;
      const err = o.error && typeof o.error === "object" ? (o.error as Record<string, unknown>) : null;
      if (err && typeof err.code === "string" && err.code) return err.code;
      if (typeof o.code === "string" && o.code) return o.code;
    }
  } catch {
    /* fall through to the status-derived code */
  }
  return `http-${response.status}`;
}

export async function speakMessage(
  text: string,
  secret: string,
  options: AudioClientOptions = {},
): Promise<SpeakOutcome> {
  const fetchFn = options.fetchFn ?? fetch;
  let response: Response;
  try {
    response = await fetchFn(`${options.basePath ?? "/api"}/speak`, {
      method: "POST",
      headers: authHeaders(secret, { "Content-Type": "application/json" }),
      body: JSON.stringify({ text }),
    });
  } catch {
    return { ok: false, code: "network-error" };
  }
  if (!response.ok) return { ok: false, code: await refusalCode(response) };
  try {
    const blob = await response.blob();
    if (blob.size === 0) return { ok: false, code: "empty-audio" };
    return { ok: true, blob, truncated: response.headers?.get?.("X-Speak-Truncated") === "1" };
  } catch {
    return { ok: false, code: "unparseable-response" };
  }
}

/** Recorded on the page's mic, or an uploaded file (d7). */
export type ClipOrigin = "recorded" | "uploaded";

export async function postClip(
  clip: Blob,
  secret: string,
  options: AudioClientOptions = {},
  durationS?: number,
  conversation?: string,
  origin?: ClipOrigin,
  clientAtMs?: number,
  settings: ConversationSettings = {},
): Promise<ClipOutcome> {
  if (clip.size === 0) return { ok: false, code: "empty-clip", status: 0 };
  const fetchFn = options.fetchFn ?? fetch;
  const headers: Record<string, string> = { ...settingsHeaders(settings) };
  if (clip.type) headers["Content-Type"] = clip.type;
  // Continuing an earlier conversation (deviation d5): the daemon answers in it.
  if (conversation) headers["X-Conversation"] = conversation;
  // How the page got the audio (deviation d7): the session log keeps it, and the
  // export tells a reader a recording from an uploaded file.
  if (origin) headers["X-Clip-Origin"] = origin;
  // When it was recorded, or the uploaded file's own date (d12): the browser's
  // clock, so the daemon stamps it as the client's claim.
  if (clientAtMs !== undefined && Number.isFinite(clientAtMs) && clientAtMs > 0) {
    headers["X-Clip-At"] = String(Math.round(clientAtMs));
  }
  if (durationS !== undefined && Number.isFinite(durationS) && durationS >= 0) {
    headers["X-Clip-Duration-S"] = String(durationS);
  }
  let response: Response;
  try {
    response = await fetchFn(`${options.basePath ?? "/api"}/clip`, {
      method: "POST",
      headers: authHeaders(secret, headers),
      body: clip,
    });
  } catch {
    return { ok: false, code: "network-error", status: 0 };
  }
  if (!response.ok) return { ok: false, code: await refusalCode(response), status: response.status };
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const o = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
  if (!o || typeof o.reply !== "string") {
    return { ok: false, code: "unparseable-response", status: response.status };
  }
  return {
    ok: true,
    transcript: typeof o.transcript === "string" ? o.transcript : "",
    reply: o.reply,
    turnSerial: typeof o.turn_serial === "number" ? o.turn_serial : null,
    sources: parseSources(o.sources),
  };
}
