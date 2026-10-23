import { describe, expect, it, vi } from "vitest";
import { fetchTodos } from "./todos";

const res = (status: number, body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: status < 400, status, json: async () => body }) as Response);

describe("fetchTodos", () => {
  it("GETs /api/todos with Bearer only when a secret is held, and parses groups", async () => {
    const fetchFn = res(200, {
      groups: [{ recording_id: "r1", title: "Sync", date: "2026-01-02", items: [{ text: "call Dana", created: "2026-01-02" }, { bad: 1 }] }, "junk"],
    });
    const out = await fetchTodos("k", { fetchFn });
    expect(fetchFn.mock.calls[0][0]).toBe("/api/todos");
    expect((fetchFn.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBe("Bearer k");
    expect(out).toEqual({
      ok: true,
      groups: [{ recordingId: "r1", title: "Sync", date: "2026-01-02", items: [{ text: "call Dana", created: "2026-01-02" }] }],
    });
    await fetchTodos("", { fetchFn });
    expect(fetchFn.mock.calls[1][1]?.headers).not.toHaveProperty("Authorization");
  });

  it("a 503 returns its fixed code; unparseable and network failures are tagged", async () => {
    expect(await fetchTodos("", { fetchFn: res(503, { code: "library-unavailable" }) })).toEqual({ ok: false, status: 503, code: "library-unavailable" });
    expect(await fetchTodos("", { fetchFn: res(200, { nope: 1 }) })).toEqual({ ok: false, status: 200, code: "unparseable-response" });
    const boom = vi.fn(async () => { throw new Error("x"); });
    expect(await fetchTodos("", { fetchFn: boom as unknown as typeof fetch })).toEqual({ ok: false, status: 0, code: "network-error" });
  });
});
