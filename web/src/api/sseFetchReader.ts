// api/sseFetchReader.ts
//
// The default SSEConnect implementation: a fetch()-streamed SSE reader.
// Round 4 replaces EventSource with this, because of a LIVE finding: the
// operator opened the dashboard over Tailscale (daemon bound off-loopback,
// t15's --http-bind/--allowed-host), and t16's guard
// (embodiment/http/guard.py) refused every request with
// `http-refused-cookie-without-origin`. t16's guard vouches for a cookie
// credential only via an allow-listed Origin OR a `Sec-Fetch-Site:
// same-origin` header. Round 3 established that EventSource always sends
// Origin because its fetch mode is "cors" -- true, but browsers only send
// `Sec-Fetch-*` metadata headers to a SECURE CONTEXT (https:, or
// http://localhost/http://127.0.0.1 specifically) per the Fetch Metadata
// spec, and Origin itself is also WITHHELD by some browsers on a request
// whose target is a non-secure, non-loopback origin for privacy reasons in
// practice observed here -- the net effect: the cookie path only works on
// 127.0.0.1 or behind TLS, and the Tailscale IP is neither. Round 3's
// analysis was right about the *spec text* and wrong about what actually
// reaches the guard off-loopback/off-https.
//
// The fix is in THIS app, not the guard (per the report): stop using the
// cookie path entirely. `Authorization: Bearer <secret>` is a header WE
// set ourselves on a `fetch()` call -- unlike EventSource, `fetch()` can
// set arbitrary headers -- so the secret never has to travel by cookie at
// all, and t16's CSRF/cookie-without-Origin rule is simply never
// consulted for this request. See api/secret.ts's module docstring: the
// cookie write is now gone; this is the ONE credential path.
//
// This module owns everything EventSource used to give us for free:
// framing the byte stream (api/sseFraming.ts), reconnecting after a drop
// with bounded exponential backoff, and sending `Last-Event-ID` on
// reconnect so a server that supports resume (embodiment/bus.py's
// Subscription is not currently replay-capable, but the header costs
// nothing to send and matches the spec's own contract for any SSE server
// that does).

import { SSEFrameParser } from "./sseFraming";
import type { SSEConnect, SSEConnectionHandle } from "../hooks/sseConnection";

export interface FetchSSEOptions {
  /** Test/DI seam: defaults to the global `fetch`. */
  fetchFn?: typeof fetch;
  /** Base reconnect delay, doubled on each consecutive failure. Chosen, not
   *  measured -- mirrors culture-nodes' own SharedEventsManager
   *  (BASE_RECONNECT_DELAY_MS = 1000), the closest prior art in this
   *  workspace for a hand-rolled SSE reconnect loop. */
  baseRetryDelayMs?: number;
  /** Cap on the reconnect delay, however many attempts have failed in a
   *  row. Chosen, not measured -- same prior art (MAX_RECONNECT_DELAY_MS
   *  = 15_000). */
  maxRetryDelayMs?: number;
  /** Test seam: defaults to the global setTimeout/clearTimeout. */
  setTimeoutFn?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  clearTimeoutFn?: (id: ReturnType<typeof setTimeout>) => void;
}

const DEFAULT_BASE_RETRY_DELAY_MS = 1000;
const DEFAULT_MAX_RETRY_DELAY_MS = 15_000;

/**
 * Build an `SSEConnect` backed by `fetch()` + a streamed body read. Every
 * call to the returned function opens ONE logical connection: internally it
 * reconnects on its own after a drop (bounded backoff, resetting to the
 * base delay after a successful open), so from `useEventStream.ts`'s point
 * of view a single `connect()` call behaves like EventSource's own built-in
 * auto-reconnect did -- `onError` fires once per drop, `onOpen` fires again
 * once a retry succeeds, and `close()` is the only way to stop it for good.
 */
export function createFetchSSEConnect(options: FetchSSEOptions = {}): SSEConnect {
  const fetchFn = options.fetchFn ?? fetch;
  const baseDelay = options.baseRetryDelayMs ?? DEFAULT_BASE_RETRY_DELAY_MS;
  const maxDelay = options.maxRetryDelayMs ?? DEFAULT_MAX_RETRY_DELAY_MS;
  const setTimeoutFn = options.setTimeoutFn ?? setTimeout;
  const clearTimeoutFn = options.clearTimeoutFn ?? clearTimeout;

  return (url, headers, callbacks): SSEConnectionHandle => {
    let closed = false;
    let attempt = 0;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;
    let lastEventId: string | undefined;

    const scheduleRetry = () => {
      if (closed) return;
      const delay = Math.min(baseDelay * 2 ** attempt, maxDelay);
      attempt += 1;
      retryTimer = setTimeoutFn(() => {
        retryTimer = null;
        void run();
      }, delay);
    };

    async function run(): Promise<void> {
      if (closed) return;
      controller = new AbortController();
      const requestHeaders: Record<string, string> = {
        ...headers,
        Accept: "text/event-stream",
      };
      if (lastEventId) requestHeaders["Last-Event-ID"] = lastEventId;

      let response: Response;
      try {
        response = await fetchFn(url, { headers: requestHeaders, signal: controller.signal });
      } catch {
        if (closed) return; // an intentional abort throws -- not a real failure
        callbacks.onError();
        scheduleRetry();
        return;
      }
      if (closed) return;

      if (!response.ok || !response.body) {
        callbacks.onError();
        scheduleRetry();
        return;
      }

      attempt = 0; // a successful open resets the backoff
      callbacks.onOpen();

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const parser = new SSEFrameParser();
      try {
        while (!closed) {
          const { value, done } = await reader.read();
          if (done) break;
          const text = decoder.decode(value, { stream: true });
          const events = parser.push(text);
          for (const event of events) {
            if (event.id) lastEventId = event.id;
            callbacks.onFrame({ kind: event.event, data: event.data, id: event.id });
          }
        }
      } catch {
        if (closed) return;
        callbacks.onError();
        scheduleRetry();
        return;
      }
      if (closed) return;
      // The stream ended without us closing it -- the server (or a
      // network intermediary) dropped the connection. Treat exactly like
      // any other error: report and retry.
      callbacks.onError();
      scheduleRetry();
    }

    void run();

    return {
      close(): void {
        if (closed) return;
        closed = true;
        if (retryTimer) {
          clearTimeoutFn(retryTimer);
          retryTimer = null;
        }
        controller?.abort();
      },
    };
  };
}

/** The connector `useEventStream.ts` uses by default. */
export const connectSSE: SSEConnect = createFetchSSEConnect();
