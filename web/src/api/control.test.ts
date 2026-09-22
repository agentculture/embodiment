import { describe, expect, it, vi } from "vitest";
import { fetchStatus, setMicMute, startVoice, stopVoice } from "./control";

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

  it("GETs /api/status with the Authorization header and returns parsed JSON", async () => {
    const fetchFn = fakeFetch(200, { voice: "on" });
    const result = await fetchStatus("s3cr3t", { fetchFn });
    expect(result).toEqual({ voice: "on" });
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
