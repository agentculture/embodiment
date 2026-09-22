// api/secret.ts
//
// The install-secret cookie t16's guard accepts for GET /api/events (round 3
// fix). embodiment/http/guard.py (task t16, commit d147f6c) requires the
// install secret as EITHER `Authorization: Bearer <secret>` OR the
// `embodiment_secret` cookie, because `EventSource` cannot set a request
// header — the cookie path exists FOR this dashboard's stream connection.
//
// The guard's own CSRF rule (`REFUSED_COOKIE_WITHOUT_ORIGIN_CODE` in
// guard.py): a cookie credential arriving with NO Origin header at all is
// refused outright. This is satisfied without any special handling here:
// per the Fetch standard, `EventSource` always issues its request in "cors"
// mode (https://html.spec.whatwg.org/multipage/server-sent-events.html#the-
// eventsource-interface, step 5 of "Fetch the following resource..." sets
// `mode` to "cors"), and the Fetch spec's "append a request-or-response
// header" / Origin-header algorithm adds `Origin` whenever a request's mode
// is "cors" -- NOT only for cross-origin requests, unlike a plain same-origin
// `fetch()` GET. So a same-origin `new EventSource("/api/events")` DOES send
// an Origin header even though it never crosses an origin boundary, and the
// guard's cookie-without-Origin refusal is never hit by this dashboard. (If
// that assumption is ever wrong on a real browser/version, the guard would
// refuse every cookie-authenticated stream connection outright -- see this
// task's final report for the flag to the t16 owner if that is ever
// observed in practice.)

/** embodiment/http/guard.py's `SECRET_COOKIE_NAME`. Duplicated here, in ONE
 *  place, rather than fetched from the server, since the dashboard has no
 *  other reason to talk to the Python side before it can authenticate. */
export const INSTALL_SECRET_COOKIE_NAME = "embodiment_secret";

/** sessionStorage key the install secret is remembered under. Deliberately
 *  sessionStorage, never localStorage (brief's own instruction) -- the
 *  secret does not outlive the tab. */
export const INSTALL_SECRET_SESSION_KEY = "embodiment.installSecret";

export type CookieWriter = (cookieString: string) => void;

function defaultCookieWriter(cookieString: string): void {
  document.cookie = cookieString;
}

export interface InstallSecretCookieOptions {
  /** Test seam: overrides `location.protocol` ("http:" vs "https:") to
   *  decide whether the `Secure` attribute is added. */
  protocol?: string;
  /** Test/DI seam: defaults to `(s) => { document.cookie = s; }`. */
  cookieWriter?: CookieWriter;
}

/**
 * Build the exact `Set-Cookie`-shaped string this dashboard assigns to
 * `document.cookie` when the operator enters the install secret:
 * `embodiment_secret=<secret>; Path=/; SameSite=Strict`, with `; Secure`
 * appended only when the page itself is loaded over https: (never on an
 * http loopback dev server, where `Secure` would make the browser refuse to
 * ever send the cookie at all).
 */
export function buildInstallSecretCookie(
  secret: string,
  options: InstallSecretCookieOptions = {},
): string {
  const protocol =
    options.protocol ?? (typeof location !== "undefined" ? location.protocol : "http:");
  const secureSuffix = protocol === "https:" ? "; Secure" : "";
  return `${INSTALL_SECRET_COOKIE_NAME}=${secret}; Path=/; SameSite=Strict${secureSuffix}`;
}

/**
 * Set the install-secret cookie. Never throws (lesson 3): a sandboxed
 * iframe, cookies disabled, or any other browser-API surprise is swallowed
 * rather than crashing the dashboard -- the guard will simply keep refusing
 * the stream, which the connection-status pill already surfaces.
 */
export function setInstallSecretCookie(
  secret: string,
  options: InstallSecretCookieOptions = {},
): void {
  const cookieWriter = options.cookieWriter ?? defaultCookieWriter;
  const cookieString = buildInstallSecretCookie(secret, options);
  try {
    cookieWriter(cookieString);
  } catch {
    // never throw on a browser API surprise
  }
}

/** Persist the secret in sessionStorage ONLY -- never localStorage (the
 *  brief's own instruction: the secret must not outlive the tab/window).
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
