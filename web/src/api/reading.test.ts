import { describe, expect, it, vi } from "vitest";
import { clearReading, fetchNext, fetchPositions, reportHeard } from "./reading";

const reply = (status: number, body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response);

const unitBody = {
  item_id: "it-1",
  sha256: "s1",
  title: "A synthetic book",
  section: 2,
  section_count: 12,
  heading: "3. A heading",
  paragraph: 2,
  paragraph_count: 13,
  part: 0,
  part_count: 1,
  text: "Synthetic paragraph text.",
  position: "opaque-pos-1",
};

const headers = (f: ReturnType<typeof reply>, n = 0) => f.mock.calls[n][1]?.headers as Record<string, string>;

describe("api/reading: the route contract shared by t3, t4 and t7", () => {
  it("lists saved positions, Bearer only when a secret is held (as api/library.ts)", async () => {
    const f = reply(200, [
      { item_id: "it-1", sha256: "s1", section: 2, paragraph: 2, part: 0, updated_at: "2026-10-26T01:00:00Z", gone: false },
      { item_id: 7, sha256: "s2", section: 0, paragraph: 0, part: 0, updated_at: "", gone: true },
      "junk",
    ]);
    const r = await fetchPositions("k", { fetchFn: f });
    expect(f.mock.calls[0][0]).toBe("/api/reading");
    expect(headers(f).Authorization).toBe("Bearer k");
    expect(r.ok && r.value).toEqual([
      { itemId: "it-1", sha256: "s1", section: 2, paragraph: 2, part: 0, updatedAt: "2026-10-26T01:00:00Z", gone: false },
      { itemId: "7", sha256: "s2", section: 0, paragraph: 0, part: 0, updatedAt: "", gone: true },
    ]);
    await fetchPositions("", { fetchFn: f });
    expect(headers(f, 1)).not.toHaveProperty("Authorization");
  });

  it("accepts the list wrapped in {positions}", async () => {
    const r = await fetchPositions("", { fetchFn: reply(200, { positions: [{ item_id: "a" }] }) });
    expect(r.ok && r.value.map((p) => p.itemId)).toEqual(["a"]);
  });

  it("next: absent section/paragraph continues at the cursor; given, both go in the query", async () => {
    const f = reply(200, unitBody);
    const r = await fetchNext("a/b", "k", { fetchFn: f });
    expect(f.mock.calls[0][0]).toBe("/api/reading/a%2Fb/next");
    expect(headers(f).Authorization).toBe("Bearer k");
    expect(r).toEqual({
      ok: true,
      value: {
        done: false,
        unit: {
          itemId: "it-1", sha256: "s1", title: "A synthetic book", section: 2, sectionCount: 12,
          heading: "3. A heading", paragraph: 2, paragraphCount: 13, part: 0, partCount: 1,
          text: "Synthetic paragraph text.", position: "opaque-pos-1",
        },
      },
    });
    await fetchNext("a", "", { fetchFn: f }, { section: 3, paragraph: 0 });
    expect(f.mock.calls[1][0]).toBe("/api/reading/a/next?section=3&paragraph=0");
  });

  it("next past the end is {done: true}; a unit with no position is unparseable", async () => {
    expect(await fetchNext("a", "", { fetchFn: reply(200, { done: true }) })).toEqual({ ok: true, value: { done: true } });
    expect(await fetchNext("a", "", { fetchFn: reply(200, { ...unitBody, position: "" }) })).toEqual({
      ok: false,
      code: "unparseable-response",
    });
  });

  it("heard POSTs exactly the position and returns moved + cursor", async () => {
    const f = reply(200, { moved: true, cursor: { section: 2, paragraph: 3 } });
    const r = await reportHeard("it-1", "opaque-pos-1", "k", { fetchFn: f });
    expect(f.mock.calls[0][0]).toBe("/api/reading/it-1/heard");
    const init = f.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ position: "opaque-pos-1" });
    expect(headers(f)).toMatchObject({ Authorization: "Bearer k", "Content-Type": "application/json" });
    expect(r).toEqual({ ok: true, value: { moved: true, cursor: { section: 2, paragraph: 3 } } });
    const stale = await reportHeard("it-1", "p", "", { fetchFn: reply(200, { moved: false, cursor: {} }) });
    expect(stale.ok && stale.value.moved).toBe(false);
  });

  it("clear POSTs to /clear", async () => {
    const f = reply(200, { cleared: true });
    expect(await clearReading("it-1", "", { fetchFn: f })).toEqual({ ok: true, value: true });
    expect(f.mock.calls[0][0]).toBe("/api/reading/it-1/clear");
    expect((f.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("never throws: the contract's errors keep their codes, a bare status maps to them", async () => {
    expect(await fetchNext("a", "", { fetchFn: reply(404, { error: { code: "library-item-not-found" } }) })).toEqual({ ok: false, code: "library-item-not-found" });
    expect(await fetchNext("a", "", { fetchFn: reply(410, {}) })).toEqual({ ok: false, code: "library-item-gone" });
    expect(await fetchNext("a", "", { fetchFn: reply(503, null) })).toEqual({ ok: false, code: "library_unavailable" });
    expect(await reportHeard("a", "p", "", { fetchFn: reply(401, {}) })).toEqual({ ok: false, code: "http-401" });
    const boom = vi.fn(async () => { throw new Error("x"); });
    expect(await fetchPositions("", { fetchFn: boom })).toEqual({ ok: false, code: "network-error" });
    expect(await reportHeard("a", "p", "", { fetchFn: boom })).toEqual({ ok: false, code: "network-error" });
    expect(await fetchPositions("", { fetchFn: reply(200, { nope: 1 }) })).toEqual({ ok: false, code: "unparseable-response" });
  });
});
