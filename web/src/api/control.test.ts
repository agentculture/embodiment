import { describe, expect, it, vi } from "vitest";
import { fetchStatus, parseControlOutcome, setMicMute, startVoice, stopVoice } from "./control";

function fakeFetch(status = 200, body: unknown = {}) {
  return vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => {
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as Response;
  });
}

describe("control API client", () => {
  it("POSTs /api/voice/start with the Authorization: Bearer header", async () => {
    const fetchFn = fakeFetch();
    await startVoice("s3cr3t", { fetchFn });
    expect(fetchFn).toHaveBeenCalledTimes(1);
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/voice/start");
    expect(init?.method).toBe("POST");
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer s3cr3t");
  });

  it("POSTs /api/voice/stop with the Authorization header", async () => {
    const fetchFn = fakeFetch();
    await stopVoice("s3cr3t", { fetchFn });
    const [url] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/voice/stop");
  });

  it("POSTs /api/mic/mute with the muted flag and the Authorization header", async () => {
    const fetchFn = fakeFetch();
    await setMicMute("s3cr3t", true, { fetchFn });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/mic/mute");
    expect(JSON.parse(init?.body as string)).toEqual({ muted: true });
  });

  it("GETs /api/status with the Authorization header and returns the daemon/http envelope", async () => {
    // The real shape embodiment/http/server.py's _status handler sends:
    // {"daemon": outcome.result, "http": app.status()} -- outcome.result
    // being embodiment/daemon/app.py's own DaemonApp.status().
    const body = {
      daemon: { ear: { active: "host", muted: false }, clients: { count: 1, remote: 0 }, recall: { mode: "lexical", semantic: false } },
      http: { streams_open: 1 },
    };
    const fetchFn = fakeFetch(200, body);
    const result = await fetchStatus("s3cr3t", { fetchFn });
    expect(result).toEqual(body);
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/status");
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer s3cr3t");
  });

  it("never sends the secret anywhere but the Authorization header", async () => {
    const fetchFn = fakeFetch();
    await startVoice("s3cr3t", { fetchFn });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).not.toContain("s3cr3t");
    expect(init?.body ?? "").not.toContain("s3cr3t");
  });

  it("rejects on a non-2xx status from GET /api/status", async () => {
    const fetchFn = fakeFetch(500);
    await expect(fetchStatus("s3cr3t", { fetchFn })).rejects.toThrow();
  });
});

// Round 5: the daemon's real response envelopes -- {"ok": true, "result": ...}
// on 200 (embodiment/http/server.py's _invoke), {"error": {"code", "message"}}
// on a refusal (guard.py's decision, or _send_error) -- read directly, not
// assumed.
describe("parseControlOutcome", () => {
  it("parses a 200 {ok, result} body", async () => {
    const response = { ok: true, status: 200, json: async () => ({ ok: true, result: { ear: "host" } }) } as unknown as Response;
    const outcome = await parseControlOutcome(response);
    expect(outcome).toEqual({ ok: true, status: 200, result: { ear: "host" } });
  });

  it("parses a 401 {error: {code, message}} body, surfacing ONLY the code", async () => {
    const response = {
      ok: false,
      status: 401,
      json: async () => ({ error: { code: "http-refused-secret", message: "no valid install secret was presented" } }),
    } as unknown as Response;
    const outcome = await parseControlOutcome(response);
    expect(outcome).toEqual({ ok: false, status: 401, code: "http-refused-secret" });
    expect(JSON.stringify(outcome)).not.toContain("no valid install secret");
  });

  it("parses a 503 control-busy refusal", async () => {
    const response = {
      ok: false,
      status: 503,
      json: async () => ({ error: { code: "control-timeout", message: "the control call timed out" } }),
    } as unknown as Response;
    const outcome = await parseControlOutcome(response);
    expect(outcome).toEqual({ ok: false, status: 503, code: "control-timeout" });
  });

  it("degrades to an 'unparseable-response' code, never throwing, on a body that isn't JSON", async () => {
    const response = {
      ok: false,
      status: 500,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    } as unknown as Response;
    await expect(parseControlOutcome(response)).resolves.toEqual({
      ok: false,
      status: 500,
      code: "unparseable-response",
    });
  });

  it("degrades to 'unparseable-response' when the error body has no code field", async () => {
    const response = { ok: false, status: 400, json: async () => ({ error: {} }) } as unknown as Response;
    const outcome = await parseControlOutcome(response);
    expect(outcome).toEqual({ ok: false, status: 400, code: "unparseable-response" });
  });

  it("never throws even on a wildly malformed 200 body", async () => {
    const response = { ok: true, status: 200, json: async () => "just a string" } as unknown as Response;
    await expect(parseControlOutcome(response)).resolves.toEqual({
      ok: true,
      status: 200,
      result: undefined,
    });
  });
});
