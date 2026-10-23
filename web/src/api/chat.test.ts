import { describe, expect, it, vi } from "vitest";
import { formatMmSs, sendChat } from "./chat";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

function fakeFetch(status: number, body: unknown) {
  return vi.fn(async (_u: RequestInfo | URL, _i?: RequestInit) =>
    ({ ok: status < 300, status, json: async () => body }) as Response,
  );
}

describe("chat client", () => {
  it("POSTs /api/chat with message and Bearer secret, parses reply and sources", async () => {
    const fetchFn = fakeFetch(200, {
      reply: "hi",
      sources: [{ source: "recording", title: "Demo", date: "2026-01-02", start: 65, end: 70, section: "", link: "", text: "x" }],
      turn_serial: 4,
    });
    const out = await sendChat("hello", "s3", { fetchFn });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe("/api/chat");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer s3");
    expect(JSON.parse(init?.body as string)).toEqual({ message: "hello" });
    expect(out.ok && out.value.sources[0].title).toBe("Demo");
    expect(out.ok && out.value.turnSerial).toBe(4);
  });
  it("omits Authorization with no secret", async () => {
    const fetchFn = fakeFetch(200, { reply: "r", sources: [] });
    await sendChat("m", "", { fetchFn });
    expect((fetchFn.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBeUndefined();
  });
  it("degrades without throwing", async () => {
    expect(await sendChat("m", "", { fetchFn: (async () => { throw new Error("x"); }) as typeof fetch })).toEqual({ ok: false, code: "network-error" });
    expect(await sendChat("m", "", { fetchFn: fakeFetch(401, { error: { code: "refused", message: "secret" } }) })).toEqual({ ok: false, code: "refused" });
    expect(await sendChat("m", "", { fetchFn: fakeFetch(200, { nope: 1 }) })).toEqual({ ok: false, code: "unparseable-response" });
  });
  it("formats mm:ss", () => {
    expect(formatMmSs(65)).toBe("01:05");
  });
  it("no HTML-injection sinks anywhere under web/src", () => {
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
          // comments may name the sinks (to forbid them); code may not
          const t = readFileSync(p, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
          for (const sink of ["innerHTML", "dangerouslySetInnerHTML", "insertAdjacentHTML"]) {
            if (t.includes(sink)) hits.push(`${p}:${sink}`);
          }
        }
      }
    };
    walk(join(__dirname, ".."));
    expect(hits).toEqual([]);
  });
});
