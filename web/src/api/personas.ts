// api/personas.ts
//
// Client for the persona routes (gwen-pm t14): GET /api/personas ->
// {personas:[...], builtin_id}; POST /api/personas; PUT /api/personas/{id};
// DELETE /api/personas/{id}. The server composes the prompt; this client only
// carries fields. Never throws: a failure is a tagged result with the HTTP status
// and the server's fixed code (bad_*, unknown_field, not_found, builtin_read_only,
// exists, too_many, save_failed). The secret rides in `Authorization: Bearer`
// only when held.

export interface PersonaFields {
  name: string;
  language: string;
  length: string;
  allow_formatting: boolean;
  /** A role name that translates for the model, or null. */
  translate_via: string | null;
  mesh_enabled: boolean;
  text: string;
  /** The role this persona answers with unless the conversation picks another. */
  default_role: string;
}

export interface Persona extends PersonaFields {
  id: string;
}

export type PersonasOutcome =
  | { ok: true; personas: Persona[]; builtinId: string }
  | { ok: false; status: number; code: string };
export type PersonaSaveOutcome = { ok: true; persona: Persona } | { ok: false; status: number; code: string };
export type PersonaDeleteOutcome = { ok: true } | { ok: false; status: number; code: string };

export interface PersonasClientOptions {
  basePath?: string;
  fetchFn?: typeof fetch;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object";

export function parsePersona(o: unknown): Persona | null {
  if (!isObj(o) || typeof o.id !== "string" || !o.id) return null;
  return {
    id: o.id,
    name: str(o.name),
    language: str(o.language) || "none",
    length: str(o.length) || "unset",
    allow_formatting: o.allow_formatting === true,
    translate_via: typeof o.translate_via === "string" && o.translate_via ? o.translate_via : null,
    mesh_enabled: o.mesh_enabled === true,
    text: str(o.text),
    default_role: str(o.default_role),
  };
}

function errorCode(body: unknown): string {
  if (!isObj(body)) return "unparseable-response";
  if (typeof body.code === "string") return body.code;
  if (isObj(body.error) && typeof body.error.code === "string") return body.error.code;
  return "unparseable-response";
}

async function call(
  method: string,
  path: string,
  secret: string,
  options: PersonasClientOptions,
  payload?: unknown,
): Promise<{ ok: true; body: unknown } | { ok: false; status: number; code: string }> {
  const fetchFn = options.fetchFn ?? fetch;
  const headers: Record<string, string> = {};
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  if (secret) headers.Authorization = `Bearer ${secret}`;
  let response: Response;
  try {
    response = await fetchFn(`${options.basePath ?? "/api"}${path}`, {
      method,
      headers,
      ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}),
    });
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
  return { ok: true, body };
}

export async function listPersonas(secret: string, options: PersonasClientOptions = {}): Promise<PersonasOutcome> {
  const out = await call("GET", "/personas", secret, options);
  if (!out.ok) return out;
  const b = out.body;
  if (!isObj(b) || !Array.isArray(b.personas)) {
    return { ok: false, status: 200, code: "unparseable-response" };
  }
  const personas = b.personas.map(parsePersona).filter((p): p is Persona => p !== null);
  return { ok: true, personas, builtinId: str(b.builtin_id) };
}

/** Create (no id) or update (id) a persona. */
export async function savePersona(
  id: string | undefined,
  fields: PersonaFields,
  secret: string,
  options: PersonasClientOptions = {},
): Promise<PersonaSaveOutcome> {
  const path = id ? `/personas/${encodeURIComponent(id)}` : "/personas";
  const out = await call(id ? "PUT" : "POST", path, secret, options, fields);
  if (!out.ok) return out;
  const persona = parsePersona(out.body);
  return persona ? { ok: true, persona } : { ok: false, status: 200, code: "unparseable-response" };
}

export async function deletePersona(
  id: string,
  secret: string,
  options: PersonasClientOptions = {},
): Promise<PersonaDeleteOutcome> {
  const out = await call("DELETE", `/personas/${encodeURIComponent(id)}`, secret, options);
  return out.ok ? { ok: true } : out;
}
