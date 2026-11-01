// api/models.ts
//
// Client for GET /api/models -> {roles:[{role, model, ready, host, voice, voice_p90_s}],
// stale, age_s, conversation_role} (gwen-pm t14), and the per-conversation settings a
// turn carries: persona, model (a role name) and language. Never throws: a failure is
// a tagged result with the HTTP status and a fixed code. The install secret rides in
// `Authorization: Bearer` only when held, exactly like api/chat.ts.

export type VoiceState = "unmeasured" | "probing" | "measured";
export type Language = "he" | "en" | "none";

export interface ModelRole {
  role: string;
  model: string;
  ready: boolean;
  host: string;
  voice: VoiceState;
  voiceP90S: number | null;
}

export interface ModelsInfo {
  roles: ModelRole[];
  stale: boolean;
  ageS: number | null;
  conversationRole: string;
  /** The live conversation's persona and what was CHOSEN for it (a reload restores these). */
  live: ConversationSettings;
}

export type ModelsOutcome =
  | { ok: true; info: ModelsInfo }
  | { ok: false; status: number; code: string };

export interface ModelsClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

/** What the operator chose for this conversation; an absent key means "the persona's own". */
export interface ConversationSettings {
  persona?: string;
  model?: string;
  language?: Language;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object";

/** The optional body fields of /api/chat and /api/conversation/new. */
export function settingsBody(s: ConversationSettings): Record<string, string> {
  const out: Record<string, string> = {};
  if (s.persona) out.persona = s.persona;
  if (s.model) out.model = s.model;
  if (s.language) out.language = s.language;
  return out;
}

/** The same choices as the clip's headers (X-Persona, X-Model, X-Language). */
export function settingsHeaders(s: ConversationSettings): Record<string, string> {
  const out: Record<string, string> = {};
  if (s.persona) out["X-Persona"] = s.persona;
  if (s.model) out["X-Model"] = s.model;
  if (s.language) out["X-Language"] = s.language;
  return out;
}

/** What the gateway measured about speaking with this role, and nothing more. */
export function voiceLabel(r: ModelRole): string {
  if (r.voice === "measured" && r.voiceP90S !== null) return `voice p90 ${r.voiceP90S.toFixed(1)} s`;
  if (r.voice === "probing") return "voice speed being measured";
  return "voice speed unmeasured";
}

function parseRoles(raw: unknown[]): ModelRole[] {
  const out: ModelRole[] = [];
  for (const r of raw) {
    if (!isObj(r) || typeof r.role !== "string" || !r.role) continue;
    const voice: VoiceState = r.voice === "measured" || r.voice === "probing" ? r.voice : "unmeasured";
    out.push({
      role: r.role,
      model: str(r.model),
      ready: r.ready === true,
      host: str(r.host),
      voice,
      voiceP90S: typeof r.voice_p90_s === "number" && Number.isFinite(r.voice_p90_s) ? r.voice_p90_s : null,
    });
  }
  return out;
}

const LANGS: readonly string[] = ["he", "en", "none"];

/** `conversation` of GET /api/models -> the settings to restore; a null field stays unset. */
export function parseLiveChoice(raw: unknown): ConversationSettings {
  if (!isObj(raw)) return {};
  const out: ConversationSettings = {};
  if (typeof raw.persona === "string" && raw.persona) out.persona = raw.persona;
  if (typeof raw.model === "string" && raw.model) out.model = raw.model;
  if (typeof raw.language === "string" && LANGS.includes(raw.language)) out.language = raw.language as Language;
  return out;
}

function errorCode(body: unknown): string {
  if (!isObj(body)) return "unparseable-response";
  if (typeof body.code === "string") return body.code;
  if (isObj(body.error) && typeof body.error.code === "string") return body.error.code;
  return "unparseable-response";
}

export async function fetchModels(secret: string, options: ModelsClientOptions = {}): Promise<ModelsOutcome> {
  const fetchFn = options.fetchFn ?? fetch;
  const headers: Record<string, string> = {};
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let response: Response;
  try {
    response = await fetchFn(`${options.basePath ?? "/api"}/models`, { method: "GET", headers });
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
  if (!isObj(body) || !Array.isArray(body.roles)) {
    return { ok: false, status: response.status, code: "unparseable-response" };
  }
  return {
    ok: true,
    info: {
      roles: parseRoles(body.roles),
      stale: body.stale === true,
      ageS: typeof body.age_s === "number" && Number.isFinite(body.age_s) ? body.age_s : null,
      conversationRole: str(body.conversation_role),
      live: parseLiveChoice(body.conversation),
    },
  };
}
