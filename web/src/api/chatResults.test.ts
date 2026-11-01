import { describe, expect, it, vi } from "vitest";
import { CHAT_PENDING_CODE, CHAT_TIMEOUT_CODE, fetchChatResult, newRequestId, sendChat } from "./chat";
import { addPending, loadPending, MAX_PENDING_CHATS, PENDING_CHATS_KEY, PENDING_MAX_AGE_MS, removePending } from "./pendingChats";

const res = (status: number, body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) => ({ ok: status < 400, status, json: async () => body }) as Response);

describe("a chat answer that outlives its request", () => {
  it("names the request in the body only when given an id", async () => {
    const fetchFn = res(200, { reply: "r", sources: [] });
    await sendChat("q", "k", { fetchFn }, undefined, {}, "req-abcdef123456");
    await sendChat("q", "k", { fetchFn });
    const [a, b] = fetchFn.mock.calls.map((c) => JSON.parse((c[1] as RequestInit).body as string));
    expect(a.request_id).toBe("req-abcdef123456");
    expect("request_id" in b).toBe(false);
  });

  it("makes ids the daemon accepts", () => {
    const ids = new Set(Array.from({ length: 20 }, newRequestId));
    expect(ids.size).toBe(20);
    for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
  });

  it("surfaces the route's timeout code so the page can wait instead", async () => {
    const out = await sendChat("q", "", { fetchFn: res(503, { error: { code: CHAT_TIMEOUT_CODE } }) });
    expect(out).toEqual({ ok: false, code: CHAT_TIMEOUT_CODE });
    const handed = await sendChat("q", "", { fetchFn: res(202, { ok: true, state: "pending", request_id: "r" }) });
    expect(handed).toEqual({ ok: false, code: CHAT_PENDING_CODE });
  });

  it("reads pending, unknown, a finished answer and a finished refusal", async () => {
    const get = (body: unknown) => fetchChatResult("req-abcdef123456", "k", { fetchFn: res(200, body) });
    expect(await get({ state: "pending", result: null })).toEqual({ state: "pending" });
    expect(await get({ state: "unknown", result: null })).toEqual({ state: "unknown" });
    expect(await get({ state: "done", result: { ok: true, reply: "late", sources: [], turn_serial: 3, fallback: null } })).toEqual({
      state: "done",
      outcome: { ok: true, value: { reply: "late", sources: [], turnSerial: 3, fallback: null } },
    });
    expect(await get({ state: "done", result: { ok: false, code: "app-text-model-unavailable" } })).toEqual({
      state: "done",
      outcome: { ok: false, code: "app-text-model-unavailable" },
    });
    expect(await get({ nope: 1 })).toEqual({ state: "error", code: "unparseable-response" });
  });

  it("GETs the id's own path with the secret, and never throws", async () => {
    const fetchFn = res(200, { state: "pending", result: null });
    await fetchChatResult("req-abcdef123456", "k", { fetchFn });
    expect(fetchFn.mock.calls[0][0]).toBe("/api/chat/req-abcdef123456");
    expect(((fetchFn.mock.calls[0][1] as RequestInit).headers as Record<string, string>).Authorization).toBe("Bearer k");
    const boom = vi.fn(async () => { throw new Error("x"); });
    expect(await fetchChatResult("req-abcdef123456", "", { fetchFn: boom })).toEqual({ state: "error", code: "network-error" });
    expect(await fetchChatResult("req-abcdef123456", "", { fetchFn: res(401, {}) })).toEqual({ state: "error", code: "http-401" });
  });
});

describe("the pending store", () => {
  it("keeps, removes, bounds and ages questions; never the secret", () => {
    localStorage.clear();
    addPending({ id: "req-a00000000", question: "q1", conversation: null, at: Date.now() });
    addPending({ id: "req-b00000000", question: "q2", conversation: "c1", at: Date.now() });
    expect(loadPending().map((p) => p.id)).toEqual(["req-a00000000", "req-b00000000"]);
    removePending("req-a00000000");
    expect(loadPending().map((p) => p.id)).toEqual(["req-b00000000"]);
    expect(loadPending(Date.now() + PENDING_MAX_AGE_MS + 1)).toEqual([]);
    for (let i = 0; i < MAX_PENDING_CHATS + 5; i++) addPending({ id: `req-${i}0000000`, question: "q", conversation: null, at: Date.now() });
    expect(loadPending()).toHaveLength(MAX_PENDING_CHATS);
    expect(Object.keys(JSON.parse(localStorage.getItem(PENDING_CHATS_KEY) ?? "[]")[0]).sort()).toEqual(["at", "conversation", "id", "question"]);
  });

  it("an unreadable store is empty, not an error", () => {
    localStorage.setItem(PENDING_CHATS_KEY, "not json");
    expect(loadPending()).toEqual([]);
  });
});
