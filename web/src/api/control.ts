// api/control.ts
//
// The control API this dashboard calls (t16 implements the server side;
// this task's integrator notes record the agreed paths so both sides build
// against the same contract without waiting on each other):
//
//   POST /api/voice/start
//   POST /api/voice/stop
//   POST /api/mic/mute
//   GET  /api/status
//
// Every POST carries the install secret as `Authorization: Bearer <secret>`
// (integrator note). This module never reads a secret from disk or an
// environment file itself (that would touch a credential file, forbidden
// by the task-agent preamble) — the caller supplies it, typically read from
// a value the operator pastes into the page once and the page keeps in
// memory/sessionStorage, never logged.
//
// Round 5: `GET /api/status` and its response shape are now load-bearing,
// not merely exported and unused. `embodiment/http/server.py`'s `_status`
// handler answers `{"daemon": outcome.result, "http": app.status()}` where
// `outcome.result` is `embodiment.daemon.app.DaemonApp.status()`'s own
// dict — read directly from `/home/spark/git/.worktrees.embodiment/
// realtime-t15/embodiment/daemon/app.py` (`_status`, read-only reference;
// field names cross-checked against that file's own test assertions in
// `tests/test_daemon_app.py`, e.g. `status()["ear"]["active"]`,
// `status()["recall"]["mode"]`). `DaemonStatusBody` below types exactly
// the fields this dashboard reads from it; every other field the real
// daemon returns (turns, audio, session, bus, ...) is preserved through
// the trailing index signature rather than narrowed, since this dashboard
// has no use for them yet and a stricter type would silently drop them on
// re-serialization if anything here ever did that.
//
// A POST's outcome is now rendered, not discarded: `parseControlOutcome`
// reads the daemon's own response envelope --
// `{"ok": true, "result": {...}}` on 200, `{"error": {"code", "message"}}`
// on a refusal (`embodiment/http/server.py`'s `_invoke`/`_send_error`) --
// and returns a small tagged result the caller renders directly. Only
// `code` is ever surfaced to the page (never `message`, and never the
// secret): the guard's own refusal reasons are fixed per-code strings
// (embodiment/http/guard.py's own "never echo attacker/operator input"
// rule), but this client treats that as an implementation detail it does
// not need to depend on, and only shows the code the operator can act on.

export interface ControlClientOptions {
  /** Base path control requests are issued against; overridable for tests. */
  basePath?: string;
  /** fetch implementation seam for tests. */
  fetchFn?: typeof fetch;
}

/** `status()["ear"]` (embodiment/daemon/app.py's `_status`). `active` is
 *  the ear's NAME ("host", "browser", "null" -- a string, even for the
 *  null test endpoint) or JSON `null` when nothing is attached; testing
 *  `active !== null` is "is a voice loop attached", never string
 *  truthiness (the string `"null"` is itself a valid, attached ear name). */
export interface DaemonEarStatus {
  active: string | null;
  muted: boolean;
  [key: string]: unknown;
}

/** `status()["clients"]` -- byte-identical shape to the bus's own
 *  `clients` event `data` (`{count, remote}`), which is why a status-seeded
 *  value and a live event both slot into the same hook state unmodified. */
export interface DaemonClientsStatus {
  count: number;
  remote: number;
}

/** `status()["recall"]`. `mode` is `None`/`null` before the first recall
 *  call this process has made, then whatever
 *  `embodiment.memory.RoomMemory.recall`'s result reports (observed in
 *  `tests/test_daemon_app.py`: `"lexical"`; `"semantic"` is the other
 *  value the same field can hold, though this rig's embedder is not ready
 *  today -- CLAUDE.md's C3 -- so it is not expected to be observed live
 *  here). `semantic` is a bool: whether the LAST recall actually ran
 *  semantic, independent of what mode was configured. */
export interface DaemonRecallStatus {
  mode: string | null;
  semantic: boolean;
  /** `AppConfig.recall_mode`'s value (default `"keyword"`) -- what recall
   *  is CONFIGURED to do, independent of `mode` (what the last completed
   *  call actually did). Always present, unlike `mode`, which stays `null`
   *  until the first recall call this process makes -- round 6: renders
   *  "recall: <configured_mode> (configured)" in that gap, so the operator
   *  sees what WILL happen rather than a bare "unknown". */
  configured_mode: string;
  [key: string]: unknown;
}

/** `embodiment.daemon.app.DaemonApp.status()`'s return shape -- the
 *  `"daemon"` half of `GET /api/status`'s body. Only the fields this
 *  dashboard reads are narrowed; everything else passes through the index
 *  signature. */
export interface DaemonStatusBody {
  ear: DaemonEarStatus;
  clients: DaemonClientsStatus;
  recall: DaemonRecallStatus;
  [key: string]: unknown;
}

/** `GET /api/status`'s whole response body
 *  (`embodiment/http/server.py`'s `_status`): `daemon` is
 *  `DaemonApp.status()`, `http` is `DashboardServer.status()` (bind,
 *  `streams_open`, counters -- this dashboard does not read `http` today,
 *  kept only as an index-signature passthrough). */
export interface StatusEnvelopeResponse {
  daemon: DaemonStatusBody;
  http: Record<string, unknown>;
}

/** One control POST's rendered outcome. `ok: true` on any 2xx; `ok: false`
 *  otherwise, carrying only `code` (never the response's `message`, and
 *  never the secret) -- see this module's own docstring for why. */
export type ControlOutcome =
  | { ok: true; status: number; result: unknown }
  | { ok: false; status: number; code: string };

const DEFAULT_BASE_PATH = "/api";
/** The code shown when a refusal's body could not be parsed at all (a
 *  malformed response, or a network intermediary that ate the JSON) --
 *  never fabricated as if the server had said something specific. */
const UNPARSEABLE_REFUSAL_CODE = "unparseable-response";

function authHeaders(secret: string): HeadersInit {
  return { Authorization: `Bearer ${secret}`, "Content-Type": "application/json" };
}

async function post(
  path: string,
  secret: string,
  body: unknown,
  options: ControlClientOptions,
): Promise<Response> {
  const fetchFn = options.fetchFn ?? fetch;
  const basePath = options.basePath ?? DEFAULT_BASE_PATH;
  return fetchFn(`${basePath}${path}`, {
    method: "POST",
    headers: authHeaders(secret),
    body: JSON.stringify(body ?? {}),
  });
}

export function startVoice(secret: string, options: ControlClientOptions = {}): Promise<Response> {
  return post("/voice/start", secret, {}, options);
}

export function stopVoice(secret: string, options: ControlClientOptions = {}): Promise<Response> {
  return post("/voice/stop", secret, {}, options);
}

export function setMicMute(
  secret: string,
  muted: boolean,
  options: ControlClientOptions = {},
): Promise<Response> {
  return post("/mic/mute", secret, { muted }, options);
}

export async function fetchStatus(
  secret: string,
  options: ControlClientOptions = {},
): Promise<StatusEnvelopeResponse> {
  const fetchFn = options.fetchFn ?? fetch;
  const basePath = options.basePath ?? DEFAULT_BASE_PATH;
  const response = await fetchFn(`${basePath}/status`, {
    headers: authHeaders(secret),
  });
  if (!response.ok) {
    throw new Error(`GET ${basePath}/status failed: ${response.status}`);
  }
  return (await response.json()) as StatusEnvelopeResponse;
}

/**
 * Read a control POST's `Response` into a rendered outcome. Never throws:
 * a body that is not JSON, or JSON that doesn't match either expected
 * shape, degrades to `{ok: false, code: UNPARSEABLE_REFUSAL_CODE}` rather
 * than propagating a parse exception up into a click handler.
 */
export async function parseControlOutcome(response: Response): Promise<ControlOutcome> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  const obj = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
  if (response.ok) {
    const result = obj && "result" in obj ? obj.result : undefined;
    return { ok: true, status: response.status, result };
  }
  const errorField = obj && typeof obj.error === "object" && obj.error !== null ? obj.error : null;
  const rawCode = errorField ? (errorField as Record<string, unknown>).code : undefined;
  const code = typeof rawCode === "string" ? rawCode : UNPARSEABLE_REFUSAL_CODE;
  return { ok: false, status: response.status, code };
}
