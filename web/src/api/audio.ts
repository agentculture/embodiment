// api/audio.ts
//
// Clients for POST /api/speak {text} -> audio/wav and POST /api/clip <raw
// audio> -> {ok, transcript, reply, code} (plan t15). Never throws: every
// failure is a tagged result with a fixed code the caller shows (C3). The
// clip is sent exactly as the browser recorded it (webm/opus or mp4/aac
// differ by browser); transcoding is not done here -- the server's refusal
// (413/415/400/422/502) is surfaced instead. The secret rides as a bearer
// and is omitted when empty (public Host).

export interface AudioClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

export type SpeakOutcome = { ok: true; blob: Blob } | { ok: false; code: string };
export type ClipOutcome =
  | { ok: true; transcript: string; reply: string }
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
    return { ok: true, blob };
  } catch {
    return { ok: false, code: "unparseable-response" };
  }
}

export async function postClip(
  clip: Blob,
  secret: string,
  options: AudioClientOptions = {},
  durationS?: number,
): Promise<ClipOutcome> {
  if (clip.size === 0) return { ok: false, code: "empty-clip", status: 0 };
  const fetchFn = options.fetchFn ?? fetch;
  const headers: Record<string, string> = {};
  if (clip.type) headers["Content-Type"] = clip.type;
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
  return { ok: true, transcript: typeof o.transcript === "string" ? o.transcript : "", reply: o.reply };
}
