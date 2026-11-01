import { describe, expect, it, vi } from "vitest";
import { fetchModels, parseLiveChoice, settingsBody, voiceLabel } from "./models";

const res = (status: number, body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: status < 400, status, json: async () => body }) as Response);

describe("api/models", () => {
  it("parses roles, staleness and the conversation role; sends the secret", async () => {
    const fetchFn = res(200, {
      roles: [
        { role: "senses", model: "Gemma", ready: true, host: "spark", voice: "measured", voice_p90_s: 1.234 },
        { role: "muse", model: "G31", ready: false, host: "", voice: "unmeasured", voice_p90_s: null },
        { nope: 1 },
      ],
      stale: true,
      age_s: 14,
      conversation_role: "senses",
    });
    const out = await fetchModels("k", { fetchFn });
    expect(out.ok).toBe(true);
    if (!out.ok) return;
    expect(out.info.roles).toHaveLength(2);
    expect(out.info.roles[0]).toMatchObject({ role: "senses", ready: true, voice: "measured", voiceP90S: 1.234 });
    expect(out.info.stale).toBe(true);
    expect(out.info.ageS).toBe(14);
    expect(out.info.conversationRole).toBe("senses");
    const init = fetchFn.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer k");
  });

  it("never throws: a network failure and a bad body are tagged", async () => {
    expect(await fetchModels("", { fetchFn: vi.fn(async () => { throw new Error("x"); }) })).toEqual({
      ok: false, status: 0, code: "network-error",
    });
    const bad = await fetchModels("", { fetchFn: res(200, { roles: "no" }) });
    expect(bad).toMatchObject({ ok: false, code: "unparseable-response" });
    expect(await fetchModels("", { fetchFn: res(503, { error: { code: "x-down" } }) })).toMatchObject({
      ok: false, status: 503, code: "x-down",
    });
  });

  it("voiceLabel states what was measured and nothing more", () => {
    const base = { role: "r", model: "m", ready: true, host: "", voice: "unmeasured" as const, voiceP90S: null };
    expect(voiceLabel(base)).toBe("voice speed unmeasured");
    expect(voiceLabel({ ...base, voice: "probing" })).toBe("voice speed being measured");
    expect(voiceLabel({ ...base, voice: "measured", voiceP90S: 1.234 })).toBe("voice p90 1.2 s");
  });

  it("parseLiveChoice keeps only what was chosen and valid", () => {
    expect(parseLiveChoice(undefined)).toEqual({});
    expect(parseLiveChoice({ persona: "ada", model: null, language: null })).toEqual({ persona: "ada" });
    expect(parseLiveChoice({ persona: "gwen", model: "worker", language: "en" })).toEqual({
      persona: "gwen", model: "worker", language: "en",
    });
    expect(parseLiveChoice({ persona: "", model: 3, language: "fr" })).toEqual({});
  });

  it("settingsBody carries only what was chosen", () => {
    expect(settingsBody({})).toEqual({});
    expect(settingsBody({ persona: "p1", model: "worker", language: "none" })).toEqual({
      persona: "p1", model: "worker", language: "none",
    });
  });
});
