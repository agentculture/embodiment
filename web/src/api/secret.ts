// api/secret.ts
//
// Where the install secret this dashboard holds gets persisted (never how
// it authenticates a request — that's api/control.ts's Authorization
// header for the control API, and useEventStream.ts's Authorization
// header for the stream, wired through api/sseFetchReader.ts).
//
// Round 4 removed the cookie path entirely. Round 3 added
// `document.cookie = "embodiment_secret=...; Path=/; SameSite=Strict"`
// because EventSource cannot set a request header, so t16's guard
// (embodiment/http/guard.py) accepted the secret as a cookie for the
// stream specifically. Round 3's analysis of the guard's own CSRF rule
// (REFUSED_COOKIE_WITHOUT_ORIGIN_CODE) was correct as far as it went — a
// same-origin EventSource's request mode is "cors", so it DOES send an
// Origin header per the Fetch spec. What round 3 missed: the operator
// opening the dashboard over Tailscale (daemon bound off-loopback, t15's
// --http-bind/--allowed-host) hit `http-refused-cookie-without-origin`
// anyway, live, in Chrome. The guard's OWN rule for vouching for a cookie
// is Origin-allow-listed OR `Sec-Fetch-Site: same-origin`, and browsers
// only attach `Sec-Fetch-*` metadata headers to a SECURE CONTEXT (https:,
// or specifically http://localhost / http://127.0.0.1) — never a plain
// http:// Tailscale IP, which is neither loopback nor TLS. So the cookie
// path only ever worked on 127.0.0.1 or behind TLS, exactly the two hosts
// this dashboard is least likely to be opened from operator-side.
//
// The fix is in THIS app, not the guard: `Authorization: Bearer <secret>`
// is a header we set ourselves on every request — the control API's POSTs
// already used it, and api/sseFetchReader.ts's fetch-based stream reader
// (round 4) now does too. That header needs no Origin, no Sec-Fetch-Site,
// no secure context: it is simply present or it is not. One credential
// path, for every request this dashboard makes. This module therefore only
// persists the secret string across a tab session — it never touches
// `document.cookie`.

/** sessionStorage key the install secret is remembered under. Deliberately
 *  sessionStorage, never localStorage (the secret does not outlive the
 *  tab/window). */
export const INSTALL_SECRET_SESSION_KEY = "embodiment.installSecret";

/** Persist the secret in sessionStorage ONLY -- never localStorage.
 *  Wrapped in try/catch: private browsing, a full quota, or storage
 *  disabled by policy must degrade silently, never crash the page. */
export function saveInstallSecretToSession(secret: string): void {
  try {
    sessionStorage.setItem(INSTALL_SECRET_SESSION_KEY, secret);
  } catch {
    // private mode / quota / disabled storage -- never throw
  }
}

/** The empty string when nothing was saved, storage is unavailable, or
 *  reading it throws. Never throws itself. */
export function loadInstallSecretFromSession(): string {
  try {
    return sessionStorage.getItem(INSTALL_SECRET_SESSION_KEY) ?? "";
  } catch {
    return "";
  }
}
