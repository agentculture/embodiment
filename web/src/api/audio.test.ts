import { describe, expect, it, vi } from "vitest";
import { postClip, speakMessage } from "./audio";

const resp = (status: number, body: unknown, extra: Partial<Response> = {}) =>
  ({ ok: status < 400, status, json: async () => body, blob: async () => new Blob(["RIFFx"], { type: "audio/wav" }), ...extra }) as Response;

describe("speakMessage", () => {
  it("posts {text} and returns the wav blob; secret as bearer, omitted when empty", async () => {
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) => resp(200, null));
    const out = await speakMessage("שלום", "k", { fetchFn });
    expect(out.ok).toBe(true);
    expect(fetchFn.mock.calls[0][0]).toBe("/api/speak");
    expect(JSON.parse(String(fetchFn.mock.calls[0][1]?.body))).toEqual({ text: "שלום" });
    expect((fetchFn.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBe("Bearer k");
    await speakMessage("x", "", { fetchFn });
    expect((fetchFn.mock.calls[1][1]?.headers as Record<string, string>).Authorization).toBeUndefined();
  });
  it("returns a fixed code on refusal, network failure, or empty audio", async () => {
    expect(await speakMessage("x", "", { fetchFn: async () => resp(502, { error: { code: "speak-tts-failed" } }) })).toEqual({ ok: false, code: "speak-tts-failed" });
    expect(await speakMessage("x", "", { fetchFn: async () => resp(502, undefined, { json: async () => { throw new Error("no"); } }) })).toEqual({ ok: false, code: "http-502" });
    expect(await speakMessage("x", "", { fetchFn: async () => { throw new Error("down"); } })).toEqual({ ok: false, code: "network-error" });
    expect(await speakMessage("x", "", { fetchFn: async () => resp(200, null, { blob: async () => new Blob([]) }) })).toEqual({ ok: false, code: "empty-audio" });
  });
});

describe("postClip", () => {
  it("sends the raw body with its recorded Content-Type and declared duration, returns transcript+reply", async () => {
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) => resp(200, { ok: true, transcript: "t", reply: "r", code: "" }));
    const blob = new Blob(["abc"], { type: "audio/webm;codecs=opus" });
    const out = await postClip(blob, "k", { fetchFn }, 3.5);
    expect(out).toEqual({ ok: true, transcript: "t", reply: "r", turnSerial: null, sources: [] });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/clip");
    expect(init?.body).toBe(blob);
    const h = init?.headers as Record<string, string>;
    expect(h["Content-Type"]).toBe("audio/webm;codecs=opus");
    expect(h["X-Clip-Duration-S"]).toBe("3.5");
  });
  it("omits duration and secret when absent", async () => {
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) => resp(200, { ok: true, transcript: "t", reply: "r", turnSerial: null }));
    await postClip(new Blob(["a"], { type: "audio/ogg" }), "", { fetchFn });
    const h = fetchFn.mock.calls[0][1]?.headers as Record<string, string>;
    expect(h["X-Clip-Duration-S"]).toBeUndefined();
    expect(h.Authorization).toBeUndefined();
  });
  it("surfaces the server's refusal code (413/415/400/422/502), top-level or nested", async () => {
    for (const status of [413, 415, 400, 422, 502]) {
      const out = await postClip(new Blob(["a"], { type: "audio/ogg" }), "", { fetchFn: async () => resp(status, { ok: false, code: `c${status}` }) });
      expect(out).toEqual({ ok: false, code: `c${status}`, status });
    }
    expect(await postClip(new Blob(["a"]), "", { fetchFn: async () => resp(415, { error: { code: "nested" } }) })).toEqual({ ok: false, code: "nested", status: 415 });
    expect(await postClip(new Blob(["a"]), "", { fetchFn: async () => resp(415, null, { json: async () => { throw new Error("x"); } }) })).toEqual({ ok: false, code: "http-415", status: 415 });
  });
  it("rejects an empty blob locally and maps network errors", async () => {
    const fetchFn = vi.fn();
    expect(await postClip(new Blob([]), "", { fetchFn: fetchFn as unknown as typeof fetch })).toEqual({ ok: false, code: "empty-clip", status: 0 });
    expect(fetchFn).not.toHaveBeenCalled();
    expect(await postClip(new Blob(["a"]), "", { fetchFn: async () => { throw new Error("d"); } })).toEqual({ ok: false, code: "network-error", status: 0 });
  });
  it("an ok body without a reply string is unparseable", async () => {
    expect(await postClip(new Blob(["a"]), "", { fetchFn: async () => resp(200, { ok: true }) })).toEqual({ ok: false, code: "unparseable-response", status: 200 });
  });
});

describe("postClip sources", () => {
  it("parses the clip answer's cited sources like chat's", async () => {
    const src = { source: "library", item_id: 9, anchor: "a#1", title: "T", date: "2026-10-20", text: "q" };
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
      new Response(JSON.stringify({ ok: true, transcript: "t", reply: "r [1]", turn_serial: 2, sources: [src] }), { status: 200 }));
    const out = await postClip(new Blob(["a"], { type: "audio/wav" }), "", { fetchFn });
    expect(out.ok && out.sources.length).toBe(1);
    expect(out.ok && out.sources[0].item_id).toBe("9");
  });
});

describe("postClip continuing a conversation (deviation d5)", () => {
  it("names the conversation in X-Conversation only when given one", async () => {
    const seen: Record<string, string>[] = [];
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
      seen.push({ ...(init?.headers as Record<string, string>) });
      return { ok: true, status: 200, json: async () => ({ transcript: "t", reply: "r" }) } as Response;
    });
    const clip = new Blob(["abc"], { type: "audio/ogg" });
    await postClip(clip, "", { fetchFn });
    await postClip(clip, "", { fetchFn }, 2, "conv42");
    expect(seen[0]["X-Conversation"]).toBeUndefined();
    expect(seen[1]["X-Conversation"]).toBe("conv42");
  });
});

describe("postClip origin (deviation d7)", () => {
  it("says how the page got the audio only when told", async () => {
    const seen: Record<string, string>[] = [];
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
      seen.push({ ...(init?.headers as Record<string, string>) });
      return { ok: true, status: 200, json: async () => ({ transcript: "t", reply: "r" }) } as Response;
    });
    const clip = new Blob(["abc"], { type: "audio/ogg" });
    await postClip(clip, "", { fetchFn });
    await postClip(clip, "", { fetchFn }, 2, undefined, "recorded");
    expect(seen[0]["X-Clip-Origin"]).toBeUndefined();
    expect(seen[1]["X-Clip-Origin"]).toBe("recorded");
  });
});
