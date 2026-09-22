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

export interface ControlClientOptions {
  /** Base path control requests are issued against; overridable for tests. */
  basePath?: string;
  /** fetch implementation seam for tests. */
  fetchFn?: typeof fetch;
}

export interface StatusResponse {
  [key: string]: unknown;
}

const DEFAULT_BASE_PATH = "/api";

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
): Promise<StatusResponse> {
  const fetchFn = options.fetchFn ?? fetch;
  const basePath = options.basePath ?? DEFAULT_BASE_PATH;
  const response = await fetchFn(`${basePath}/status`, {
    headers: authHeaders(secret),
  });
  if (!response.ok) {
    throw new Error(`GET ${basePath}/status failed: ${response.status}`);
  }
  return (await response.json()) as StatusResponse;
}
