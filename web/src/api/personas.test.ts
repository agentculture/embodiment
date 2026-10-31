import { describe, expect, it, vi } from "vitest";
import { deletePersona, listPersonas, savePersona, type PersonaFields } from "./personas";

const res = (status: number, body: unknown) =>
  vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: status < 400, status, json: async () => body }) as Response);

const fields: PersonaFields = {
  name: "Explainer", language: "he", length: "detailed", allow_formatting: true,
  translate_via: "senses", mesh_enabled: false, text: "שלום", default_role: "worker",
};

describe("api/personas", () => {
  it("lists personas and the built-in id", async () => {
    const fetchFn = res(200, { personas: [{ id: "gwen", ...fields, name: "Gwen" }, 5], builtin_id: "gwen" });
    const out = await listPersonas("k", { fetchFn });
    expect(out.ok && out.personas).toHaveLength(1);
    expect(out.ok && out.builtinId).toBe("gwen");
    expect(out.ok && out.personas[0].translate_via).toBe("senses");
  });

  it("creates with POST and updates with PUT, JSON body, bearer secret", async () => {
    const fetchFn = res(200, { id: "p1", ...fields });
    await savePersona(undefined, fields, "k", { fetchFn });
    await savePersona("p 1", fields, "k", { fetchFn });
    const [a, b] = fetchFn.mock.calls;
    expect(a[0]).toBe("/api/personas");
    expect((a[1] as RequestInit).method).toBe("POST");
    expect(b[0]).toBe("/api/personas/p%201");
    expect((b[1] as RequestInit).method).toBe("PUT");
    expect(JSON.parse((a[1] as RequestInit).body as string)).toEqual(fields);
    expect(((a[1] as RequestInit).headers as Record<string, string>).Authorization).toBe("Bearer k");
  });

  it("returns the server's refusal code, never throws", async () => {
    expect(await savePersona("gwen", fields, "", { fetchFn: res(409, { error: { code: "builtin_read_only" } }) })).toEqual({
      ok: false, status: 409, code: "builtin_read_only",
    });
    expect(await deletePersona("x", "", { fetchFn: vi.fn(async () => { throw new Error("n"); }) })).toMatchObject({
      ok: false, code: "network-error",
    });
    expect(await deletePersona("x", "", { fetchFn: res(200, { deleted: "x" }) })).toEqual({ ok: true });
  });

  it("reads the saved persona from the daemon's {persona: ...} reply (the shape test_http_personas pins)", async () => {
    const out = await savePersona(undefined, fields, "k", { fetchFn: res(200, { persona: { id: "p1", ...fields } }) });
    expect(out).toMatchObject({ ok: true, persona: { id: "p1", name: "Explainer" } });
    const upd = await savePersona("p1", fields, "k", { fetchFn: res(200, { persona: { id: "p1", ...fields, name: "E2" } }) });
    expect(upd.ok && upd.persona.name).toBe("E2");
  });
});
