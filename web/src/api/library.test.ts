import { describe, expect, it, vi } from "vitest";
import { fetchItem, fetchLibrary } from "./library";

const ok = (body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: true, status: 200, json: async () => body }) as Response);

describe("api/library", () => {
  it("lists items, sending Bearer only when a secret is held", async () => {
    const fetchFn = ok({ items: [{ id: "a", title: "T", kind: "recording", created_at: "2026-01-02T03:04:05Z", indexed: true }, 7] });
    const r = await fetchLibrary("k", { fetchFn });
    expect(fetchFn.mock.calls[0][0]).toBe("/api/library");
    expect((fetchFn.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBe("Bearer k");
    expect(r.ok && r.value).toEqual([{ id: "a", title: "T", kind: "recording", createdAt: "2026-01-02T03:04:05Z", indexed: true }]);
    await fetchLibrary("", { fetchFn });
    expect(fetchFn.mock.calls[1][1]?.headers as Record<string, string>).not.toHaveProperty("Authorization");
  });

  it("fetches one item with passages; times are numbers or null", async () => {
    const fetchFn = ok({
      item: { id: "a/b", title: "T", kind: "note", author: "me", created_at: "2026-01-02" },
      passages: [{ anchor: "p1", heading: "H", text: "x", start_s: 1, end_s: 2, speaker: "S" }, { anchor: "p2", text: "y" }],
    });
    const r = await fetchItem("a/b", "", { fetchFn });
    expect(fetchFn.mock.calls[0][0]).toBe("/api/library/a%2Fb");
    expect(r.ok && r.value.passages[1]).toEqual({ anchor: "p2", heading: "", text: "y", startS: null, endS: null, speaker: "" });
    expect(r.ok && r.value.item.author).toBe("me");
  });

  it("never throws: network, http and malformed bodies become codes", async () => {
    expect(await fetchLibrary("", { fetchFn: vi.fn(async () => { throw new Error("x"); }) })).toEqual({ ok: false, code: "network-error" });
    const bad = vi.fn(async () => ({ ok: false, status: 404, json: async () => ({ error: { code: "not-found" } }) }) as Response);
    expect(await fetchItem("z", "", { fetchFn: bad })).toEqual({ ok: false, code: "not-found" });
    expect(await fetchLibrary("", { fetchFn: ok({ nope: 1 }) })).toEqual({ ok: false, code: "unparseable-response" });
    expect(await fetchItem("z", "", { fetchFn: ok({ item: 1 }) })).toEqual({ ok: false, code: "unparseable-response" });
  });
});
